"""Run neural-vs-neural self-play and write decision rows to JSONL."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

DM_ENGINE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DM_ENGINE_ROOT.parent
if str(DM_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(DM_ENGINE_ROOT))

from db.card_database import CardDatabase
from training.self_play import run_self_play_games

logger = logging.getLogger("run_self_play")

DEFAULT_DECK_JSON = DM_ENGINE_ROOT / "decks" / "prebuilt_game.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "self_play" / "gen0_v2_games.jsonl"
GAME_PRESETS = {
    "quick": 50,
    "standard": 100,
    "large": 500,
}


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run v2 neural self-play")
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--deck-json", type=Path, default=DEFAULT_DECK_JSON)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--games", type=int, default=15)
    parser.add_argument("--preset", choices=sorted(GAME_PRESETS), default=None, help="Use a standard v2 game count: quick=50, standard=100, large=500")
    parser.add_argument("--seed-start", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=1000)
    parser.add_argument("--epsilon", type=float, default=0.05)
    parser.add_argument("--first-player", type=int, choices=[0, 1], default=0)
    parser.add_argument("--model-path", type=Path, default=None)
    parser.add_argument("--terminal-weight", type=float, default=0.65, help="Weight for final win/loss when blending with heuristic targets")
    parser.add_argument(
        "--fixed-seating",
        action="store_true",
        help="Do not randomize first player or swap deck seats between games",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the output JSONL before writing new self-play rows",
    )
    return parser


def main() -> None:
    _load_env_file(PROJECT_ROOT / "crawler" / ".env")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = _build_parser()
    args = parser.parse_args()
    if args.preset is not None:
        args.games = GAME_PRESETS[args.preset]
    if not args.dsn:
        parser.error("--dsn is required unless DATABASE_URL is set in crawler/.env")

    db = CardDatabase(args.dsn)
    db.load()
    summary = run_self_play_games(
        db=db,
        deck_json=args.deck_json,
        output_path=args.output,
        games=args.games,
        seed_start=args.seed_start,
        max_steps=args.max_steps,
        epsilon=args.epsilon,
        first_player=args.first_player,
        randomize_seating=not args.fixed_seating,
        model_path=args.model_path,
        terminal_weight=args.terminal_weight,
        overwrite=args.overwrite,
    )
    logger.info(
        "Self-play done: games=%s decisions=%s p0_wins=%s p1_wins=%s no_winner_terminal=%s unfinished=%s output=%s",
        summary.games,
        summary.decisions,
        summary.player0_wins,
        summary.player1_wins,
        summary.no_winner_terminal,
        summary.unfinished,
        args.output,
    )


if __name__ == "__main__":
    main()
