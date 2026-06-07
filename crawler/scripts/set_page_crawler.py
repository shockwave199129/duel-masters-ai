"""
set_page_crawler.py — Level 2: Given a set page URL, extract all card page URLs
that belong to that set's main card list.

Design principles
─────────────────
• Card hrefs must be exactly /wiki/<slug> — no sub-paths (rules out Cycle/... pages).
• Section skipping is LEVEL-AWARE:
    h2 "Reprinted Cards"           → skip (standalone reprint appendix for booster packs)
    h2 "Alternate Artwork"         → skip
    h2 "Gallery"                   → skip
    h2 "Contents sorted by …"     → skip (duplicate listing by civilization)
    h3 "Reprinted Cards" under     → INCLUDE (deck products: reprints are deck content)
       h2 "Contents"
    h3 "New Cards" under           → INCLUDE
       h2 "Contents"
• Old-format pages (DM-01 – DM-27 era) have no explicit "Card List" heading —
  just rarity sub-headings (Super Rare, Very Rare, Rare…). All rarity sections
  are included since none match the h2 skip patterns.
• Fallback: if section walk produces no cards, scan the full content area.
• Deduplicates URLs within a single set page crawl.

Entry point:
    crawl_set_page(set_url, set_code, session) -> list[dict]

Returns:
    [{"url": "https://...wiki/Card_Name", "card_name": "...", "set_code": "..."}, ...]
"""

from __future__ import annotations
import logging
import re
import time
import random
from typing import Optional

from bs4 import BeautifulSoup, Tag
from curl_cffi import requests
from curl_cffi.requests.errors import RequestsError

from scripts.cf_cookies import apply_cf_cookies

logger = logging.getLogger(__name__)

BASE_URL = "https://duelmasters.fandom.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Section headings that indicate a reprint/bonus/alt-art section — skip these
SKIP_SECTION_RE = re.compile(
    r"(reprint|reprinted|re-print|new artwork|alternate art|secret|promo"
    r"|bonus|gallery|full.?art|stamp|foil|parallel|special|alternate)",
    re.IGNORECASE,
)

# href pattern — individual card pages on duelmasters.fandom.com.
# Twin Pact card titles can contain "/" in the wiki slug, so slashes are allowed;
# namespace pages with ":" are still excluded.
CARD_HREF_RE = re.compile(r"^/wiki/[^:]+$")

# Set code patterns — to exclude set links from card links.
# Covers both old-format (DM-01, DMR-13, DMRP-22, DMD-14 …) and
# modern-format (DM22-BD1, DM23-RP4, DM24-RP4, DM25-RP4 …) set codes.
SET_CODE_IN_HREF_RE = re.compile(
    r"^/wiki/("
    r"DM\d{2,4}-"                         # modern: DM22-BD1, DM25-RP4
    r"|DM-|DMR-|DMRP-|DMD-|DMBD-|DMSD-"  # old booster/deck
    r"|DMEX-|DMSP-|DMPS-|DMPD-|DMART-|DMTG-"
    r")",
    re.IGNORECASE,
)

# Patterns in href/title that clearly mean it's NOT a card page
NON_CARD_HREF_FRAGMENTS = (
    "Gallery", "gallery", "Talk:", "User:", "File:", "Category:",
    "Template:", "Help:", "Special:", "Wikipedia:", "disambiguation",
    "List_of", "List_Of", "Cycle/",
)


def _polite_delay(min_s: float = 1.5, max_s: float = 3.0):
    time.sleep(random.uniform(min_s, max_s))


def _fetch(url: str, session: requests.Session, retries: int = 3) -> Optional[str]:
    refreshed_cookies = False
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 403 and not refreshed_cookies:
                logger.warning("HTTP 403 for %s; refreshing browser cookies and retrying", url)
                apply_cf_cookies(session, force=True)
                refreshed_cookies = True
                continue
            elif resp.status_code == 429:
                wait = 30 * (attempt + 1)
                logger.warning(f"Rate limited, waiting {wait}s")
                time.sleep(wait)
            else:
                logger.warning(f"HTTP {resp.status_code} for {url}")
                return None
        except RequestsError as e:
            logger.warning(f"Request error ({attempt+1}/{retries}): {e}")
            time.sleep(5 * (attempt + 1))
    return None


def _is_valid_card_href(href: str) -> bool:
    if not CARD_HREF_RE.match(href):
        return False
    if SET_CODE_IN_HREF_RE.match(href):
        return False
    for frag in NON_CARD_HREF_FRAGMENTS:
        if frag in href:
            return False
    return True


def _heading_text(tag: Tag) -> str:
    """Get clean text from a heading tag (h2/h3/h4)."""
    # Remove [edit] span if present
    for span in tag.find_all("span", class_="mw-editsection"):
        span.decompose()
    return tag.get_text(strip=True)


def _extract_cards_from_section(section_soup: Tag, set_code: str) -> list[dict]:
    """
    Extract card links from a section blob (could be a table, ul, or div).
    """
    cards = []
    seen_urls: set[str] = set()

    for a in section_soup.find_all("a", href=True):
        href: str = a["href"]
        if not _is_valid_card_href(href):
            continue
        full_url = BASE_URL + href
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        name = a.get("title") or a.get_text(strip=True)
        # Skip anchors that look like collector numbers (e.g. "1", "1/110")
        if re.match(r"^\d+[/\d]*$", name):
            continue
        cards.append({
            "url": full_url,
            "card_name": name,
            "set_code": set_code,
        })
    return cards


