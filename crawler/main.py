#!/usr/bin/env python3
"""
dm_scraper/main.py — Resumable 3-level Duel Masters card scraper pipeline.

Pipeline levels:
  Level 1 — Crawl set-list pages → discover all set URLs
  Level 2 — Crawl each set page  → discover card URLs for that set
  Level 3 — Scrape each card URL → parse + persist card data
  Level 4 — LLM parse            → convert raw abilities to structured effects

All state is persisted in PostgreSQL + a local JSON checkpoint so the pipeline
can be stopped and resumed at any point without re-doing completed work.

Usage examples:

  # Reads DATABASE_URL, OPENROUTER_API_KEY, OPENROUTER_MODEL,
  # DM_RULES_DATABASE_URL, and DM_RULES_CHROMA_PATH from crawler/.env

  # Full pipeline from scratch (OCG + TCG)
  python main.py run \\
    --series both

  # Discover sets only (no scraping yet)
  python main.py discover-sets \\
    --series both

  # Discover card URLs from sets already in DB
  python main.py discover-cards

  # Scrape card data for all discovered card URLs
  python main.py scrape-cards

  # Run LLM effect parsing on all scraped cards
  python main.py parse-effects \\
    --batch-size 100

  # Scrape a single card (test)
  python main.py single \\
    --url "https://duelmasters.fandom.com/wiki/Bolshack_Dragon"

  # Show current pipeline status
  python main.py status

  # Reset error cards so they can be retried
  python main.py retry-errors
"""

import argparse
import logging
import os
import signal
import sys
import time
import random
from curl_cffi import requests
from pathlib import Path

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.state_manager import StateManager
from scripts.sets_crawler import crawl_sets_list
from scripts.set_page_crawler import crawl_set_page
from scripts.scraper import scrape_card
from scripts.effect_parser import parse_pending_cards
from scripts.rules_context import RulesContextConfig
from scripts.cf_cookies import apply_cf_cookies, close_browser_context
from scripts.repair_cards import repair_cards_from_raw_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("dm_scraper")

# ── Graceful shutdown ──────────────────────────────────────────────────────────

_STOP_REQUESTED = False


def _handle_signal(sig, frame):
    global _STOP_REQUESTED
    logger.warning(
        "Stop signal received — finishing current card then saving state...")
    _STOP_REQUESTED = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_env_file(path: Path):
    """Load KEY=VALUE pairs without overwriting existing environment variables."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _polite_delay(min_s=1.5, max_s=3.5):
    if _STOP_REQUESTED:
        return
    time.sleep(random.uniform(min_s, max_s))


def _make_session() -> requests.Session:
    s = requests.Session(impersonate="chrome124")
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    })
    apply_cf_cookies(s)
    return s


def _init_db(dsn: str):
    """Create tables if they don't exist."""
    schema_path = Path(__file__).parent / "sql" / "schema.sql"
    if not schema_path.exists():
        logger.error(f"Schema file not found: {schema_path}")
        sys.exit(1)
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(schema_path.read_text())
        conn.commit()
        logger.info("Database schema ready")
    finally:
        conn.close()


# ── Pipeline stages ────────────────────────────────────────────────────────────

def stage_discover_sets(sm: StateManager, series: str):
    """Level 1: Crawl set-list pages and persist all set URLs."""
    logger.info("=" * 60)
    logger.info("STAGE 1: Discovering sets")
    logger.info("=" * 60)

    session = _make_session()
    sets = crawl_sets_list(series=series, session=session)
    if not sets:
        logger.error("No sets discovered — check network and wiki structure")
        return

    sm.add_sets(sets)
    sm.save()
    logger.info(f"Discovered and saved {len(sets)} sets")


