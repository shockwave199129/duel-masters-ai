"""Build fine-tuning datasets from already parsed card_effects rows."""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CRAWLER_ROOT = PROJECT_ROOT / "crawler"
if str(CRAWLER_ROOT) not in sys.path:
    sys.path.insert(0, str(CRAWLER_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_PARSED_AFTER = "2026-06-05 00:00:00"
DEFAULT_TRAIN_OUTPUT = "dm_train"
DEFAULT_VAL_OUTPUT = "dm_val"
DEFAULT_MIN_CONFIDENCE = 0.70


SYSTEM_PROMPT = (
    "You are an expert Duel Masters card game rules engine parser. "
    "Given a card's raw ability text lines, output only valid JSON with an "
    "'effects' array of structured card_effects rows."
)


def _dataset_from_list(examples: list[dict[str, Any]]):
    datasets_module = import_module("datasets")
    return datasets_module.Dataset.from_list(examples)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


def _fetch_parsed_cards(
    dsn: str,
    *,
    parsed_after: str,
    limit: int | None,
    min_effects: int,
    min_confidence: float,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            c.id,
            c.name,
            c.slug,
            c.card_type,
            c.card_subtype,
            c.cost,
            c.power,
            c.raw_text,
            c.is_multiface,
            cu.url,
            cu.parsed_at,
            COALESCE(civs.civilizations, ARRAY[]::text[]) AS civilizations,
            COALESCE(races.races, ARRAY[]::text[]) AS races,
            COALESCE(keywords.keywords, ARRAY[]::text[]) AS keywords,
            effects.effects
        FROM card_urls cu
        JOIN cards c ON c.source_url = cu.url
        LEFT JOIN LATERAL (
            SELECT array_agg(cc.civilization ORDER BY cc.civilization) AS civilizations
            FROM card_civilizations cc
            WHERE cc.card_id = c.id
        ) civs ON TRUE
        LEFT JOIN LATERAL (
            SELECT array_agg(cr.race ORDER BY cr.race) AS races
            FROM card_races cr
            WHERE cr.card_id = c.id
        ) races ON TRUE
        LEFT JOIN LATERAL (
            SELECT array_agg(ck.keyword ORDER BY ck.keyword) AS keywords
            FROM card_keywords ck
            WHERE ck.card_id = c.id
        ) keywords ON TRUE
        JOIN LATERAL (
            SELECT jsonb_agg(
                jsonb_build_object(
                    'ability_index', ce.ability_index,
                    'raw_text', ce.raw_text,
                    'effect_type', ce.effect_type,
                    'trigger_event', ce.trigger_event,
                    'trigger_condition', ce.trigger_condition,
                    'effect_action', ce.effect_action,
                    'effect_target', ce.effect_target,
                    'effect_value', ce.effect_value,
                    'is_optional', ce.is_optional,
                    'is_replacement', ce.is_replacement,
                    'active_in_phase', ce.active_in_phase,
                    'active_in_zone', ce.active_in_zone,
                    'parse_confidence', ce.parse_confidence,
                    'face_index', ce.face_index,
                    'face_name', ce.face_name
                )
                ORDER BY COALESCE(ce.face_index, 0), ce.ability_index
            ) AS effects,
            count(*) AS effect_count,
            min(COALESCE(ce.parse_confidence, 0.0)) AS min_parse_confidence
            FROM card_effects ce
            WHERE ce.card_id = c.id
        ) effects ON effects.effect_count >= %s
                 AND effects.min_parse_confidence >= %s
        WHERE cu.parsed_at::timestamp > %s::timestamp
          AND c.raw_text IS NOT NULL
          AND btrim(c.raw_text) <> ''
        ORDER BY cu.parsed_at, c.id
    """
    params: list[Any] = [min_effects, min_confidence, parsed_after]
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _compact_effect(effect: dict[str, Any], *, include_confidence: bool) -> dict[str, Any]:
    keys = [
        "ability_index",
        "raw_text",
        "effect_type",
        "trigger_event",
        "trigger_condition",
        "effect_action",
        "effect_target",
        "effect_value",
        "is_optional",
        "is_replacement",
        "active_in_phase",
        "active_in_zone",
        "face_index",
        "face_name",
    ]
    if include_confidence:
        keys.append("parse_confidence")
    return {
        key: _json_safe(effect.get(key))
        for key in keys
        if effect.get(key) is not None
    }


def format_card_to_training_example(
    card: dict[str, Any],
    *,
    include_confidence: bool = False,
) -> dict[str, Any]:
    """Convert a parsed DB card row to a ChatML training example."""
    effects = [
        _compact_effect(dict(effect), include_confidence=include_confidence)
        for effect in (card.get("effects") or [])
    ]
    user_content = f"""Parse this Duel Masters card into structured card_effects JSON.

Name: {card.get('name', '')}
Slug: {card.get('slug', '')}
Civilizations: {', '.join(card.get('civilizations') or [])}
Card Type: {card.get('card_type') or ''}
Subtype: {card.get('card_subtype') or ''}
Mana Cost: {card.get('cost') if card.get('cost') is not None else ''}
Races: {' / '.join(card.get('races') or [])}
Power: {card.get('power') or 'N/A'}
Keywords: {', '.join(card.get('keywords') or [])}
Raw Text:
{card.get('raw_text') or ''}
"""
    assistant_content = json.dumps(
        {"effects": effects}, ensure_ascii=False, separators=(",", ":"))
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ]
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare parser fine-tuning data from parsed card_effects rows")
    parser.add_argument(
        "--dsn", default=os.getenv("DATABASE_URL"), help="PostgreSQL DSN")
    parser.add_argument("--parsed-after", default=DEFAULT_PARSED_AFTER)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-effects", type=int, default=1)
    parser.add_argument("--min-confidence", type=float,
                        default=DEFAULT_MIN_CONFIDENCE)
    parser.add_argument("--include-confidence", action="store_true",
                        help="Include parse_confidence in assistant targets")
    parser.add_argument("--val-ratio", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--train-output", default=DEFAULT_TRAIN_OUTPUT)
    parser.add_argument("--val-output", default=DEFAULT_VAL_OUTPUT)
    parser.add_argument("--jsonl-output", type=Path, default=None,
                        help="Optional debug JSONL copy of examples")
    return parser


def main() -> None:
    _load_env_file(CRAWLER_ROOT / ".env")
    args = _build_parser().parse_args()
    if not args.dsn:
        raise SystemExit(
            "--dsn is required unless DATABASE_URL is set in crawler/.env")
    if not 0.0 < args.val_ratio < 1.0:
        raise SystemExit("--val-ratio must be between 0 and 1")
    if not 0.0 <= args.min_confidence <= 1.0:
        raise SystemExit("--min-confidence must be between 0 and 1")

    cards = _fetch_parsed_cards(
        args.dsn,
        parsed_after=args.parsed_after,
        limit=args.limit,
        min_effects=args.min_effects,
        min_confidence=args.min_confidence,
    )
    examples = [
        format_card_to_training_example(
            card, include_confidence=args.include_confidence)
        for card in cards
    ]
    rng = random.Random(args.seed)
    rng.shuffle(examples)

    split = max(1, int(len(examples) * (1.0 - args.val_ratio))
                ) if examples else 0
    if split >= len(examples) and len(examples) > 1:
        split = len(examples) - 1

    train_ds = _dataset_from_list(examples[:split])
    val_ds = _dataset_from_list(examples[split:])

    train_ds.save_to_disk(args.train_output)
    val_ds.save_to_disk(args.val_output)

    if args.jsonl_output is not None:
        args.jsonl_output.parent.mkdir(parents=True, exist_ok=True)
        with args.jsonl_output.open("w", encoding="utf-8") as f:
            for example in examples:
                f.write(json.dumps(example, ensure_ascii=False) + "\n")

    print(
        f"Fetched cards: {len(cards)} | Train: {len(train_ds)} | Val: {len(val_ds)} "
        f"| parsed_after: {args.parsed_after} | min_confidence: {args.min_confidence}"
    )


if __name__ == "__main__":
    main()
