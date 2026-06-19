"""
twinpact_repair.py
==================
Audit Twinpact cards in the database, auto-fix detectable errors, and
mark broken cards for LLM re-parsing.

Twinpact cards have two faces on one physical card:
  • Creature face  – regular Creature DB entry
  • Spell face     – separate Spell entry with a "♪" name prefix

Errors detected and auto-fixed:
  1. is_multiface=FALSE on ♪ Spell cards  → set to TRUE
  2. ♪ Spell at status='parsed' but missing card_effects → reset to 'scraped'
  3. other_face_id=NULL (not yet linked)   → attempt pair matching via page fetch
     Pair matching: fetch the ♪ card's wiki page to read its collector number
     (N), then look in the DB for a Creature in the same set at number N-1.

Usage:
    python -m scripts.twinpact_repair                  # fix is_multiface + mark for reparse
    python -m scripts.twinpact_repair --dry-run        # preview changes without applying
    python -m scripts.twinpact_repair --list-only      # audit only, no changes
    python -m scripts.twinpact_repair --link-pairs     # also try other_face_id linking via page fetch
    python -m scripts.twinpact_repair --scrape-pairs   # re-crawl set pages, scrape missing creature
                                                       # cards, then link pairs (slowest, most complete)
    python -m scripts.twinpact_repair --slugs SLUG1 SLUG2  # target specific cards
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from typing import Optional

import psycopg2
import psycopg2.extras
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from curl_cffi.requests.errors import RequestsError

# ── Path setup ────────────────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(__file__)
_CRAWLER_DIR = os.path.dirname(_SCRIPT_DIR)
if _CRAWLER_DIR not in sys.path:
    sys.path.insert(0, _CRAWLER_DIR)

from scripts.cf_cookies import apply_cf_cookies, fetch_html_with_browser  # noqa: E402
from scripts.scraper import save_card_to_db, parse_card_page  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_DSN = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:12345@localhost:5432/dm_database",
)

# ─────────────────────────────────────────────────────────────────────────────
# DB queries
# ─────────────────────────────────────────────────────────────────────────────

_SPELL_QUERY = """
    SELECT
        c.id,
        c.name,
        c.slug,
        c.card_type,
        c.cost,
        c.is_multiface,
        c.other_face_id,
        c.source_url,
        (SELECT COUNT(*) FROM card_effects ce WHERE ce.card_id = c.id) AS effects_count,
        cu.url      AS cu_url,
        cu.status   AS cu_status,
        cu.set_code AS cu_set_code
    FROM   cards c
    LEFT   JOIN card_urls cu ON cu.url = c.source_url
    WHERE  {where}
    ORDER  BY c.id
