"""Parse effects only for cards referenced by a prebuilt deck JSON file."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path
from threading import Event
from typing import Any

CRAWLER_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CRAWLER_ROOT.parent
if str(CRAWLER_ROOT) not in sys.path:
    sys.path.insert(0, str(CRAWLER_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.effect_parser import find_missing_card_slugs, parse_cards_by_slugs
from scripts.rules_context import RulesContextConfig

logger = logging.getLogger("parse_prebuilt_decks")
STOP_REQUESTED = Event()

DEFAULT_DECK_JSON = PROJECT_ROOT / "dm_engine" / "decks" / "prebuilt_game.json"
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
DEFAULT_OPENAI_MODEL = "gpt-5-nano"
DEFAULT_OLLAMA_MODEL = "nemotron-3-nano:4b"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_LLM_MAX_TOKENS = 2048
DEFAULT_OPENAI_MAX_TOKENS = 20000


def _handle_signal(sig, frame):
    del sig, frame
    logger.warning("Stop signal received; finishing current batch before exiting")
    STOP_REQUESTED.set()


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _zone_card_keys(value: Any) -> list[str]:
    if value in (None, {}, []):
        return []
    if isinstance(value, dict):
        return [str(key) for key in value.keys()]
    if isinstance(value, list):
        return [str(item) for item in value]
    raise ValueError("Deck zones must be objects or lists of card keys")


def _read_prebuilt_slugs(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    players = data.get("players")
    if not isinstance(players, list):
        raise ValueError("Prebuilt JSON must contain a 'players' list")

    slugs: list[str] = []
    for player in players:
        if not isinstance(player, dict):
            raise ValueError("Each player entry must be an object")
        slugs.extend(_zone_card_keys(player.get("main", player.get("cards", {}))))
        slugs.extend(_zone_card_keys(player.get("hyperspatial", {})))
        slugs.extend(_zone_card_keys(player.get("ultra_gr", {})))
        slugs.extend(_zone_card_keys(player.get("start_battle_zone", [])))

    return list(dict.fromkeys(slugs))


def _default_api_key(provider: str) -> str | None:
    if provider == "openai":
        return os.getenv("OPENAI_API_KEY")
    if provider == "openrouter":
        return os.getenv("OPENROUTER_API_KEY")
    return None


def _default_model(provider: str) -> str:
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    if provider == "ollama":
        return os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    return os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse only unparsed card effects from a prebuilt deck JSON",
    )
    parser.add_argument(
        "--deck-json",
        type=Path,
        default=DEFAULT_DECK_JSON,
        help="Path to prebuilt game JSON",
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv("DATABASE_URL"),
        help="PostgreSQL DSN (defaults to DATABASE_URL)",
    )
    parser.add_argument(
        "--llm-provider",
        choices=["openrouter", "openai", "ollama"],
        default=os.getenv("LLM_PROVIDER", "openrouter"),
        help="LLM provider to use for effect parsing",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="LLM API key (defaults to provider-specific env var)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("LLM_MODEL"),
        help="Model for effect parsing",
    )
    parser.add_argument(
        "--cards-per-call",
        type=int,
        default=int(os.getenv("LLM_CARDS_PER_CALL", "2")),
        help="How many deck cards to parse in one LLM call",
    )
    parser.add_argument(
        "--delay-between",
        type=float,
        default=float(os.getenv("LLM_DELAY_BETWEEN", "2.0")),
        help="Seconds to wait between LLM calls",
    )
    parser.add_argument(
        "--llm-retries",
        type=int,
        default=int(os.getenv("LLM_RETRIES", "5")),
        help="Retries per card batch for retryable provider errors",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.getenv("LLM_MAX_TOKENS", str(DEFAULT_LLM_MAX_TOKENS))),
        help="Maximum output/completion tokens requested from the provider",
    )
    parser.add_argument(
        "--ollama-host",
        default=os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST),
        help="Ollama server URL when --llm-provider=ollama",
    )
    parser.add_argument(
        "--rules-db-dsn",
        default=os.getenv("DM_RULES_DATABASE_URL") or os.getenv("DATABASE_URL"),
        help="PostgreSQL DSN containing dm_rules",
    )
    parser.add_argument(
        "--rules-chroma-path",
        default=os.getenv("DM_RULES_CHROMA_PATH"),
        help="Optional ChromaDB path for semantic rules retrieval",
    )
    parser.add_argument(
        "--rules-embedding-key",
        default=os.getenv("DM_RULES_EMBEDDING_KEY"),
        help="Optional embedding API key for Chroma collections built with OpenAI embeddings",
    )
    parser.add_argument(
        "--no-rule-context",
        action="store_true",
        help="Disable PostgreSQL/Chroma rules context in LLM prompts",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list deck cards missing parsed effects; do not call an LLM",
    )
    return parser


def main() -> None:
    _load_env_file(CRAWLER_ROOT / ".env")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    parser = _build_parser()
    args = parser.parse_args()

    if not args.dsn:
        parser.error("--dsn is required unless DATABASE_URL is set in crawler/.env")
    if args.model is None:
        args.model = _default_model(args.llm_provider)
    if args.api_key is None:
        args.api_key = _default_api_key(args.llm_provider)
    if (
        args.llm_provider == "openai"
        and args.max_tokens == DEFAULT_LLM_MAX_TOKENS
        and not os.getenv("LLM_MAX_TOKENS")
    ):
        args.max_tokens = DEFAULT_OPENAI_MAX_TOKENS

    slugs = _read_prebuilt_slugs(args.deck_json)
    missing = find_missing_card_slugs(args.dsn, slugs)
    if missing:
        raise SystemExit(f"Deck JSON contains card slugs not found in DB: {missing}")

    logger.info("Deck JSON contains %s unique card slugs", len(slugs))
    if args.dry_run:
        logger.info("Dry run complete; no parsing was performed")
        return

    counts = parse_cards_by_slugs(
        dsn=args.dsn,
        slugs=slugs,
        api_key=args.api_key,
        cards_per_call=args.cards_per_call,
        delay_between=args.delay_between,
        model=args.model,
        provider=args.llm_provider,
        ollama_host=args.ollama_host,
        rules_context_config=None
        if args.no_rule_context
        else RulesContextConfig(
            postgres_dsn=args.rules_db_dsn,
            chroma_path=args.rules_chroma_path,
            embedding_key=args.rules_embedding_key,
        ),
        retries=args.llm_retries,
        max_tokens=args.max_tokens,
        should_stop=STOP_REQUESTED.is_set,
    )
    logger.info(
        "Prebuilt deck parsing done: %s parsed, %s already parsed, %s skipped, %s errors, "
        "tokens prompt=%s completion=%s total=%s",
        counts["parsed"],
        counts["already_parsed"],
        counts["skipped"],
        counts["errors"],
        counts["prompt_tokens"],
        counts["completion_tokens"],
        counts["total_tokens"],
    )


if __name__ == "__main__":
    main()
