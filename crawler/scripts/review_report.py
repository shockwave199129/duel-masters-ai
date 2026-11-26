"""List cards whose parsed effects need review.

Usage (from the crawler directory, with DATABASE_URL set):
    python scripts/review_report.py
"""

import os
import sys

import psycopg2


def main() -> None:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL is not set")
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT card_id, ability_index, parse_confidence, raw_text
                FROM card_effects
                WHERE needs_review
                ORDER BY card_id, ability_index
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    for card_id, ability_index, confidence, raw_text in rows:
        print(f"card={card_id} ability={ability_index} confidence={confidence:.2f} {raw_text}")
    print(f"{len(rows)} effect(s) need review")


if __name__ == "__main__":
    main()
