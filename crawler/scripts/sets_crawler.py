"""
sets_crawler.py — Level 1: Crawl the "List of OCG/TCG Sets" pages and extract
all individual set URLs.

Entry points:
    crawl_sets_list(series="OCG"|"TCG"|"both") -> list[dict]

Each returned dict:
    {
        "set_code": "DMRP-22",
        "set_name": "The Super King Has Arrived!!",
        "set_url":  "https://duelmasters.fandom.com/wiki/DMRP-22",
        "series":   "OCG"
    }

The pages we crawl:
    OCG: https://duelmasters.fandom.com/wiki/List_of_Duel_Masters_OCG_Sets
    TCG: https://duelmasters.fandom.com/wiki/List_of_Duel_Masters_TCG_Sets

Both pages contain anchor tags inside tables/lists with set names and hrefs.
We filter only hrefs that look like card set codes (DM-01, DMRP-22, DMR-01, etc.)
"""

from __future__ import annotations
import logging
import re
import time
import random
from urllib.parse import unquote
from typing import Optional

from bs4 import BeautifulSoup
from curl_cffi import requests
from curl_cffi.requests.errors import RequestsError

from scripts.cf_cookies import apply_cf_cookies

logger = logging.getLogger(__name__)

BASE_URL = "https://duelmasters.fandom.com"

SETS_PAGES = {
    "OCG": f"{BASE_URL}/wiki/List_of_Duel_Masters_OCG_Sets",
    "TCG": f"{BASE_URL}/wiki/List_of_Duel_Masters_TCG_Sets",
}

# Regex for valid set codes (covers all known series prefixes)
SET_CODE_RE = re.compile(
    r"^(DM|DMR|DMRP|DMD|DMBD|DMSD|DMEX|DMSP|DMPS|DMPD|DMART|DMTG|DMVS"
    r"|S|P|DMP|DMC|DMS|DMX|DMF|DMT)-?\d+",
    re.IGNORECASE,
)

# href patterns for set pages — they look like /wiki/DMRP-22 or /wiki/DM-01_Base_Set
SET_HREF_RE = re.compile(
    r"^/wiki/(DM|DMR|DMRP|DMD|DMBD|DMSD|DMEX|DMSP|DMPS|DMPD|DMART|DMTG|DMVS"
    r"|S\d+|DMP|DMC|DMS|DMX|DMF|DMT)-",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _polite_delay(min_s: float = 1.5, max_s: float = 3.5):
    time.sleep(random.uniform(min_s, max_s))


def _fetch(url: str, session: requests.Session, retries: int = 3) -> Optional[str]:
    refreshed_cookies = False
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 403 and not refreshed_cookies:
                logger.warning("HTTP 403 on %s; refreshing browser cookies and retrying", url)
                apply_cf_cookies(session, force=True)
                refreshed_cookies = True
                continue
            elif resp.status_code == 429:
                wait = 30 * (attempt + 1)
                logger.warning(f"Rate limited on {url}, waiting {wait}s")
                time.sleep(wait)
            else:
                logger.warning(f"HTTP {resp.status_code} on {url}")
                return None
        except RequestsError as e:
            logger.warning(f"Request error ({attempt+1}/{retries}) for {url}: {e}")
            time.sleep(5 * (attempt + 1))
    return None


def _extract_set_code_from_href(href: str) -> Optional[str]:
    """
    Extract the canonical set code from a wiki href.
    /wiki/DMRP-22_The_Super_King_Has_Arrived!! → DMRP-22
    /wiki/DM-01_Base_Set                        → DM-01
    """
    slug = href.replace("/wiki/", "").split("_")[0]
    # Clean any URL-encoded chars
    slug = unquote(slug)
    if SET_CODE_RE.match(slug):
        return slug.upper()
    return None


def _parse_sets_page(html: str, series: str, base_url: str = BASE_URL) -> list[dict]:
    """
    Parse a List_of_Duel_Masters_*_Sets page.
    Returns list of {set_code, set_name, set_url, series}.

    These pages have varying structures across edits, so we cast a wide net:
    - All <a> tags whose href matches SET_HREF_RE
    - Deduplicate by set_code
    - Skip Gallery pages (contain "Gallery" in name or href)
    """
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    results: list[dict] = []

    # Find the main content area to avoid navbar pollution
    content = soup.find("div", id="content") or soup.find("div", class_="mw-content-text") or soup

    for a in content.find_all("a", href=True):
        href: str = a["href"]

        # Must match set href pattern
        if not SET_HREF_RE.match(href):
            continue

        # Skip gallery pages
        if "Gallery" in href or "gallery" in href:
            continue

        # Skip disambiguation pages
        if "disambiguation" in href.lower():
            continue

        set_code = _extract_set_code_from_href(href)
        if not set_code or set_code in seen:
            continue

        # Build full URL
        set_url = base_url + href if href.startswith("/") else href

        # Card name: link text, cleaned
        set_name = a.get_text(strip=True)
        # Remove the set code prefix if it appears in name (e.g. "DMRP-22 The Super King")
        set_name = re.sub(rf"^{re.escape(set_code)}\s*[:\-—]?\s*", "", set_name).strip()
        if not set_name:
            # Fall back to slug-based name from href
            slug = href.replace("/wiki/", "")
            set_name = slug.replace("_", " ").split("(")[0].strip()

        seen.add(set_code)
        results.append({
            "set_code": set_code,
            "set_name": set_name,
            "set_url": set_url,
            "series": series,
        })

    return results


def crawl_sets_list(
    series: str = "both",
    session: Optional[requests.Session] = None,
) -> list[dict]:
    """
    Crawl the set-list pages and return all discovered sets.

    Args:
        series: "OCG" | "TCG" | "both"
        session: optional pre-configured requests.Session

    Returns:
        List of set dicts with keys: set_code, set_name, set_url, series
    """
    if session is None:
        session = requests.Session(impersonate="chrome124")
        apply_cf_cookies(session)

    pages_to_crawl = (
        list(SETS_PAGES.items())
        if series.lower() == "both"
        else [(series.upper(), SETS_PAGES[series.upper()])]
    )

    all_sets: list[dict] = []
    seen_codes: set[str] = set()

    for series_name, page_url in pages_to_crawl:
        logger.info(f"Crawling {series_name} sets list: {page_url}")
        html = _fetch(page_url, session)
        if not html:
            logger.error(f"Failed to fetch sets list for {series_name}")
            continue

        sets = _parse_sets_page(html, series_name)
        logger.info(f"Found {len(sets)} sets on {series_name} page")

        for s in sets:
            if s["set_code"] not in seen_codes:
                all_sets.append(s)
                seen_codes.add(s["set_code"])

        _polite_delay(2, 4)

    logger.info(f"Total sets discovered: {len(all_sets)}")
    return all_sets