"""


def _query(conn, sql: str, params=None) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def get_spell_cards(conn, slugs: Optional[list[str]] = None) -> list[dict]:
    if slugs:
        where = "c.slug = ANY(%s)"
        params = (slugs,)
    else:
        where = "c.name LIKE '♪%'"
        params = None
    sql = _SPELL_QUERY.format(where=where)
    return _query(conn, sql, params)


def get_errors(cards: list[dict]) -> list[dict]:
    """Return only cards with at least one detectable error."""
    def _err(c: dict) -> bool:
        return (
            not c["is_multiface"]
            or c["other_face_id"] is None
            or (c["effects_count"] == 0 and c["cu_status"] == "parsed")
        )
    return [c for c in cards if _err(c)]


# ─────────────────────────────────────────────────────────────────────────────
# Immediate SQL fixes (no page fetch required)
# ─────────────────────────────────────────────────────────────────────────────

def fix_is_multiface(conn, card_ids: list[int], dry_run: bool) -> int:
    """Set is_multiface=TRUE for cards that still have it FALSE."""
    if not card_ids:
        return 0
    sql = "UPDATE cards SET is_multiface=TRUE, updated_at=NOW() WHERE id=ANY(%s)"
    if dry_run:
        logger.info("[DRY-RUN] Would set is_multiface=TRUE for %d card(s)", len(card_ids))
        return len(card_ids)
    with conn.cursor() as cur:
        cur.execute(sql, (card_ids,))
        n = cur.rowcount
    conn.commit()
    logger.info("Set is_multiface=TRUE for %d card(s)", n)
    return n


def reset_for_reparse(conn, urls: list[str], dry_run: bool) -> int:
    """Reset card_urls.status → 'scraped' so the LLM effect parser re-runs."""
    if not urls:
        return 0
    sql = """
        UPDATE card_urls
        SET    status='scraped', parsed_at=NULL
        WHERE  url=ANY(%s) AND status != 'error'
    """
    if dry_run:
        logger.info("[DRY-RUN] Would reset %d URL(s) to 'scraped'", len(urls))
        return len(urls)
    with conn.cursor() as cur:
        cur.execute(sql, (urls,))
        n = cur.rowcount
    conn.commit()
    logger.info("Reset %d URL(s) to status='scraped'", n)
    return n


def link_pair(conn, spell_id: int, creature_id: int, dry_run: bool) -> None:
    """Bi-directionally set other_face_id between spell and creature faces."""
    sql = "UPDATE cards SET is_multiface=TRUE, other_face_id=%s, updated_at=NOW() WHERE id=%s"
    if dry_run:
        logger.info("[DRY-RUN] Would link spell=%d ↔ creature=%d", spell_id, creature_id)
        return
    with conn.cursor() as cur:
        cur.execute(sql, (creature_id, spell_id))
        cur.execute(sql, (spell_id, creature_id))
    conn.commit()
    logger.info("Linked: spell id=%d ↔ creature id=%d", spell_id, creature_id)


# ─────────────────────────────────────────────────────────────────────────────
# Page-fetch helpers for pair matching
# ─────────────────────────────────────────────────────────────────────────────

# No leading \b — Playwright sometimes renders "HideDMRP-09..." (collapsed toggle).
# We capture any DM set code pattern within the row text.
_SET_CODE_RE = re.compile(r"(DM(?:RP|EX|BD|SD|SP|PS|PD|ART|TG|VS|R|)-?\d+[\w]*)", re.IGNORECASE)
_COLL_NUM_RE = re.compile(r"\b(\d{1,3})/\d{2,3}\b")

# Settle time (ms) to wait after Playwright page load for JS tables to render.
# The "Sets and Rarity" wikitable is JS-collapsed and needs a few seconds.
_PLAYWRIGHT_SETTLE_MS = int(os.getenv("PLAYWRIGHT_SETTLE_MS", "8000"))


def _fetch_html_with_wait(url: str) -> Optional[str]:
    """
    Fetch a wiki page with Playwright and wait for the wikitable to fully render.
    Falls back to a plain content() call if the selector is not found.
    """
    from scripts.cf_cookies import _get_browser_context  # noqa: PLC0415
    logger.info("Fetching with Playwright (settle=%dms): %s", _PLAYWRIGHT_SETTLE_MS, url)
    try:
        ctx = _get_browser_context()
        page = ctx.new_page()  # type: ignore[union-attr]
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            # Wait for the first wikitable to appear, then give extra JS settle time
            try:
                page.wait_for_selector("table.wikitable", timeout=15_000)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            page.wait_for_timeout(_PLAYWRIGHT_SETTLE_MS)
            return page.content()
        finally:
            page.close()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Playwright page fetch failed for %s: %s", url, exc)
        return None


def _fetch_html(url: str, session: cffi_requests.Session, retries: int = 3) -> Optional[str]:
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=25)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 403:
                break
            if resp.status_code == 429:
                time.sleep(30 * (attempt + 1))
        except RequestsError as exc:
            logger.warning("Fetch error (%d/%d): %s", attempt + 1, retries, exc)
            time.sleep(5 * (attempt + 1))
    return _fetch_html_with_wait(url)


def _parse_printings(html: str) -> list[dict]:
    """
    Extract set-code + collector-number pairs from the 'Sets and Rarity' section
    of a fandom wiki card page.

    Strategy: scan the full text content of the page (not row by row) because
    some pages render the set name and collector number in separate elements.
    We scan all text nodes for set codes, then look for the nearest numeric
    collector number following each set code.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Collect all text nodes that may contain set/rarity data
    # Prefer the Sets and Rarity table cell if identifiable
    sets_text = ""
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            header_texts = [c.get_text(strip=True).lower() for c in cells]
            if any("sets and rarity" in h or "sets&rarity" in h for h in header_texts):
                # Found the Sets and Rarity row — use the whole table from here
                sets_text = table.get_text(" ", strip=False)
                break
        if sets_text:
            break

    # Fallback: search the full page text
    if not sets_text:
        sets_text = soup.get_text(" ", strip=False)

    # Now extract (set_code, collector_num) pairs from the text
    # Strategy: find all set codes, then look for the nearest collector num
    # within the next 200 chars of text after each set code match.
    results: list[dict] = []
    seen: set[tuple] = set()

    for m in _SET_CODE_RE.finditer(sets_text):
        sc = m.group(1)
        # Search for a collector number in the ~300 chars following the set code
        window = sets_text[m.start(): m.start() + 300]
        cn_match = _COLL_NUM_RE.search(window)
        col_num = int(cn_match.group(1)) if cn_match else None
        key = (sc, col_num)
        if key not in seen:
            seen.add(key)
            results.append({"set_code": sc, "collector_num": col_num})

    return results