def stage_discover_cards(sm: StateManager):
    """Level 2: For each pending set, crawl its set page and collect card URLs."""
    pending = sm.pending_sets()
    if not pending:
        logger.info("No pending sets to discover cards from")
        return

    logger.info("=" * 60)
    logger.info(f"STAGE 2: Discovering card URLs from {len(pending)} sets")
    logger.info("=" * 60)

    session = _make_session()
    done_count = 0

    for set_state in pending:
        if _STOP_REQUESTED:
            break

        logger.info(
            f"  [{done_count+1}/{len(pending)}] {set_state.set_code} — {set_state.set_name}")

        try:
            card_dicts = crawl_set_page(
                set_url=set_state.set_url,
                set_code=set_state.set_code,
                session=session,
            )

            if not card_dicts:
                logger.warning(
                    f"  No card URLs found for {set_state.set_code}")
                sm.mark_set_error(set_state.set_code, "no cards found")
            else:
                sm.add_card_urls(card_dicts)
                sm.mark_set_discovered(set_state.set_code, len(card_dicts))
                logger.info(
                    f"  ✓ {set_state.set_code}: {len(card_dicts)} card URLs added")

        except Exception as e:
            logger.error(f"  ✗ Error crawling set {set_state.set_code}: {e}")
            sm.mark_set_error(set_state.set_code, str(e))

        sm.save()
        done_count += 1
        _polite_delay(2, 5)

    logger.info(
        f"Set discovery done. Total card URLs: {len(sm.pending_cards()) + sm._state.scraped_cards}")


def stage_scrape_cards(sm: StateManager, dsn: str):
    """Level 3: Scrape each pending card URL and persist to DB."""
    pending = sm.pending_cards()
    if not pending:
        logger.info("No pending card URLs to scrape")
        return

    logger.info("=" * 60)
    logger.info(f"STAGE 3: Scraping {len(pending)} card pages")
    logger.info("=" * 60)

    session = _make_session()
    done = 0
    errors = 0

    try:
        for card_state in pending:
            if _STOP_REQUESTED:
                break

            url = card_state.url
            set_code = card_state.set_code

            logger.info(
                f"  [{done+errors+1}/{len(pending)}] {card_state.card_name or url}")

            try:
                card = scrape_card(url=url, set_code=set_code,
                                   dsn=dsn, session=session)
                if not card:
                    raise ValueError("Card scrape returned None")

                sm.mark_card_scraped(url)
                done += 1
                logger.info(f"  ✓ {card.name}")

            except Exception as e:
                logger.error(f"  ✗ {url}: {e}")
                sm.mark_card_error(url, str(e))
                errors += 1

            # Checkpoint every 50 cards
            if (done + errors) % 50 == 0:
                sm.save()

            _polite_delay(1.5, 3.5)

    finally:
        close_browser_context()
        sm.save()

    logger.info(f"Scraping done. ✓ {done} scraped, ✗ {errors} errors")


def stage_parse_effects(
    sm: StateManager,
    dsn: str,
    api_key: str,
    batch_size: int,
    cards_per_call: int,
    model: str,
    base_url: str | None,
    llm_provider: str,
    ollama_host: str,
    rules_db_dsn: str | None,
    rules_chroma_path: str | None,
    rules_embedding_key: str | None,
    llm_retries: int,
    delay_between: float,
    max_tokens: int,
):
    """Level 4: LLM-parse abilities for all scraped cards."""
    logger.info("=" * 60)
    logger.info("STAGE 4: LLM effect parsing")
    logger.info("=" * 60)

    counts = parse_pending_cards(
        dsn=dsn,
        api_key=api_key,
        batch_size=batch_size,
        cards_per_call=cards_per_call,
        delay_between=delay_between,
        model=model,
        base_url=base_url,
        provider=llm_provider,
        ollama_host=ollama_host,
        rules_context_config=RulesContextConfig(
            postgres_dsn=rules_db_dsn,
            chroma_path=rules_chroma_path,
            embedding_key=rules_embedding_key,
        ),
        retries=llm_retries,
        max_tokens=max_tokens,
        should_stop=lambda: _STOP_REQUESTED,
    )
    logger.info(
        f"Effect parsing done: "
        f"✓ {counts['parsed']} parsed, "
        f"⚡ {counts['skipped']} skipped (no abilities), "
        f"✗ {counts['errors']} errors, "
        f"tokens prompt={counts.get('prompt_tokens', 0)} "
        f"completion={counts.get('completion_tokens', 0)} "
        f"total={counts.get('total_tokens', 0)}"
    )


