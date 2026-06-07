"""
scraper.py — Level 3: Scrape individual card wiki pages into RawCard dataclasses
and persist them to PostgreSQL.

Handles both page formats:
  - Portable infobox  (newer pages): <aside class="portable-infobox">
  - Legacy wikitable  (older pages): <table class="wikitable">

Deduplication:
  Cards with the same name/slug may appear in multiple sets (reprints).
  We INSERT OR DO NOTHING on the `cards` table and always add a `card_printings`
  row for the current set, so reprints are recorded without duplicating the card.

Entry point:
    scrape_card(url, set_code, dsn, session) -> Optional[RawCard]
"""

from __future__ import annotations
import json
import logging
import re
import time
import random
from dataclasses import dataclass, field
from typing import Optional

import psycopg2
import psycopg2.extras
from bs4 import BeautifulSoup, Tag
from curl_cffi import requests
from curl_cffi.requests.errors import RequestsError

from scripts.cf_cookies import apply_cf_cookies, fetch_html_with_browser

logger = logging.getLogger(__name__)

BASE_URL = "https://duelmasters.fandom.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Keyword list for auto-tagging
KNOWN_KEYWORDS = {
    "blocker", "double breaker", "triple breaker", "world breaker",
    "shield trigger", "speed attacker", "slayer", "power attacker",
    "ninja strike", "revolution change", "evolution", "madness",
    "sympathy", "veil", "mach fighter", "siegfried", "metamorph",
    "gravity zero", "super eternal", "saver", "hunting", "camping",
    "fortress", "guardman", "labyrinth", "invasion", "draghearts",
    "gacharange summon", "revolution 0", "d2 field", "orega aura",
    "neo evolution", "super evolution", "psychic",
}


@dataclass
class RawCard:
    slug: str                       # URL slug (unique, used as dedup key)
    name: str
    cost: Optional[int]
    power: Optional[str]
    card_type: str                  # "Creature", "Spell", "Cross Gear", ...
    card_subtype: Optional[str]     # "Evolution", "Neo Evolution", ...
    civilizations: list[str]
    races: list[str]
    abilities: list[str]            # raw ■ bullet texts
    flavor_text: Optional[str]
    rulings: list[str]
    printings: list[dict]           # [{set_code, collector_num, rarity, mana_number, image_url}]
    keywords_found: list[str]       # auto-detected keywords
    faces: list[dict] = field(default_factory=list)
    is_multiface: bool = False
    source_url: str = ""
    raw_text: str = ""              # full parsed text dump for debugging


# ── Fetch ─────────────────────────────────────────────────────────────────────

def _polite_delay(min_s: float = 1.5, max_s: float = 3.0):
    time.sleep(random.uniform(min_s, max_s))


def _fetch(url: str, session: requests.Session, retries: int = 3) -> Optional[str]:
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 403:
                logger.warning("HTTP 403 for %s; using Playwright fallback", url)
                break
            elif resp.status_code == 429:
                wait = 30 * (attempt + 1)
                logger.warning(f"Rate limited, waiting {wait}s")
                time.sleep(wait)
            else:
                logger.warning(f"HTTP {resp.status_code} for {url}")
                break
        except RequestsError as e:
            logger.warning(f"Request error ({attempt+1}/{retries}) for {url}: {e}")
            time.sleep(5 * (attempt + 1))
    try:
        return fetch_html_with_browser(url)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Playwright fallback failed for %s: %s", url, e)
        return None


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_cost(text: str) -> Optional[int]:
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else None


def _extract_power(text: str) -> Optional[str]:
    text = _clean(text)
    if not text or text.lower() in ("—", "-", "n/a", ""):
        return None
    return text


def _split_card_type(raw_type: str) -> tuple[str, Optional[str]]:
    """
    "Evolution Creature" → ("Creature", "Evolution")
    "Neo Evolution Creature" → ("Creature", "Neo Evolution")
    "Creature" → ("Creature", None)
    """
    raw_type = _clean(raw_type)
    SUBTYPES = [
        "Neo Evolution", "Super Neo Evolution", "Neo Neo Evolution",
        "Neo Neo Neo Evolution", "Evolution", "Psychic",
        "Dragheart Creature", "Dragheart Weapon", "Gacharange Creature",
        "Jokers Creature",
    ]
    for sub in SUBTYPES:
        if raw_type.startswith(sub + " "):
            remainder = raw_type[len(sub):].strip()
            return (remainder or raw_type, sub)
        if raw_type == sub:
            return (raw_type, None)
    return (raw_type, None)


def _normalize_table_key(text: str) -> str:
    """Normalize legacy wikitable labels like 'Card type:' to 'card type'."""
    return _clean(text).lower().rstrip(":")


