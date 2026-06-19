"""
Discover card URLs for sets present in the downloaded OCG Sets HTML
but missing from card_sets / card_urls. Uses MediaWiki API (no Playwright).

Usage:
    cd crawler/
    python add_missing_sets.py [--dry-run] [--only-codes SET1 SET2]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import psycopg2

from scripts.api_client import (
    db_upsert_card_urls,
    db_upsert_set,
    make_api_session,
    url_to_title,
)
from scripts.api_set_page import get_set_card_links
from scripts.sets_crawler import _parse_sets_page

DSN = os.getenv("DATABASE_URL", "postgresql://postgres:12345@localhost:5432/dm_database")
HTML_FILE = os.path.join(
    os.path.dirname(__file__),
    "List of Duel Masters OCG Sets _ Duel Masters Wiki _ Fandom.html",
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("add_missing_sets")


def _sets_in_db(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT set_code FROM card_sets")
        return {row[0] for row in cur.fetchall()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show missing sets only")
    parser.add_argument(
        "--only-codes",
        nargs="*",
        metavar="SET_CODE",
        help="Only process specific set codes",
    )
    args = parser.parse_args()

    log.info("Parsing downloaded HTML: %s", HTML_FILE)
    with open(HTML_FILE, encoding="utf-8") as handle:
        html = handle.read()
    all_sets = _parse_sets_page(html, "OCG")
    log.info("Found %s sets in downloaded page", len(all_sets))

    conn = psycopg2.connect(DSN)
    try:
        in_db = _sets_in_db(conn)
    finally:
        conn.close()
    log.info("Sets already in card_sets: %s", len(in_db))

    missing = [s for s in all_sets if s["set_code"] not in in_db]
    if args.only_codes:
        only = set(args.only_codes)
        missing = [s for s in missing if s["set_code"] in only]
    log.info("Missing sets to discover: %s", len(missing))

    if not missing:
        log.info("Nothing to do!")
        return

    if args.dry_run:
        log.info("DRY-RUN — would process:")
        for s in missing:
            print(f"  {s['set_code']:20} {s['set_name']}")
        return

    session = make_api_session()
    total_urls = 0
    errors: list[str] = []

    for idx, s in enumerate(missing, 1):
        set_code = s["set_code"]
        set_name = s["set_name"]
        set_url = s["set_url"]
        page_title = url_to_title(set_url)

        log.info("[%s/%s] %s — %s", idx, len(missing), set_code, set_name)

        try:
            card_dicts = get_set_card_links(session, page_title, set_code)
        except Exception as exc:
            log.error("  API card discovery failed: %s", exc)
            errors.append(set_code)
            continue

        if not card_dicts:
            log.warning("  No card URLs found via API")
            errors.append(set_code)
            continue

        conn = psycopg2.connect(DSN)
        try:
            db_upsert_set(conn, {**s, "series": s.get("series", "OCG")})
            inserted = db_upsert_card_urls(conn, card_dicts)
            conn.commit()
            log.info("  ✓ %s: upserted set + %s card URLs", set_code, inserted)
            total_urls += len(card_dicts)
        except Exception as exc:
            conn.rollback()
            log.error("  DB upsert failed: %s", exc)
            errors.append(set_code)
        finally:
            conn.close()

    log.info("=" * 60)
    log.info("Done. Total card URLs upserted: %s", total_urls)
    if errors:
        log.warning("Sets with errors (%s): %s", len(errors), errors)
    else:
        log.info("All sets processed successfully!")


if __name__ == "__main__":
    main()