def _find_pair_in_db(conn, spell_id: int, printings: list[dict]) -> Optional[int]:
    """
    Given a ♪ spell's printings, look for a Creature in the DB whose
    printing is at collector_num - 1 in the same set.
    Returns the creature card_id or None.
    """
    sql = """
        SELECT c.id
        FROM   card_printings pp
        JOIN   cards c ON c.id = pp.card_id
        WHERE  pp.set_code      = %s
          AND  pp.collector_num = %s
          AND  pp.card_id       != %s
          AND  c.card_type      = 'Creature'
        LIMIT  1
    """
    for p in printings:
        col = p.get("collector_num")
        sc  = p.get("set_code")
        if col is None or not sc:
            continue
        for adj in (col - 1, col + 1):
            with conn.cursor() as cur:
                cur.execute(sql, (sc, str(adj), spell_id))
                row = cur.fetchone()
                if row:
                    return row[0]
    return None


def _upsert_collector_nums(conn, card_id: int, printings: list[dict], dry_run: bool) -> None:
    """Store any newly discovered collector numbers into card_printings."""
    sql = """
        UPDATE card_printings
        SET    collector_num = %s
        WHERE  card_id = %s AND set_code = %s
          AND  (collector_num IS NULL OR collector_num = '')
    """
    for p in printings:
        if not p.get("collector_num"):
            continue
        if dry_run:
            logger.debug("[DRY-RUN] collector_num=%s for card %d set %s",
                         p["collector_num"], card_id, p["set_code"])
            continue
        with conn.cursor() as cur:
            cur.execute(sql, (str(p["collector_num"]), card_id, p["set_code"]))
    if not dry_run:
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Pair-matching pass (optional, page-fetch based)
# ─────────────────────────────────────────────────────────────────────────────