def _parse_civilizations(text: str) -> list[str]:
    CIVS = ["Fire", "Water", "Nature", "Light", "Darkness", "Zero", "Jokers"]
    return [c for c in CIVS if c.lower() in text.lower()]


def _extract_abilities(soup_section: Tag) -> list[str]:
    """
    Find all ■ bullet ability lines.
    On infobox pages they are in <p> or <li> tags containing '■'.
    On wikitable pages they are in <td> cells.
    """
    abilities = []
    for tag in soup_section.find_all(["p", "li", "td"]):
        text = _clean(tag.get_text())
        if "■" in text:
            # Split on ■ in case multiple abilities are on one line
            parts = text.split("■")
            for part in parts:
                part = _clean(part)
                if len(part) > 5:
                    abilities.append("■ " + part)
    return abilities


def _extract_abilities_from_text(text: str) -> list[str]:
    abilities = []
    for part in str(text).split("■"):
        part = _clean(part)
        if len(part) > 5:
            abilities.append("■ " + part)
    return abilities


def _detect_keywords(abilities: list[str]) -> list[str]:
    found = []
    combined = " ".join(abilities).lower()
    for kw in KNOWN_KEYWORDS:
        if kw.lower() in combined:
            found.append(kw)
    return found


def _extract_image_url(soup: BeautifulSoup) -> Optional[str]:
    img = soup.find("img", class_="pi-image-thumbnail")
    if img:
        return img.get("src") or img.get("data-src")
    # Fallback: first image in infobox
    aside = soup.find("aside")
    if aside:
        img = aside.find("img")
        if img:
            return img.get("src") or img.get("data-src")
    return None


# ── Portable infobox parser ────────────────────────────────────────────────────

def _parse_infobox(soup: BeautifulSoup, set_code: str, source_url: str) -> Optional[RawCard]:
    aside = soup.find("aside", class_=re.compile(r"portable-infobox|pi-theme"))
    if not aside:
        return None

    def get_field(label: str) -> str:
        """Find a field by its data-source or label text."""
        # Try data-source attribute first
        el = aside.find(attrs={"data-source": re.compile(label, re.IGNORECASE)})
        if el:
            val = el.find("div", class_="pi-data-value")
            if val:
                return _clean(val.get_text())
        # Try label text match
        for label_el in aside.find_all("h3", class_="pi-data-label"):
            if label.lower() in label_el.get_text().lower():
                val_el = label_el.find_next_sibling("div", class_="pi-data-value")
                if val_el:
                    return _clean(val_el.get_text())
        return ""

    name_tag = aside.find("h2", class_="pi-title") or aside.find("h1", class_="pi-title")
    name = _clean(name_tag.get_text()) if name_tag else ""
    if not name:
        return None

    slug = source_url.rstrip("/").split("/wiki/")[-1]

    raw_type = get_field("type") or get_field("cardtype") or get_field("card type")
    card_type, card_subtype = _split_card_type(raw_type or "Unknown")

    cost_str = get_field("cost") or get_field("mana cost") or ""
    cost = _extract_cost(cost_str)

    power_str = get_field("power") or ""
    power = _extract_power(power_str)

    civ_str = get_field("civilization") or get_field("civs") or ""
    civilizations = _parse_civilizations(civ_str)

    race_str = get_field("race") or get_field("races") or ""
    races = [_clean(r) for r in re.split(r"[/\n,]", race_str) if _clean(r)]

    english_text = get_field("english text") or get_field("effect") or ""

    # Abilities: prefer the English effect field to avoid Japanese/duplicate text.
    content = soup.find("div", class_="mw-content-text") or soup
    abilities = _extract_abilities_from_text(english_text) if english_text else _extract_abilities(content)

    # Flavor text: italic paragraph in content
    flavor = None
    for em in content.find_all(["em", "i"]):
        t = _clean(em.get_text())
        if len(t) > 20:
            flavor = t
            break

    # Rulings: look for "Rulings" section
    rulings = []
    for h in content.find_all(["h2", "h3"]):
        if "ruling" in h.get_text().lower():
            for li in h.find_next_siblings():
                if li.name in ("h2", "h3"):
                    break
                for item in li.find_all("li") if li.name in ("ul", "ol") else [li]:
                    t = _clean(item.get_text())
                    if t and len(t) > 10:
                        rulings.append(t)

    # Printings: look for a set list table or just use provided set_code
    image_url = _extract_image_url(soup)
    collector_num = get_field("number") or get_field("collector") or ""
    rarity = get_field("rarity") or ""
    mana_number = get_field("mana number") or ""

    printings = [{
        "set_code": set_code,
        "collector_num": collector_num,
        "rarity": rarity,
        "mana_number": mana_number,
        "image_url": image_url,
    }]

    keywords_found = _detect_keywords(abilities)
    is_multiface = bool(aside.find("section", class_="pi-group") and
                        len(aside.find_all("section", class_="pi-group")) > 1)

    raw_text = json.dumps(
        {
            "name": name,
            "source_url": source_url,
            "fields": {
                "card type": raw_type,
                "mana cost": cost_str,
                "power": power_str,
                "civilization": civ_str,
                "race": race_str,
                "english text": english_text,
                "collector": collector_num,
                "rarity": rarity,
                "mana number": mana_number,
            },
            "abilities": abilities,
        },
        ensure_ascii=False,
    )

    return RawCard(
        slug=slug,
        name=name,
        cost=cost,
        power=power,
        card_type=card_type,
        card_subtype=card_subtype,
        civilizations=civilizations,
        races=races,
        abilities=abilities,
        flavor_text=flavor,
        rulings=rulings,
        printings=printings,
        keywords_found=keywords_found,
        faces=[],
        is_multiface=is_multiface,
        source_url=source_url,
        raw_text=raw_text,
    )