def _parse_set_page(html: str, set_code: str) -> list[dict]:
    """
    Parse a set page and extract card URLs.

    Skipping strategy (level-aware):
    ─────────────────────────────────
    • h2 "Reprinted Cards"        → skip (standalone reprint appendix)
    • h2 "Alternate Artwork"      → skip
    • h2 "Contents sorted by …"  → skip IF a plain "Contents" h2 was already
      seen (duplicate). INCLUDE if no plain Contents exists (DMBD deck pages).
    • h2 "Gallery"                → skip
    • h3 "Reprinted Cards" under h2 "Contents" → INCLUDE
      (deck products list all deck cards, reprints included, under Contents)
    • h3 "New Cards" under h2 "Contents"       → INCLUDE

    Fallback: if no sections produce cards, scan the full content area.
    """
    soup = BeautifulSoup(html, "html.parser")
    content = (
        soup.find("div", class_="mw-content-text")
        or soup.find("div", id="mw-content-text")
        or soup
    )

    all_cards: list[dict] = []
    seen_urls: set[str] = set()

    def add_cards(cards: list[dict]):
        for c in cards:
            if c["url"] not in seen_urls:
                seen_urls.add(c["url"])
                all_cards.append(c)

    # ── Strategy 1: Level-aware section walk ──────────────────────────────────
    headings = content.find_all(["h2", "h3", "h4"])

    if headings:
        elements = list(content.children)

        current_is_skip = False
        current_h2_is_skip = False   # tracks parent h2 state for h3 decisions
        seen_contents_h2 = False     # True once we pass a plain "Contents" h2

        for el in elements:
            if not isinstance(el, Tag):
                continue

            if el.name in ("h2", "h3", "h4"):
                heading_text = _heading_text(el)
                level = int(el.name[1])  # 2, 3, or 4

                if level == 2:
                    # ── h2-level skip rules ────────────────────────────────
                    # Track plain "Contents" h2 (not "Contents sorted by...")
                    if re.match(r"^contents?\s*$", heading_text, re.IGNORECASE):
                        seen_contents_h2 = True

                    is_sorted_by = bool(
                        re.search(r"sorted.?by|by.?civilization", heading_text, re.IGNORECASE)
                    )
                    # "Contents sorted by Civilizations":
                    #   SKIP only if a plain "Contents" h2 was already seen (duplicate).
                    #   INCLUDE if it's the only listing (DMBD deck pages have no plain Contents).
                    sorted_by_skip = is_sorted_by and seen_contents_h2

                    h2_skip = bool(SKIP_SECTION_RE.search(heading_text) or sorted_by_skip)
                    current_is_skip = h2_skip
                    current_h2_is_skip = h2_skip

                else:
                    # ── h3/h4-level skip rules ─────────────────────────────
                    if current_h2_is_skip:
                        # Parent h2 is already a skip section → skip everything inside
                        current_is_skip = True
                    else:
                        # Parent h2 is content (e.g. "Contents") — decide per sub-heading
                        # "Reprinted Cards" / "New Cards" at h3 under Contents = deck listing
                        # → INCLUDE both; only skip sub-headings that are clearly decorative
                        h3_skip = bool(
                            re.search(r"gallery|sorted.?by|by.?civilization", heading_text, re.IGNORECASE)
                        )
                        current_is_skip = h3_skip
                continue

            if current_is_skip:
                continue

            cards = _extract_cards_from_section(el, set_code)
            add_cards(cards)

        if all_cards:
            logger.debug(f"Section strategy: {len(all_cards)} card links from {set_code}")
            # Post-filter before returning
            return _post_filter(all_cards, set_code)

    # ── Strategy 2: Full-page fallback (no sections or none produced cards) ───
    logger.debug(f"Fallback full-page scan for {set_code}")
    cards = _extract_cards_from_section(content, set_code)
    add_cards(cards)
    return _post_filter(all_cards, set_code)


def _post_filter(cards: list[dict], set_code: str) -> list[dict]:
    """Remove nav/meta links that slipped through section filtering."""
    filtered = []
    for c in cards:
        name = c["card_name"]
        if any(x in name for x in ("(TCG)", "(OCG)", "(manga)", "(anime)", "(character)")):
            continue
        if len(name) < 2:
            continue
        filtered.append(c)
    logger.info(f"Set {set_code}: extracted {len(filtered)} card links")
    return filtered


def crawl_set_page(
    set_url: str,
    set_code: str,
    session: Optional[requests.Session] = None,
) -> list[dict]:
    """
    Crawl a single set page and return card URLs for that set.

    Args:
        set_url:  Full URL of the set wiki page
        set_code: Set code (e.g. "DMRP-22") for tagging results
        session:  Optional pre-configured requests.Session

    Returns:
        List of {"url", "card_name", "set_code"} dicts
    """
    if session is None:
        session = requests.Session(impersonate="chrome124")
        apply_cf_cookies(session)

    logger.info(f"Crawling set page: {set_code} — {set_url}")
    html = _fetch(set_url, session)
    if not html:
        logger.error(f"Failed to fetch set page: {set_url}")
        return []

    return _parse_set_page(html, set_code)
