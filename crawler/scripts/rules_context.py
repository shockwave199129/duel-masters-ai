"""
rules_context.py — retrieve Duel Masters rules for effect parsing prompts.

This module is optional support for the LLM parser. It never decides gameplay;
it only supplies rule text so the model can produce better structured effects.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass(frozen=True)
class RulesContextConfig:
    postgres_dsn: str | None = None
    chroma_path: str | None = None
    embedding_key: str | None = None
    max_semantic_rules: int = 8


EXACT_RULE_REFS = (
    "101.2",
    "101.3",
    "101.4",
    "110.3",
    "110.4",
    "110.5",
    "112.2a",
    "112.3a",
    "112.3b",
    "112.3c",
    "112.3e",
    "112.3g",
    "113.6",
    "509.5a",
    "509.5b",
    "509.5c",
    "603.3",
    "605.2c",
)


def build_rules_context(
    card_name: str,
    card_type: str,
    abilities: list[dict],
    config: RulesContextConfig | None,
) -> str:
    """Return a compact rules block for the LLM prompt."""
    if config is None:
        return ""

    lines: list[str] = []
    exact_rules = _fetch_exact_rules(config.postgres_dsn, EXACT_RULE_REFS)
    if exact_rules:
        lines.append("Exact rules likely relevant to card-effect parsing:")
        lines.extend(_format_rule_rows(exact_rules))

    query = _semantic_query(card_name, card_type, abilities)
    semantic_rules = _fetch_semantic_rules(config, query)
    if semantic_rules:
        lines.append("")
        lines.append("Semantic rules retrieved for this specific card text:")
        lines.extend(_format_rule_rows(semantic_rules))

    if not lines:
        return ""

    return "\n".join(lines)


def _semantic_query(card_name: str, card_type: str, abilities: list[dict]) -> str:
    ability_text = " ".join(str(a.get("raw_text", "")) for a in abilities)
    return f"{card_name} {card_type} card effect parsing rules: {ability_text}"


def _fetch_exact_rules(dsn: str | None, rule_numbers: Iterable[str]) -> list[dict]:
    if not dsn:
        return []
    try:
        conn = psycopg2.connect(dsn)
    except psycopg2.Error as exc:
        logger.warning("Could not connect to rules PostgreSQL for context: %s", exc)
        return []

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT rule_number, text, rule_category, applies_in_phase, applies_in_zone
                FROM dm_rules
                WHERE rule_number = ANY(%s)
                ORDER BY priority, rule_number
                """,
                (list(rule_numbers),),
            )
            return list(cur.fetchall())
    except psycopg2.Error as exc:
        logger.warning("Could not load exact rules from PostgreSQL: %s", exc)
        return []
    finally:
        conn.close()


def _fetch_semantic_rules(config: RulesContextConfig, query: str) -> list[dict]:
    if not config.chroma_path:
        return []
    try:
        from rules_ingest.ingest_chroma import DMRulesRetriever
    except ImportError as exc:
        logger.warning("Could not load semantic rules from ChromaDB: %s", exc)
        return []

    try:
        retriever = DMRulesRetriever(config.chroma_path, config.embedding_key)
        return retriever.search(query, n=config.max_semantic_rules)
    except ValueError as exc:
        if config.embedding_key and "embedding function" in str(exc).lower():
            logger.warning(
                "ChromaDB embedding function conflict; retrying semantic rules with default embeddings"
            )
            try:
                retriever = DMRulesRetriever(config.chroma_path, None)
                return retriever.search(query, n=config.max_semantic_rules)
            except (RuntimeError, ValueError, OSError) as retry_exc:
                logger.warning("Could not load semantic rules from ChromaDB: %s", retry_exc)
                return []
        logger.warning("Could not load semantic rules from ChromaDB: %s", exc)
        return []
    except (RuntimeError, OSError) as exc:
        logger.warning("Could not load semantic rules from ChromaDB: %s", exc)
        return []


def _format_rule_rows(rows: list[dict]) -> list[str]:
    formatted: list[str] = []
    seen: set[str] = set()
    for row in rows:
        rule_number = str(row.get("rule_number", ""))
        if rule_number in seen:
            continue
        seen.add(rule_number)

        text = row.get("text", "")
        if isinstance(text, str) and text.startswith("[Rule "):
            formatted.append(f"- {text}")
        else:
            formatted.append(f"- [Rule {rule_number}] {text}")
    return formatted