# ── Legacy wikitable parser ────────────────────────────────────────────────────

def _parse_wikitable_faces(table: Tag) -> list[dict]:
    """Split a legacy wikitable into one or more card faces."""
    faces: list[dict] = []
    current: Optional[dict] = None

    for row in table.find_all("tr"):
        cols = row.find_all(["th", "td"], recursive=False)
        if len(cols) >= 2:
            if current is None:
                current = {"name": "", "fields": {}}
                faces.append(current)
            key = _normalize_table_key(cols[0].get_text())
            val = _clean(cols[1].get_text())
            current["fields"][key] = val
            continue

        title = _clean(row.get_text())
        if title:
            current = {"name": title, "fields": {}}
            faces.append(current)

    return [face for face in faces if face.get("fields")]


def _face_to_raw(face: dict) -> dict:
    fields = face.get("fields", {})
    raw_type = fields.get("card type", fields.get("type", "Unknown"))
    card_type, card_subtype = _split_card_type(raw_type)
    return {
        "name": face.get("name") or "",
        "fields": fields,
        "cost": _extract_cost(fields.get("cost", fields.get("mana cost", ""))),
        "power": _extract_power(fields.get("power", "")),
        "card_type": card_type,
        "card_subtype": card_subtype,
        "civilizations": _parse_civilizations(fields.get("civilization", fields.get("civs", ""))),
        "races": [
            _clean(r)
            for r in re.split(r"[/\n,]", fields.get("race", fields.get("races", "")))
            if _clean(r)
        ],
        "abilities": _extract_abilities_from_text(fields.get("english text", "")),
    }


def _parse_wikitable(soup: BeautifulSoup, set_code: str, source_url: str) -> Optional[RawCard]:
    """Parse the older wikitable format."""
    table = soup.find("table", class_=re.compile(r"wikitable"))
    if not table:
        return None

    slug = source_url.rstrip("/").split("/wiki/")[-1]

    # Extract name from page title
    title_tag = soup.find("h1", id="firstHeading") or soup.find("h1", class_="page-header__title")
    name = _clean(title_tag.get_text()) if title_tag else slug.replace("_", " ")

    face_rows = [_face_to_raw(face) for face in _parse_wikitable_faces(table)]
    if not face_rows:
        return None

    primary = next((face for face in face_rows if face["power"]), face_rows[0])
    data = primary["fields"]

    card_type = primary["card_type"]
    card_subtype = primary["card_subtype"]

    cost = primary["cost"]
    power = primary["power"]
    civilizations = sorted({c for face in face_rows for c in face["civilizations"]})
    races = sorted({r for face in face_rows for r in face["races"]})

    content = soup.find("div", class_="mw-content-text") or soup
    abilities = [ability for face in face_rows for ability in face["abilities"]]
    if not abilities:
        abilities = _extract_abilities(content)

    flavor = None
    for em in content.find_all(["em", "i"]):
        t = _clean(em.get_text())
        if len(t) > 20:
            flavor = t
            break

    rulings = []
    image_url = _extract_image_url(soup)
    printings = [{
        "set_code": set_code,
        "collector_num": data.get("number", data.get("collector", "")),
        "rarity": data.get("rarity", ""),
        "mana_number": data.get("mana number", ""),
        "image_url": image_url,
    }]

    keywords_found = _detect_keywords(abilities)
    raw_text = json.dumps(
        {
            "name": name,
            "source_url": source_url,
            "fields": data,
            "faces": face_rows,
            "abilities": abilities,
        },
        ensure_ascii=False,
    )

    return RawCard(
        slug=slug,
        name=name,
        cost=cost,
        power=power,
        card_type=card_type,
        card_subtype=card_subtype,
        civilizations=civilizations,
        races=races,
        abilities=abilities,
        flavor_text=flavor,
        rulings=rulings,
        printings=printings,
        keywords_found=keywords_found,
        faces=face_rows,
        is_multiface=len(face_rows) > 1,
        source_url=source_url,
        raw_text=raw_text,
    )