def attempt_pair_linking(
    conn,
    candidates: list[dict],
    session: cffi_requests.Session,
    dry_run: bool,
    delay: float,
) -> dict[int, Optional[int]]:
    """
    For each unlinked ♪ card, fetch its wiki page, extract its collector
    number, then look for the adjacent Creature in the DB.

    Returns a dict mapping spell_id → creature_id (None if not found).
    """
    result: dict[int, Optional[int]] = {}

    for i, cand in enumerate(candidates):
        if cand.get("other_face_id"):
            result[cand["id"]] = cand["other_face_id"]  # already linked
            continue

        url = cand.get("source_url")
        if not url:
            result[cand["id"]] = None
            continue

        logger.info("  [pair %d/%d] Fetching %s", i + 1, len(candidates), cand["name"])
        html = _fetch_html(url, session)

        if html:
            printings = _parse_printings(html)
            logger.debug("    printings: %s", printings)
            _upsert_collector_nums(conn, cand["id"], printings, dry_run)
            paired = _find_pair_in_db(conn, cand["id"], printings)
        else:
            paired = None

        result[cand["id"]] = paired

        if paired:
            link_pair(conn, cand["id"], paired, dry_run)
        else:
            logger.warning("    No paired Creature found in DB for %s", cand["name"])

        if i + 1 < len(candidates):
            time.sleep(delay)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Scrape-and-link pass (--scrape-pairs: re-discover set, scrape missing, link)
# ─────────────────────────────────────────────────────────────────────────────

# Known set page URL pattern on the fandom wiki
_SET_PAGE_URL = "https://duelmasters.fandom.com/wiki/{set_code}"


def _insert_card_url(conn, url: str, card_name: str, set_code: str) -> bool:
    """Insert a card_url row if not already present. Returns True if inserted."""
    sql = """
        INSERT INTO card_urls (url, card_name, set_code, status, discovered_at)
        VALUES (%s, %s, %s, 'discovered', NOW())
        ON CONFLICT (url) DO NOTHING
    """
    with conn.cursor() as cur:
        cur.execute(sql, (url, card_name, set_code))
        inserted = cur.rowcount > 0
    conn.commit()
    return inserted


def _scrape_and_save(url: str, set_code: str, dsn: str, session: cffi_requests.Session) -> Optional[int]:
    """
    Fetch a single card page, parse it, save to DB, and return its card_id.
    Returns None on failure or if the page is not a real card.
    """
    html = _fetch_html(url, session)
    if not html:
        return None
    card = parse_card_page(html, set_code, url)
    if card is None:
        return None
    conn = psycopg2.connect(dsn)
    try:
        card_id = save_card_to_db(card, conn)
        # Update card_urls status to 'scraped'
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE card_urls SET status='scraped', scraped_at=NOW() WHERE url=%s",
                (url,),
            )
        conn.commit()
        return card_id
    except Exception as exc:  # pylint: disable=broad-exception-caught
        conn.rollback()
        logger.error("DB error saving %s: %s", url, exc)
        return None
    finally:
        conn.close()


_TWINPACT_CREATURE_RACES = re.compile(
    r"play\s*music|music\s*idols?|wonderforce|metallica|angel\s*command|beat\s*jockey|"
    r"mafi\s*gang|fire\s*bird|flame\s*command",
    re.IGNORECASE,
)


def _get_potential_creature_pairs(conn, set_code: str) -> list[dict]:
    """
    Return cards from set_code that are likely Twinpact Creature faces:
    any Creature card in the same set whose name or race matches known
    Twinpact creature patterns (e.g. 'Play Music').
    """
    sql = """
        SELECT c.id, c.name, c.source_url
        FROM   cards c
        JOIN   card_urls cu ON cu.url = c.source_url
        WHERE  cu.set_code = %s
          AND  c.card_type = 'Creature'
          AND  c.is_multiface IS NOT TRUE
          AND  c.other_face_id IS NULL
        ORDER  BY c.id
    """
    return _query(conn, sql, (set_code,))


