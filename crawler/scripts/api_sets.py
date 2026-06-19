"""
api_sets.py — Level 1 (API): Discover Duel Masters set pages via MediaWiki API.

Replaces sets_crawler.crawl_sets_list() when --use-api is enabled.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from curl_cffi import requests

from scripts.api_client import (
    BASE_URL,
    api_get,
    api_paginate,
    make_api_session,
    title_to_url,
)
from scripts.sets_crawler import SET_CODE_RE, _extract_set_code_from_href

logger = logging.getLogger(__name__)

# Categories that list OCG set pages on the Fandom wiki
OCG_SET_CATEGORIES = (
    "Booster_Packs",
    "Starter_Decks",
    "Deck_Exclusives",
    "Promotional_Cards",
    "Theme_Decks",
    "Special_Packs",
    "Duel_Masters_OCG_Sets",
)

TCG_SET_CATEGORIES = (
    "Booster_Packs",
    "Starter_Decks",
)

MODERN_PREFIXES = tuple(f"DM{year}-" for year in range(22, 31))

SKIP_TITLE_FRAGMENTS = (
    "Gallery",
    "gallery",
    "disambiguation",
    "List of",
    "Category:",
    "Booster Packs",
    "Starter Decks",
)


def _title_to_set_code(title: str) -> Optional[str]:
    """Extract canonical set code from a wiki page title."""
    slug = title.replace(" ", "_").split("_")[0]
    href = f"/wiki/{slug}"
    return _extract_set_code_from_href(href)


def _is_set_page_title(title: str) -> bool:
    if any(frag in title for frag in SKIP_TITLE_FRAGMENTS):
        return False
    code = _title_to_set_code(title)
    if not code:
        return False
    # Exclude pure card pages that happen to start with letters
    if not SET_CODE_RE.match(code):
        return False
    return True


def _set_name_from_title(title: str, set_code: str) -> str:
    name = re.sub(rf"^{re.escape(set_code)}\s*[:\-—]?\s*", "", title, flags=re.IGNORECASE).strip()
    return name or title


def _merge_set(existing: dict, candidate: dict) -> dict:
    """
    Prefer the longer descriptive title as the canonical set page.

    e.g. keep "DM22-BD1 Legend Super Deck: ..." over bare "DM22-BD1".
    """
    if len(candidate.get("set_name", "")) > len(existing.get("set_name", "")):
        return candidate
    if len(candidate["page_title"]) > len(existing.get("page_title", "")):
        return {**existing, **candidate}
    return existing


def _discover_from_categories(
    session: requests.Session,
    categories: tuple[str, ...],
    series: str,
) -> dict[str, dict]:
    found: dict[str, dict] = {}

    for category in categories:
        logger.info("API category scan: Category:%s", category)
        members = api_paginate(
            session,
            {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": f"Category:{category}",
                "cmlimit": "500",
                "cmtype": "page",
            },
            list_key="categorymembers",
        )
        for member in members:
            title = member.get("title", "")
            if not _is_set_page_title(title):
                continue
            set_code = _title_to_set_code(title)
            if not set_code:
                continue
            entry = {
                "set_code": set_code,
                "set_name": _set_name_from_title(title, set_code),
                "set_url": title_to_url(title),
                "page_title": title,
                "series": series,
            }
            if set_code in found:
                found[set_code] = _merge_set(found[set_code], entry)
            else:
                found[set_code] = entry

    return found


def _discover_modern_prefixes(session: requests.Session, series: str) -> dict[str, dict]:
    found: dict[str, dict] = {}

    for prefix in MODERN_PREFIXES:
        logger.info("API allpages scan: prefix=%s", prefix)
        pages = api_paginate(
            session,
            {
                "action": "query",
                "list": "allpages",
                "apprefix": prefix,
                "aplimit": "500",
                "apnamespace": "0",
            },
            list_key="allpages",
        )
        for page in pages:
            title = page.get("title", "")
            if not _is_set_page_title(title):
                continue
            set_code = _title_to_set_code(title)
            if not set_code:
                continue
            entry = {
                "set_code": set_code,
                "set_name": _set_name_from_title(title, set_code),
                "set_url": title_to_url(title),
                "page_title": title,
                "series": series,
            }
            if set_code in found:
                found[set_code] = _merge_set(found[set_code], entry)
            else:
                found[set_code] = entry

    return found


def _discover_from_list_page(session: requests.Session, series: str) -> dict[str, dict]:
    """Supplement discovery using links from the OCG/TCG set list wiki pages."""
    list_titles = {
        "OCG": "List of Duel Masters OCG Sets",
        "TCG": "List of Duel Masters TCG Sets",
    }
    found: dict[str, dict] = {}
    titles_to_scan = [list_titles[series]] if series in list_titles else list(list_titles.values())

    for list_title in titles_to_scan:
        logger.info("API links scan on list page: %s", list_title)
        cont: dict[str, str] = {}
        while True:
            data = api_get(
                session,
                {
                    "action": "query",
                    "prop": "links",
                    "titles": list_title,
                    "plnamespace": "0",
                    "pllimit": "500",
                    **cont,
                },
            )
            pages = data.get("query", {}).get("pages", {})
            for page in pages.values():
                for link in page.get("links", []):
                    title = link.get("title", "")
                    if not _is_set_page_title(title):
                        continue
                    set_code = _title_to_set_code(title)
                    if not set_code:
                        continue
                    entry = {
                        "set_code": set_code,
                        "set_name": _set_name_from_title(title, set_code),
                        "set_url": title_to_url(title),
                        "page_title": title,
                        "series": series if series in ("OCG", "TCG") else "OCG",
                    }
                    if set_code in found:
                        found[set_code] = _merge_set(found[set_code], entry)
                    else:
                        found[set_code] = entry
            if "continue" not in data:
                break
            cont = {"plcontinue": data["continue"]["plcontinue"]}

    return found


def discover_sets(
    session: Optional[requests.Session] = None,
    series: str = "OCG",
) -> list[dict]:
    """
    Discover set pages via MediaWiki API.

    Returns list of {set_code, set_name, set_url, page_title, series}.
    """
    if session is None:
        session = make_api_session()

    series_arg = series.upper()
    merged: dict[str, dict] = {}

    if series_arg in ("OCG", "BOTH"):
        for source in (
            _discover_from_categories(session, OCG_SET_CATEGORIES, "OCG"),
            _discover_modern_prefixes(session, "OCG"),
            _discover_from_list_page(session, "OCG"),
        ):
            for code, entry in source.items():
                if code in merged:
                    merged[code] = _merge_set(merged[code], entry)
                else:
                    merged[code] = entry

    if series_arg in ("TCG", "BOTH"):
        for source in (
            _discover_from_categories(session, TCG_SET_CATEGORIES, "TCG"),
            _discover_from_list_page(session, "TCG"),
        ):
            for code, entry in source.items():
                if code in merged:
                    merged[code] = _merge_set(merged[code], entry)
                else:
                    merged[code] = entry

    results = sorted(merged.values(), key=lambda s: s["set_code"])
    logger.info("API discovered %s sets (series=%s)", len(results), series_arg)
    return results