# ── Parse dispatcher ───────────────────────────────────────────────────────────

def parse_card_page(html: str, set_code: str, source_url: str) -> Optional[RawCard]:
    soup = BeautifulSoup(html, "html.parser")
    card = _parse_infobox(soup, set_code, source_url)
    if card is None:
        card = _parse_wikitable(soup, set_code, source_url)
    return card


# ── DB persistence ─────────────────────────────────────────────────────────────

def save_card_to_db(card: RawCard, conn) -> Optional[int]:
    """
    Upsert card into PostgreSQL. Returns card_id.
    Reprints: cards table gets INSERT ON CONFLICT DO NOTHING,
              card_printings always gets an INSERT for the new set.
    """
    try:
        with conn.cursor() as cur:
            # Upsert card
            cur.execute(
                """
                INSERT INTO cards
                    (slug, name, cost, power, card_type, card_subtype,
                     flavor_text, is_multiface, faces, raw_text, source_url, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (slug) DO UPDATE SET
                    name        = EXCLUDED.name,
                    cost        = EXCLUDED.cost,
                    power       = EXCLUDED.power,
                    card_type   = EXCLUDED.card_type,
                    card_subtype= EXCLUDED.card_subtype,
                    flavor_text = EXCLUDED.flavor_text,
                    is_multiface= EXCLUDED.is_multiface,
                    faces       = EXCLUDED.faces,
                    raw_text    = EXCLUDED.raw_text,
                    updated_at  = NOW()
                RETURNING id
                """,
                (
                    card.slug, card.name, card.cost, card.power,
                    card.card_type, card.card_subtype, card.flavor_text,
                    card.is_multiface,
                    json.dumps(card.faces, ensure_ascii=False) if card.faces else None,
                    card.raw_text, card.source_url,
                ),
            )
            card_id = cur.fetchone()[0]

            # Civilizations
            for civ in card.civilizations:
                cur.execute(
                    "INSERT INTO card_civilizations (card_id, civilization) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (card_id, civ),
                )

            # Races
            for race in card.races:
                cur.execute(
                    "INSERT INTO card_races (card_id, race) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (card_id, race),
                )

            # Printings
            for p in card.printings:
                cur.execute(
                    """
                    INSERT INTO card_printings (card_id, set_code, collector_num, rarity, mana_number, image_url)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (card_id, set_code, collector_num) DO NOTHING
                    """,
                    (
                        card_id,
                        p["set_code"],
                        p["collector_num"],
                        p["rarity"],
                        p.get("mana_number", ""),
                        p["image_url"],
                    ),
                )

            # Rulings
            for ruling in card.rulings:
                cur.execute(
                    "INSERT INTO card_rulings (card_id, ruling_text) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (card_id, ruling),
                )

            # Keywords
            for kw in card.keywords_found:
                cur.execute(
                    "INSERT INTO card_keywords (card_id, keyword) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (card_id, kw),
                )

        conn.commit()
        return card_id

    except Exception as e:
        conn.rollback()
        logger.error(f"DB save failed for {card.slug}: {e}")
        return None


# ── Main entry point ───────────────────────────────────────────────────────────

def scrape_card(
    url: str,
    set_code: str,
    dsn: str,
    session: Optional[requests.Session] = None,
) -> Optional[RawCard]:
    """
    Fetch, parse, and persist a single card page.

    Returns the RawCard on success, None on failure.
    """
    if session is None:
        session = requests.Session(impersonate="chrome124")
        apply_cf_cookies(session)

    html = _fetch(url, session)
    if not html:
        logger.error(f"Failed to fetch card: {url}")
        return None

    card = parse_card_page(html, set_code, url)
    if not card:
        logger.warning(f"Could not parse card page: {url}")
        return None

    conn = psycopg2.connect(dsn)
    try:
        card_id = save_card_to_db(card, conn)
        if card_id:
            logger.debug(f"Saved card {card.name} (id={card_id}) from {url}")
            return card
        return None
    finally:
        conn.close()