def scrape_and_link_pairs(
    conn,
    candidates: list[dict],
    session: cffi_requests.Session,
    dsn: str,
    dry_run: bool,
    delay: float,
) -> dict[int, Optional[int]]:
    """
    Full scrape-then-link workflow for ♪ cards whose creature pair is missing:

    Phase 1: Fetch each ♪ spell page → parse its collector number N in set S.
    Phase 2: For each set S, fetch all unlinked Creature pages → parse their
             collector numbers → upsert into card_printings.
    Phase 3: For each ♪ spell at position N, look for a Creature at N±1.
             If found, call link_pair().

    Returns a dict mapping spell_id → creature_id (None if not found).
    """
    result: dict[int, Optional[int]] = {}
    spell_printings: dict[int, list[dict]] = {}

    # ── Phase 1: collect collector numbers for all unlinked ♪ cards ──────────
    logger.info("Phase 1/3: Reading collector numbers from ♪ spell pages...")
    for i, cand in enumerate(candidates):
        if cand.get("other_face_id"):
            result[cand["id"]] = cand["other_face_id"]
            continue
        url = cand.get("source_url")
        if not url:
            result[cand["id"]] = None
            continue
        logger.info("  [%d/%d] %s", i + 1, len(candidates), cand["name"])
        html = _fetch_html_with_wait(url)
        if html:
            printings = _parse_printings(html)
            _upsert_collector_nums(conn, cand["id"], printings, dry_run)
            spell_printings[cand["id"]] = printings
            with_col = [p for p in printings if p.get("collector_num")]
            logger.info("    collector nums: %s", with_col if with_col else "(none found)")
        else:
            spell_printings[cand["id"]] = []
            logger.warning("    page fetch failed")
        if i + 1 < len(candidates):
            time.sleep(delay)

    # ── Phase 2: populate collector numbers for potential creature pairs ───────
    # Identify which sets we need to search
    needed_sets: set[str] = set()
    target_pairs: dict[tuple[str, int], int] = {}   # (set_code, col_num) → spell_id
    for cand in candidates:
        if cand.get("other_face_id"):
            continue
        for p in spell_printings.get(cand["id"], []):
            sc = p.get("set_code")
            col = p.get("collector_num")
            if sc and col:
                needed_sets.add(sc)
                # Creature could be at N-1 or N+1
                target_pairs[(sc, col - 1)] = cand["id"]
                target_pairs[(sc, col + 1)] = cand["id"]

    logger.info("Phase 2/3: Fetching creature pages in %d set(s) to get collector numbers: %s",
                len(needed_sets), ", ".join(sorted(needed_sets)))

    creature_col_cache: dict[int, Optional[int]] = {}   # creature_id → col_num

    for sc in sorted(needed_sets):
        creatures_in_set = _get_potential_creature_pairs(conn, sc)
        logger.info("  Set %s: %d unlinked Creature cards to check", sc, len(creatures_in_set))

        for j, creature in enumerate(creatures_in_set):
            cid = creature["id"]
            c_url = creature.get("source_url")
            if not c_url:
                continue

            # Skip if we already have this creature's collector num
            existing = _query(
                conn,
                "SELECT collector_num FROM card_printings WHERE card_id=%s AND set_code=%s",
                (cid, sc),
            )
            existing_col = next(
                (int(r["collector_num"]) for r in existing
                 if r.get("collector_num") and str(r["collector_num"]).strip().isdigit()),
                None,
            )
            if existing_col is not None:
                creature_col_cache[cid] = existing_col
                continue

            logger.info("    [%d/%d] Fetching creature: %s", j + 1, len(creatures_in_set),
                        creature["name"])
            if dry_run:
                logger.info("    [DRY-RUN] Would fetch %s", c_url)
                continue

            html = _fetch_html_with_wait(c_url)
            if not html:
                logger.warning("    Failed to fetch %s", c_url)
                continue

            printings = _parse_printings(html)
            _upsert_collector_nums(conn, cid, printings, dry_run)

            # Find the collector num for this set
            col_for_set = next(
                (p["collector_num"] for p in printings
                 if p.get("set_code") == sc and p.get("collector_num")),
                None,
            )
            creature_col_cache[cid] = col_for_set
            if col_for_set:
                logger.info("    → collector #%d", col_for_set)

            if j + 1 < len(creatures_in_set):
                time.sleep(delay)

    # ── Phase 3: link pairs ────────────────────────────────────────────────────
    logger.info("Phase 3/3: Matching and linking pairs...")

    # Now try _find_pair_in_db with populated collector numbers
    for cand in candidates:
        if cand.get("other_face_id") or cand["id"] in result:
            continue
        printings = spell_printings.get(cand["id"], [])
        paired = _find_pair_in_db(conn, cand["id"], printings)
        if paired:
            logger.info("  ✓ Linked %s ↔ creature id=%d", cand["name"], paired)
            link_pair(conn, cand["id"], paired, dry_run)
            result[cand["id"]] = paired
            # Mark the creature for re-parsing too
            creature_url_rows = _query(conn, "SELECT source_url FROM cards WHERE id=%s", (paired,))
            if creature_url_rows and creature_url_rows[0].get("source_url"):
                reset_for_reparse(conn, [creature_url_rows[0]["source_url"]], dry_run)
        else:
            logger.warning("  Still no pair found for %s", cand["name"])
            result[cand["id"]] = None

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────

