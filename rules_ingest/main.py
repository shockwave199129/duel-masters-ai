"""
main.py
Single entry point — runs PostgreSQL + ChromaDB ingestion together.

Usage:
    python -m rules_ingest.main \
        --md      path/to/Duel_Masters_rules.md \
        --chroma  ./dm_chroma_db \
        [--openai-key sk-...]

    # PostgreSQL only:
    python -m rules_ingest.main --md ... --no-chroma

    # ChromaDB only:
    python -m rules_ingest.main --md ... --chroma ... --no-postgres
"""

import argparse
import sys
import time
from pathlib import Path

from rules_ingest.db_config import get_database_url
from rules_ingest.parser import parse_rules_md


def main():
    ap = argparse.ArgumentParser(
        description="Ingest Duel Masters rules into PostgreSQL and/or ChromaDB"
    )
    ap.add_argument("--md",          required=True,
                    help="Path to Duel_Masters_rules.md")
    ap.add_argument("--dsn",         default=None,
                    help="PostgreSQL DSN; defaults to DATABASE_URL from .env")
    ap.add_argument("--chroma",      default=None,
                    help="ChromaDB persistence directory path")
    ap.add_argument("--openai-key",  default=None,
                    help="OpenAI API key for embeddings (optional)")
    ap.add_argument("--schema",
                    default=str(Path(__file__).parent / "sql" / "schema.sql"),
                    help="Path to schema.sql")
    ap.add_argument("--no-postgres", action="store_true",
                    help="Skip PostgreSQL ingestion")
    ap.add_argument("--no-chroma",   action="store_true",
                    help="Skip ChromaDB ingestion")
    args = ap.parse_args()
    database_url = get_database_url(args.dsn)

    if args.no_postgres and args.no_chroma:
        print("Nothing to do — both --no-postgres and --no-chroma are set.")
        sys.exit(0)

    if not args.no_postgres and not database_url:
        ap.error("DATABASE_URL is required in .env unless --dsn or --no-postgres is set")

    if not args.no_chroma and not args.chroma:
        ap.error("--chroma is required unless --no-chroma is set")

    # ── Parse ─────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  Duel Masters Rules Ingestion")
    print("═" * 60)

    t0 = time.perf_counter()
    print(f"\n▶ Parsing: {args.md}")
    chapters, sections, rules = parse_rules_md(args.md)

    # stats
    sba_count  = sum(1 for r in rules if r.is_state_based)
    kw_count   = sum(1 for r in rules if r.is_keyword_rule)
    phase_count = sum(1 for r in rules if r.rule_category == "turn_structure")
    print(f"  Chapters        : {len(chapters)}")
    print(f"  Sections        : {len(sections)}")
    print(f"  Rules (total)   : {len(rules)}")
    print(f"    State-based   : {sba_count}")
    print(f"    Keyword rules : {kw_count}")
    print(f"    Turn-structure: {phase_count}")

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    if not args.no_postgres:
        print(f"\n▶ PostgreSQL → {database_url}")
        from rules_ingest.ingest_postgres import (
            ingest_chapters, ingest_sections, ingest_rules,
            ingest_keywords, ingest_phases, ingest_state_based_actions,
            ingest_rule_relations, _run_schema,
        )
        import psycopg2

        conn = psycopg2.connect(database_url)
        conn.autocommit = False
        cur = conn.cursor()
        try:
            _run_schema(cur, Path(args.schema))
            chapter_map = ingest_chapters(cur, chapters)
            section_map = ingest_sections(cur, sections, chapter_map)
            rule_map    = ingest_rules(cur, rules, section_map)
            ingest_keywords(cur, rule_map)
            ingest_phases(cur)
            ingest_state_based_actions(cur, rule_map)
            ingest_rule_relations(cur, rules, rule_map)
            conn.commit()
            print("  ✅ PostgreSQL committed")
        except Exception as e:
            conn.rollback()
            print(f"  ❌ PostgreSQL error — rolled back: {e}")
            raise
        finally:
            cur.close()
            conn.close()

    # ── ChromaDB ──────────────────────────────────────────────────────────────
    if not args.no_chroma:
        print(f"\n▶ ChromaDB  → {args.chroma}")
        from rules_ingest.ingest_chroma import ingest_to_chroma
        ingest_to_chroma(rules, args.chroma, args.openai_key)
        print("  ✅ ChromaDB upsert complete")

    elapsed = time.perf_counter() - t0
    print(f"\n{'═'*60}")
    print(f"  Done in {elapsed:.1f}s")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()