# ── Sub-commands ───────────────────────────────────────────────────────────────

def cmd_run(args):
    """Full pipeline: discover sets → discover cards → scrape → parse effects."""
    _init_db(args.dsn)
    sm = StateManager(dsn=args.dsn, state_dir=args.state_dir)
    sm.load()

    if not sm._state.sets:
        stage_discover_sets(sm, args.series)

    if not _STOP_REQUESTED:
        stage_discover_cards(sm)

    if not _STOP_REQUESTED:
        stage_scrape_cards(sm, args.dsn)

    if not _STOP_REQUESTED and (args.llm_provider == "ollama" or args.api_key):
        stage_parse_effects(
            sm, args.dsn, args.api_key, args.batch_size, args.cards_per_call, args.model,
            args.base_url, args.llm_provider, args.ollama_host,
            None if args.no_rule_context else args.rules_db_dsn,
            None if args.no_rule_context else args.rules_chroma_path,
            None if args.no_rule_context else args.rules_embedding_key,
            args.llm_retries,
            args.delay_between,
            args.max_tokens,
        )
    elif not args.api_key:
        logger.info("No --api-key provided, skipping effect parsing")

    sm.print_summary()


def cmd_discover_sets(args):
    _init_db(args.dsn)
    sm = StateManager(dsn=args.dsn, state_dir=args.state_dir)
    sm.load()
    stage_discover_sets(sm, args.series)
    sm.print_summary()


def cmd_discover_cards(args):
    _init_db(args.dsn)
    sm = StateManager(dsn=args.dsn, state_dir=args.state_dir)
    sm.load()
    stage_discover_cards(sm)
    sm.print_summary()


def cmd_scrape_cards(args):
    _init_db(args.dsn)
    sm = StateManager(dsn=args.dsn, state_dir=args.state_dir)
    sm.load()
    stage_scrape_cards(sm, args.dsn)
    sm.print_summary()


def cmd_parse_effects(args):
    if args.llm_provider in ("openrouter", "openai") and not args.api_key:
        logger.error("--api-key required for parse-effects when --llm-provider=%s", args.llm_provider)
        sys.exit(1)
    sm = StateManager(dsn=args.dsn, state_dir=args.state_dir)
    sm.load()
    stage_parse_effects(
        sm, args.dsn, args.api_key, args.batch_size, args.cards_per_call, args.model,
        args.base_url, args.llm_provider, args.ollama_host,
        None if args.no_rule_context else args.rules_db_dsn,
        None if args.no_rule_context else args.rules_chroma_path,
        None if args.no_rule_context else args.rules_embedding_key,
        args.llm_retries,
        args.delay_between,
        args.max_tokens,
    )
    sm.print_summary()