def run(
    dsn: str,
    slugs: Optional[list[str]] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
    delay: float = 1.5,
    link_pairs: bool = False,
    scrape_pairs: bool = False,
    errors_only: bool = True,
) -> None:
    conn = psycopg2.connect(dsn)
    all_spell_cards = get_spell_cards(conn, slugs)
    logger.info("Found %d ♪ Twinpact spell card(s) in DB", len(all_spell_cards))

    candidates = get_errors(all_spell_cards) if errors_only else all_spell_cards
    logger.info("  → %d have detectable errors", len(candidates))

    if limit:
        candidates = candidates[:limit]

    if not candidates:
        logger.info("Nothing to repair.")
        conn.close()
        return

    # ── Step 1: Fix is_multiface immediately (no page fetch) ──────────────────
    fix_ids = [c["id"] for c in candidates if not c["is_multiface"]]
    fix_is_multiface(conn, fix_ids, dry_run)

    # ── Step 2: Mark cards for re-parsing ────────────────────────────────────
    reparse_urls = [
        c["cu_url"] for c in candidates
        if c.get("cu_url") and (
            not c["is_multiface"]                                    # multiface was wrong
            or (c["effects_count"] == 0 and c["cu_status"] == "parsed")  # empty effects
        )
    ]
    reset_for_reparse(conn, reparse_urls, dry_run)

    # ── Step 3 (optional): Attempt other_face_id linking ─────────────────────
    pair_results: dict[int, Optional[int]] = {}
    unlinked = [c for c in candidates if c.get("other_face_id") is None]

    if scrape_pairs and unlinked:
        logger.info("--scrape-pairs: re-crawling set pages and scraping missing creature cards...")
        session = cffi_requests.Session(impersonate="chrome124")
        apply_cf_cookies(session)
        pair_results = scrape_and_link_pairs(conn, unlinked, session, dsn, dry_run, delay)
    elif link_pairs and unlinked:
        logger.info("Attempting pair-linking via page fetch (--link-pairs)...")
        session = cffi_requests.Session(impersonate="chrome124")
        apply_cf_cookies(session)
        pair_results = attempt_pair_linking(conn, unlinked, session, dry_run, delay)

    conn.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    linked_count   = sum(1 for v in pair_results.values() if v is not None)
    unlinked_count = sum(1 for v in pair_results.values() if v is None)
    still_unlinked = [
        c["name"] for c in candidates
        if c.get("other_face_id") is None and pair_results.get(c["id"]) is None
    ]

    logger.info("")
    logger.info("═══════════════ Summary ═══════════════")
    logger.info("  ♪ Twinpact spell cards         : %d", len(all_spell_cards))
    logger.info("  Cards with errors              : %d", len(candidates))
    logger.info("  is_multiface fixed             : %d%s",
                len(fix_ids), " (dry-run)" if dry_run else "")
    logger.info("  Marked for re-parsing          : %d%s",
                len(reparse_urls), " (dry-run)" if dry_run else "")
    if link_pairs or scrape_pairs:
        logger.info("  Paired (other_face_id linked)  : %d%s",
                    linked_count, " (dry-run)" if dry_run else "")
        logger.info("  Still unlinked (pair not in DB): %d", unlinked_count)
    else:
        logger.info("  Pair-linking skipped")

    if still_unlinked:
        logger.info("")
        logger.info("Cards still needing other_face_id (creature pair missing from DB):")
        for name in still_unlinked:
            logger.info("  • %s", name)

    if not link_pairs and not scrape_pairs:
        logger.info("")
        logger.info("Tip: run with --scrape-pairs to re-crawl set pages, scrape missing "
                    "creature cards, and link pairs automatically.")


