"""Import prebuilt JSON decks into the training deck database tables."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import psycopg2

DM_ENGINE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DM_ENGINE_ROOT.parent
if str(DM_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(DM_ENGINE_ROOT))

from db.card_database import CardDatabase
from decks.prebuilt import prebuilt_deck_from_dict

logger = logging.getLogger("import_prebuilt_decks")

DEFAULT_DECK_JSON = DM_ENGINE_ROOT / "decks" / "prebuilt_game.json"
DEFAULT_SCHEMA = PROJECT_ROOT / "crawler" / "sql" / "schema.sql"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _apply_schema(dsn: str, schema_path: Path) -> None:
    sql = schema_path.read_text(encoding="utf-8")
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import prebuilt decks for self-play training")
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--deck-json", type=Path, default=DEFAULT_DECK_JSON)
    parser.add_argument("--source", default="prebuilt_game")
    parser.add_argument("--inactive", action="store_true", help="Import decks but mark them inactive")
    parser.add_argument("--apply-schema", action="store_true", help="Apply crawler/sql/schema.sql before importing")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    return parser


def main() -> None:
    _load_env_file(PROJECT_ROOT / "crawler" / ".env")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = _build_parser()
    args = parser.parse_args()
    if not args.dsn:
        parser.error("--dsn is required unless DATABASE_URL is set in crawler/.env")

    if args.apply_schema:
        _apply_schema(args.dsn, args.schema)
        logger.info("Applied schema from %s", args.schema)

    db = CardDatabase(args.dsn)
    db.load()

    data = json.loads(args.deck_json.read_text(encoding="utf-8"))
    players = data.get("players")
    if not isinstance(players, list) or not players:
        raise ValueError("Deck JSON must contain a non-empty 'players' array")

    imported: list[tuple[int, str]] = []
    for player in players:
        if not isinstance(player, dict):
            raise ValueError("Each deck JSON player entry must be an object")
        spec = prebuilt_deck_from_dict(player, db)
        deck_id = db.upsert_training_deck(
            spec,
            source=args.source,
            is_active=not args.inactive,
        )
        imported.append((deck_id, spec.main_deck.name))

    for deck_id, name in imported:
        logger.info("Imported training deck id=%s name=%s", deck_id, name)


if __name__ == "__main__":
    main()