def cmd_single(args):
    """Scrape and optionally parse a single card (for testing)."""
    _init_db(args.dsn)
    session = _make_session()

    card = scrape_card(
        url=args.url,
        set_code=args.set_code or "UNKNOWN",
        dsn=args.dsn,
        session=session,
    )
    if not card:
        logger.error("Could not scrape card page")
        sys.exit(1)

    logger.info(f"Parsed card: {card.name}")
    logger.info(f"  Type: {card.card_type} ({card.card_subtype})")
    logger.info(f"  Cost: {card.cost}, Power: {card.power}")
    logger.info(f"  Civs: {card.civilizations}")
    logger.info(f"  Races: {card.races}")
    logger.info(f"  Abilities ({len(card.abilities)}):")
    for ab in card.abilities:
        logger.info(f"    {ab}")

    if (args.llm_provider == "ollama" or args.api_key) and card.abilities:
        from scripts.effect_parser import _parse_with_llm
        from openai import OpenAI
        from openrouter import OpenRouter
        rules_context = ""
        if not args.no_rule_context:
            from scripts.rules_context import build_rules_context
            rules_context = build_rules_context(
                card_name=card.name,
                card_type=card.card_type,
                abilities=[{"raw_text": ability} for ability in card.abilities],
                config=RulesContextConfig(
                    postgres_dsn=args.rules_db_dsn,
                    chroma_path=args.rules_chroma_path,
                    embedding_key=args.rules_embedding_key,
                ),
            )
        if args.llm_provider == "openrouter":
            client_context = OpenRouter(api_key=args.api_key)
        elif args.llm_provider == "openai":
            class _OpenAIClient:
                def __enter__(self):
                    return OpenAI(api_key=args.api_key)
                def __exit__(self, exc_type, exc, traceback):
                    return False
            client_context = _OpenAIClient()
        else:
            client_context = None
        if client_context is None:
            class _NoClient:
                def __enter__(self):
                    return None
                def __exit__(self, exc_type, exc, traceback):
                    return False
            client_context = _NoClient()

        with client_context as client:
            parsed_result = _parse_with_llm(
                card.name,
                card.card_type,
                [{"raw_text": ability} for ability in card.abilities],
                client,
                model=args.model,
                provider=args.llm_provider,
                ollama_host=args.ollama_host,
                rules_context=rules_context,
                max_tokens=args.max_tokens,
            )
        if parsed_result:
            effects, usage = parsed_result
            import json
            logger.info(f"  LLM effects ({len(effects)}):")
            print(json.dumps(effects, indent=2))
            logger.info(
                "  Token usage: prompt=%s completion=%s total=%s",
                usage["prompt_tokens"],
                usage["completion_tokens"],
                usage["total_tokens"],
            )


def cmd_status(args):
    sm = StateManager(dsn=args.dsn, state_dir=args.state_dir)
    sm.load()
    sm.print_summary()

    # Extra detail
    pending_sets = sm.pending_sets()
    if pending_sets:
        print(f"  Pending sets ({len(pending_sets)}):")
        for s in pending_sets[:10]:
            print(f"    {s.set_code}: {s.status}")
        if len(pending_sets) > 10:
            print(f"    ... and {len(pending_sets)-10} more")

    pending_cards = sm.pending_cards()
    if pending_cards:
        print(f"\n  Next pending cards ({min(5, len(pending_cards))}):")
        for c in pending_cards[:5]:
            print(f"    {c.card_name or c.url}")


