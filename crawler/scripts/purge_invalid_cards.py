#!/usr/bin/env python3
"""
purge_invalid_cards.py — Remove wiki junk rows stored as cards (card_type = Unknown).

These are character pages, race pages, pack/deck product pages, etc. that were
linked from set pages and incorrectly scraped via the HTML fallback parser.

Usage:
    python scripts/purge_invalid_cards.py [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import psycopg2
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Child tables with ON DELETE CASCADE from cards(id)
CASCADE_TABLES = (
    "card_civilizations",
    "card_races",
    "card_printings",
    "card_rulings",
    "card_keywords",
    "card_relations",
    "card_effects",
    "card_urls",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge invalid Unknown-type cards")
    parser.add_argument("--dry-run", action="store_true", help="Report only, do not delete")
    args = parser.parse_args()

    load_dotenv(".env")
    load_dotenv("../.env")
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.error("DATABASE_URL not set")
        return 1

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, slug, name, source_url
                FROM cards
                WHERE card_type = 'Unknown'
                ORDER BY id
                """
            )
            rows = cur.fetchall()
            logger.info("Found %s cards with card_type = Unknown", len(rows))

            if args.dry_run:
                for row in rows[:20]:
                    logger.info("  would delete id=%s slug=%s", row[0], row[1])
                if len(rows) > 20:
                    logger.info("  ... and %s more", len(rows) - 20)
                return 0

            if not rows:
                return 0

            ids = [r[0] for r in rows]
            cur.execute("DELETE FROM cards WHERE id = ANY(%s)", (ids,))
            deleted = cur.rowcount
        conn.commit()
        logger.info("Deleted %s invalid card rows (cascades to child tables)", deleted)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
