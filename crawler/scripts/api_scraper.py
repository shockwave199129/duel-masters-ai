"""
api_scraper.py — Level 3 (API): Fetch card wikitext via MediaWiki API and parse {{Cardtable}}.

Replaces scraper.scrape_card() when --use-api is enabled.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import psycopg2
from curl_cffi import requests

from scripts.api_client import api_get, make_api_session, title_to_url, url_to_title
from scripts.king_cell import collect_civilizations_from_params
from scripts.card_validation import is_valid_raw_card
from scripts.scraper import (
    RawCard,
    _detect_keywords,
    _extract_abilities_from_text,
    _finalize_raw_card,
    _parse_civilizations,
    _split_card_type,
    parse_card_page,
    save_card_to_db,
)

logger = logging.getLogger(__name__)

CARDTABLE_NAMES = ("Cardtable", "Cardtable2")
BATCH_SIZE = 50


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _slug_from_title(title: str) -> str:
    return title.replace(" ", "_")


def _extract_cost(text: str) -> Optional[int]:
    match = re.search(r"(\d+)", str(text))
    return int(match.group(1)) if match else None


def _split_list_field(text: str) -> list[str]:
    if not text:
        return []
    parts = re.split(r"[,/]", text)
    return [_clean(p) for p in parts if _clean(p)]


def _strip_nested_templates(text: str) -> str:
    """Remove {{...}} template calls, keeping surrounding text."""
    result: list[str] = []
    i = 0
    while i < len(text):
        if text.startswith("{{", i):
            depth = 0
            j = i + 2
            while j < len(text) - 1:
                if text[j : j + 2] == "{{":
                    depth += 1
                    j += 2
                elif text[j : j + 2] == "}}":
                    if depth == 0:
                        i = j + 2
                        break
                    depth -= 1
                    j += 2
                else:
                    j += 1
            else:
                result.append(text[i])
                i += 1
        else:
            result.append(text[i])
            i += 1
    return _clean("".join(result))


def _extract_template_blocks(wikitext: str) -> list[str]:
    blocks: list[str] = []
    lower = wikitext.lower()
    i = 0
    while i < len(wikitext):
        matched = False
        for name in CARDTABLE_NAMES:
            needle = "{{" + name
            if lower.startswith(needle.lower(), i):
                depth = 0
                j = i + 2
                while j < len(wikitext) - 1:
                    if wikitext[j : j + 2] == "{{":
                        depth += 1
                        j += 2
                    elif wikitext[j : j + 2] == "}}":
                        if depth == 0:
                            blocks.append(wikitext[i : j + 2])
                            i = j + 2
                            matched = True
                            break
                        depth -= 1
                        j += 2
                    else:
                        j += 1
                if matched:
                    break
        if not matched:
            i += 1
    return blocks


def _split_template_params(block: str) -> dict[str, str]:
    """Parse key = value pairs from a {{Cardtable|...}} block."""
    inner = block[2:-2]  # strip {{ }}
    # Remove template name up to first |
    pipe = inner.find("|")
    if pipe == -1:
        return {}
    inner = inner[pipe + 1 :]

    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in inner:
        if ch == "{":
            depth += 1
            current.append(ch)
        elif ch == "}":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "|" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))

    params: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        params[_clean(key).lower()] = value.strip()
    return params


def _face_from_params(params: dict[str, str]) -> dict[str, Any]:
    raw_type = params.get("type", params.get("card type", "Unknown"))
    card_type, card_subtype = _split_card_type(raw_type)
    civs = collect_civilizations_from_params(params)
    if not civs:
        civ_text = params.get("civilization", params.get("civilizations", ""))
        civs = _parse_civilizations(civ_text) or _split_list_field(civ_text)
    race_text = params.get("race", params.get("races", ""))
    engtext = params.get("engtext", params.get("english text", params.get("eng text", "")))
    engtext_plain = _strip_nested_templates(engtext)
    abilities = _extract_abilities_from_text(engtext_plain)
    if not abilities and engtext_plain:
        abilities = [f"■ {engtext_plain}"] if not engtext_plain.startswith("■") else [engtext_plain]

    return {
        "name": _clean(params.get("name", params.get("jpname", ""))) or None,
        "card_type": card_type,
        "card_subtype": card_subtype,
        "cost": _extract_cost(params.get("cost", "") or params.get("mana", "")),
        "power": params.get("power", "") or None,
        "civilizations": civs,
        "races": _split_list_field(race_text),
        "abilities": abilities,
        "flavor_text": params.get("flavor", params.get("flavor1", "")) or None,
        "fields": {k: _strip_nested_templates(v) for k, v in params.items()},
    }


def parse_cardtable_wikitext(
    wikitext: str,
    title: str,
    set_code: str,
    source_url: str,
) -> Optional[RawCard]:
    """Parse one or more {{Cardtable}} blocks from wikitext into a RawCard."""
    blocks = _extract_template_blocks(wikitext)
    if not blocks:
        return None

    faces = [_face_from_params(_split_template_params(block)) for block in blocks]
    primary = faces[0]
    name = _clean(title.split(" / ")[0]) if " / " in title else _clean(title)

    # Twinpact-style combined title: "Creature / ♪ Spell"
    if len(faces) == 1 and " / " in title:
        face_names = [part.strip() for part in title.split(" / ") if part.strip()]
        if len(face_names) > 1:
            faces = [dict(primary, name=face_names[0])]
            for extra_name in face_names[1:]:
                faces.append(dict(primary, name=extra_name))

    card_type = primary["card_type"]
    card_subtype = primary["card_subtype"]
    cost = primary["cost"]
    power = primary["power"]
    civilizations = sorted({c for face in faces for c in face["civilizations"]})
    races = sorted({r for face in faces for r in face["races"]})
    abilities = [ab for face in faces for ab in face["abilities"]]
    flavor = primary.get("flavor_text")

    fields = primary.get("fields", {})
    printings = [
        {
            "set_code": set_code,
            "collector_num": fields.get("number", fields.get("collector", "")),
            "rarity": fields.get("rarity", ""),
            "mana_number": fields.get("mana number", fields.get("mana_number", "")),
            "image_url": None,
        }
    ]

    raw_text = json.dumps(
        {
            "name": name,
            "source_url": source_url,
            "fields": fields,
            "faces": faces,
            "abilities": abilities,
        },
        ensure_ascii=False,
    )

    return _finalize_raw_card(RawCard(
        slug=_slug_from_title(title),
        name=name,
        cost=cost,
        power=power,
        card_type=card_type,
        card_subtype=card_subtype,
        civilizations=civilizations,
        races=races,
        abilities=abilities,
        flavor_text=flavor,
        rulings=[],
        printings=printings,
        keywords_found=_detect_keywords(abilities),
        faces=faces,
        is_multiface=len(faces) > 1,
        source_url=source_url,
        raw_text=raw_text,
    ), wikitext)


def fetch_card_wikitexts(
    session: requests.Session,
    titles: list[str],
) -> dict[str, str]:
    """Batch-fetch wikitext for up to 50 titles per API call."""
    results: dict[str, str] = {}
    for i in range(0, len(titles), BATCH_SIZE):
        batch = titles[i : i + BATCH_SIZE]
        data = api_get(
            session,
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "titles": "|".join(batch),
            },
        )
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            if page.get("missing"):
                continue
            title = page.get("title", "")
            revs = page.get("revisions", [])
            if not revs:
                continue
            content = revs[0].get("slots", {}).get("main", {}).get("*", "")
            if content:
                results[title] = content
    return results


def fetch_parsed_html(session: requests.Session, title: str) -> Optional[str]:
    """Fetch rendered HTML via action=parse (fallback when wikitext has no Cardtable)."""
    try:
        data = api_get(
            session,
            {
                "action": "parse",
                "page": title,
                "prop": "text",
            },
        )
        return data.get("parse", {}).get("text", {}).get("*")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("parse API failed for %s: %s", title, exc)
        return None


def scrape_card_api(
    title: str,
    set_code: str,
    dsn: str,
    session: Optional[requests.Session] = None,
    source_url: Optional[str] = None,
) -> Optional[RawCard]:
    """Fetch wikitext via API, parse, and persist one card."""
    if session is None:
        session = make_api_session()

    url = source_url or title_to_url(title)
    wikitexts = fetch_card_wikitexts(session, [title])
    wikitext = wikitexts.get(title, "")

    card = None
    if wikitext:
        card = parse_cardtable_wikitext(wikitext, title, set_code, url)
        if card is None and "{{cardtable" not in wikitext.lower():
            logger.info("No Cardtable on page, skipping non-card: %s", title)
            return None

    if card is None and not wikitext:
        html = fetch_parsed_html(session, title)
        if html:
            card = parse_card_page(html, set_code, url)

    if card is None:
        logger.warning("Could not parse card via API: %s", title)
        return None

    if not is_valid_raw_card(card):
        logger.warning("Parsed page is not a valid card, skipping: %s", title)
        return None

    conn = psycopg2.connect(dsn)
    try:
        card_id = save_card_to_db(card, conn)
        if card_id:
            logger.debug("Saved card %s (id=%s) via API", card.name, card_id)
            return card
        return None
    finally:
        conn.close()


def scrape_cards_api(
    items: list[dict],
    dsn: str,
    session: Optional[requests.Session] = None,
) -> list[RawCard]:
    """
    Batch-scrape cards via API.

    Each item: {title?, url?, set_code}
    """
    if session is None:
        session = make_api_session()

    saved: list[RawCard] = []
    for item in items:
        title = item.get("title") or url_to_title(item["url"])
        set_code = item.get("set_code", "UNKNOWN")
        url = item.get("url") or title_to_url(title)
        card = scrape_card_api(title, set_code, dsn, session=session, source_url=url)
        if card:
            saved.append(card)
    return saved


def scrape_card_url_api(
    url: str,
    set_code: str,
    dsn: str,
    session: Optional[requests.Session] = None,
) -> Optional[RawCard]:
    """Convenience wrapper matching scraper.scrape_card(url=..., set_code=...)."""
    title = url_to_title(url)
    return scrape_card_api(title, set_code, dsn, session=session, source_url=url)