def cmd_retry_errors(args):
    """Reset error status cards/sets so they are retried on next run."""
    sm = StateManager(dsn=args.dsn, state_dir=args.state_dir)
    sm.load()

    reset_cards = 0
    reset_sets = 0

    conn = psycopg2.connect(args.dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE card_urls SET status='pending' WHERE status='error'")
            reset_cards = cur.rowcount
            cur.execute(
                "UPDATE card_sets SET scraped_at=NULL WHERE scraped_at IS NULL")
        conn.commit()
    finally:
        conn.close()

    for s in sm._state.sets.values():
        if s.status == "error":
            s.status = "pending"
            reset_sets += 1

    for c in sm._state.card_urls.values():
        if c.status == "error":
            c.status = "pending"
            reset_cards += 1

    sm.save()
    print(
        f"Reset {reset_sets} error sets and {reset_cards} error card URLs to pending")


def cmd_repair_cards(args):
    """Repair existing cards table rows from old raw_text dict values."""
    counts = repair_cards_from_raw_text(
        dsn=args.dsn,
        dry_run=args.dry_run,
        delete_unrepairable=args.delete_unrepairable,
    )
    mode = "would update" if args.dry_run else "updated"
    print(
        f"Scanned {counts['scanned']} cards; "
        f"{mode} {counts['repairable'] if args.dry_run else counts['updated']} rows"
    )
    json_mode = "would convert" if args.dry_run else "converted"
    json_count = (
        counts["raw_text_jsonable"]
        if args.dry_run
        else counts["raw_text_jsonified"]
    )
    print(f"{json_mode.capitalize()} {json_count} raw_text values to JSON")
    if args.delete_unrepairable:
        delete_mode = "would delete" if args.dry_run else "deleted"
        delete_count = counts["deleteable"] if args.dry_run else counts["deleted"]
        print(f"{delete_mode.capitalize()} {delete_count} unrepairable rows")


def cmd_reset_card_data(args):
    """Delete scraped card data and reset discovered URLs to pending."""
    sm = StateManager(dsn=args.dsn, state_dir=args.state_dir)
    sm.load()

    if not args.apply:
        print("Dry run: would TRUNCATE cards CASCADE and reset all card_urls to pending")
        print(f"Card URLs in state: {len(sm._state.card_urls)}")
        print("Run again with --apply to perform the reset")
        return

    conn = psycopg2.connect(args.dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE cards CASCADE")
            cur.execute(
                """
                UPDATE card_urls
                SET status = 'pending', scraped_at = NULL, parsed_at = NULL
                """
            )
            reset_urls = cur.rowcount
        conn.commit()
    finally:
        conn.close()

    for card_state in sm._state.card_urls.values():
        card_state.status = "pending"
        card_state.error_msg = ""
    sm.save()
    print(f"Deleted card data and reset {reset_urls} card URLs to pending")


# ── CLI ────────────────────────────────────────────────────────────────────────

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
DEFAULT_OPENAI_MODEL = "gpt-5-nano"
DEFAULT_LLM_MAX_TOKENS = 2048
DEFAULT_OPENAI_MAX_TOKENS = 50000
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "nemotron-3-nano:4b"


def _add_llm_parse_args(subparser):
    subparser.add_argument(
        "--llm-provider",
        choices=["openrouter", "openai", "ollama"],
        default=os.getenv("LLM_PROVIDER", "openrouter"),
        help="LLM provider to use for effect parsing",
    )
    subparser.add_argument(
        "--api-key",
        default=None,
        help="LLM API key (defaults to provider-specific env var)",
    )
    subparser.add_argument(
        "--base-url",
        default=os.getenv("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL),
        help="OpenAI-compatible API base URL",
    )
    subparser.add_argument(
        "--model",
        default=os.getenv("LLM_MODEL") or os.getenv("OPENROUTER_MODEL") or DEFAULT_OPENROUTER_MODEL,
        help="Model for effect parsing",
    )
    subparser.add_argument(
        "--ollama-host",
        default=os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST),
        help="Ollama server URL when --llm-provider=ollama",
    )
    subparser.add_argument(
        "--rules-db-dsn",
        default=os.getenv("DM_RULES_DATABASE_URL") or os.getenv("DATABASE_URL"),
        help="PostgreSQL DSN containing dm_rules (defaults to DM_RULES_DATABASE_URL, then DATABASE_URL)",
    )
    subparser.add_argument(
        "--rules-chroma-path",
        default=os.getenv("DM_RULES_CHROMA_PATH"),
        help="Optional ChromaDB path for semantic rules retrieval",
    )
    subparser.add_argument(
        "--rules-embedding-key",
        default=os.getenv("DM_RULES_EMBEDDING_KEY"),
        help="Optional embedding API key only for Chroma collections built with OpenAI embeddings",
    )
    subparser.add_argument(
        "--no-rule-context",
        action="store_true",
        help="Disable PostgreSQL/Chroma rules context in LLM prompts",
    )
    subparser.add_argument(
        "--llm-retries",
        type=int,
        default=int(os.getenv("LLM_RETRIES", "5")),
        help="Retries per card for retryable OpenRouter/provider errors",
    )
    subparser.add_argument(
        "--delay-between",
        type=float,
        default=float(os.getenv("LLM_DELAY_BETWEEN", "2.0")),
        help="Seconds to wait between LLM calls",
    )
    subparser.add_argument(
        "--cards-per-call",
        type=int,
        default=int(os.getenv("LLM_CARDS_PER_CALL", "2")),
        help="How many cards to parse in one OpenRouter request",
    )
    subparser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.getenv("LLM_MAX_TOKENS", str(DEFAULT_LLM_MAX_TOKENS))),
        help="Maximum output/completion tokens requested from the LLM provider",
    )