# ─────────────────────────────────────────────────────────────────────────────
# Listing / audit
# ─────────────────────────────────────────────────────────────────────────────

def list_twinpacts(conn, slugs: Optional[list[str]] = None) -> None:
    cards = get_spell_cards(conn, slugs)
    errors = get_errors(cards)

    header = (
        f"{'ID':>6}  {'Name':<52}  {'multi':5}  {'other_id':>8}  "
        f"{'effects':>7}  {'cu_status':<10}  {'ERR':3}"
    )
    sep = "─" * len(header)
    print(f"\n{header}")
    print(sep)
    for c in cards:
        is_err = c in errors
        print(
            f"{c['id']:>6}  {c['name'][:52]:<52}  "
            f"{str(c['is_multiface']):<5}  "
            f"{str(c.get('other_face_id') or ''):>8}  "
            f"{int(c['effects_count']):>7}  "
            f"{(c.get('cu_status') or 'N/A'):<10}  "
            f"{'YES' if is_err else '—':3}"
        )
    print(sep)
    print(f"\nTotal: {len(cards)} ♪ Twinpact spell cards, {len(errors)} with errors\n")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Audit and repair Twinpact (♪ Spell) cards in the database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--dsn", default=DEFAULT_DSN, help="PostgreSQL DSN")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would change without touching the DB.")
    p.add_argument("--limit", type=int, default=None, metavar="N",
                   help="Process at most N candidates.")
    p.add_argument("--slugs", nargs="+", default=None, metavar="SLUG",
                   help="Target specific card slugs instead of auto-detecting.")
    p.add_argument("--delay", type=float, default=1.5, metavar="SECS",
                   help="Wait between page fetches when --link-pairs is used.")
    p.add_argument("--list-only", action="store_true",
                   help="Print audit table; do not change anything.")
    p.add_argument("--all", dest="all_cards", action="store_true",
                   help="Process all ♪ cards, not just those with errors.")
    p.add_argument("--link-pairs", action="store_true",
                   help="Fetch each ♪ card's wiki page to find and link its "
                        "paired Creature via collector-number adjacency "
                        "(only works if the Creature is already in the DB).")
    p.add_argument("--scrape-pairs", action="store_true",
                   help="Re-crawl set pages, scrape any missing Creature cards, "
                        "then link pairs via collector-number adjacency. "
                        "Slowest but most complete — use when --link-pairs finds nothing.")
    return p


def main() -> None:
    args = _build_parser().parse_args()
    conn = psycopg2.connect(args.dsn)

    if args.list_only:
        list_twinpacts(conn, slugs=args.slugs)
        conn.close()
        return

    conn.close()
    run(
        dsn=args.dsn,
        slugs=args.slugs,
        dry_run=args.dry_run,
        limit=args.limit,
        delay=args.delay,
        link_pairs=args.link_pairs,
        scrape_pairs=args.scrape_pairs,
        errors_only=not args.all_cards,
    )


if __name__ == "__main__":
    main()