def main():
    _load_env_file(Path(__file__).parent / ".env")

    parser = argparse.ArgumentParser(
        description="Duel Masters wiki scraper — resumable 3-level pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--dsn", default=os.getenv("DATABASE_URL"),
                        help="PostgreSQL DSN (defaults to DATABASE_URL)")
    shared.add_argument("--state-dir", default="./state",
                        help="Directory for JSON checkpoint files")

    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser(
        "run", parents=[shared], help="Full pipeline (discover → scrape → parse)")
    p_run.add_argument("--series", default="both",
                       choices=["OCG", "TCG", "both"])
    p_run.add_argument("--batch-size", type=int, default=100)
    _add_llm_parse_args(p_run)

    # discover-sets
    p_ds = sub.add_parser(
        "discover-sets", parents=[shared], help="Level 1: find all sets")
    p_ds.add_argument("--series", default="OCG",
                      choices=["OCG", "TCG", "both"])

    # discover-cards
    sub.add_parser("discover-cards",
                   parents=[shared], help="Level 2: find card URLs from sets")

    # scrape-cards
    sub.add_parser(
        "scrape-cards", parents=[shared], help="Level 3: scrape all pending card pages")

    # parse-effects
    p_pe = sub.add_parser(
        "parse-effects", parents=[shared], help="Level 4: LLM parse abilities")
    p_pe.add_argument("--batch-size", type=int, default=100)
    _add_llm_parse_args(p_pe)

    # single
    p_single = sub.add_parser(
        "single", parents=[shared], help="Test scrape a single card URL")
    p_single.add_argument("--url", required=True)
    p_single.add_argument("--set-code", default="UNKNOWN")
    _add_llm_parse_args(p_single)

    # status
    sub.add_parser("status", parents=[shared], help="Show pipeline progress")

    # retry-errors
    sub.add_parser(
        "retry-errors", parents=[shared], help="Reset error cards to pending")

    # repair-cards
    p_repair = sub.add_parser(
        "repair-cards",
        parents=[shared],
        help="Repair existing cards from old raw_text dict values",
    )
    p_repair.add_argument(
        "--apply",
        action="store_false",
        dest="dry_run",
        help="Write repairs to the database; default is dry-run",
    )
    p_repair.add_argument(
        "--delete-unrepairable",
        action="store_true",
        help="Delete cards that still have NULL cost and unknown card type after repair",
    )
    p_repair.set_defaults(dry_run=True)

    # reset-card-data
    p_reset_cards = sub.add_parser(
        "reset-card-data",
        parents=[shared],
        help="Delete cards/effects/relations and reset discovered card URLs to pending",
    )
    p_reset_cards.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the destructive reset; default is dry-run",
    )

    args = parser.parse_args()
    if hasattr(args, "llm_provider") and not args.api_key:
        if args.llm_provider == "openai":
            args.api_key = os.getenv("OPENAI_API_KEY")
        elif args.llm_provider == "openrouter":
            args.api_key = os.getenv("OPENROUTER_API_KEY")
    openrouter_default_model = os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
    if (
        hasattr(args, "llm_provider")
        and args.llm_provider == "ollama"
        and args.model == openrouter_default_model
        and not os.getenv("LLM_MODEL")
    ):
        args.model = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    if (
        hasattr(args, "llm_provider")
        and args.llm_provider == "openai"
        and args.model == openrouter_default_model
        and not os.getenv("LLM_MODEL")
    ):
        args.model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    if (
        hasattr(args, "llm_provider")
        and args.llm_provider == "openai"
        and args.max_tokens == DEFAULT_LLM_MAX_TOKENS
        and not os.getenv("LLM_MAX_TOKENS")
    ):
        args.max_tokens = DEFAULT_OPENAI_MAX_TOKENS

    if not args.dsn:
        parser.error(
            "--dsn is required unless DATABASE_URL is set in crawler/.env")

    commands = {
        "run": cmd_run,
        "discover-sets": cmd_discover_sets,
        "discover-cards": cmd_discover_cards,
        "scrape-cards": cmd_scrape_cards,
        "parse-effects": cmd_parse_effects,
        "single": cmd_single,
        "status": cmd_status,
        "retry-errors": cmd_retry_errors,
        "repair-cards": cmd_repair_cards,
        "reset-card-data": cmd_reset_card_data,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
