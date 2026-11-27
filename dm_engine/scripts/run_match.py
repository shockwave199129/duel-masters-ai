"""
Play a match between two bots on the training decks, several games at a time.

Each deal is played twice with the bots swapping seats. Results are for the
contender.

Examples (from the project folder):
  python dm_engine/scripts/run_match.py --contender mcts:MODEL --opponent neural:MODEL \
      --games 100 --workers 8 --device cuda
  python dm_engine/scripts/run_match.py --contender neural:MODEL --opponent heuristic

The search bot knows the opponent's deck list, but not where the hidden cards are.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

DM_ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(DM_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(DM_ENGINE_ROOT))

import thread_limits  # noqa: F401  # must come before torch is imported
from training.device import play_device
from training.match_runner import MatchConfig, run_match


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Play a match between two bots")
    parser.add_argument("--contender", required=True,
                        help="Bot being tested: random, heuristic, neural:PATH or mcts:PATH")
    parser.add_argument("--opponent", required=True, help="Same forms as --contender")
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--games", type=int, default=100,
                        help="Use an even number so that every deal is played from both seats")
    parser.add_argument("--workers", type=int, default=1, help="Games played at the same time")
    parser.add_argument("--device", choices=["cpu", "cuda", "auto"], default="cpu")
    parser.add_argument("--seed", type=int, default=10_000)
    parser.add_argument("--max-steps", type=int, default=1000)
    parser.add_argument("--trees", type=int, default=8, help="mcts: sampled worlds per decision")
    parser.add_argument("--simulations", type=int, default=512,
                        help="mcts: simulations per decision, shared by the trees")
    parser.add_argument("--c-puct", type=float, default=1.5, help="mcts: exploration strength")
    parser.add_argument("--prior-temp", type=float, default=1.0,
                        help="mcts: higher spreads the search over more moves")
    parser.add_argument("--output", type=Path, default=None, help="Write results as JSON")
    return parser


def _report(config: MatchConfig):
    def on_game(row: dict, rows: list[dict]) -> None:
        wins = sum(1 for r in rows if r["outcome"] == "win")
        losses = sum(1 for r in rows if r["outcome"] == "loss")
        other = len(rows) - wins - losses
        line = (f"[{len(rows):3d}/{config.games}] game {row['game']:3d}: {row['outcome']:5s} "
                f"in {row['seconds']:5.0f}s | won {wins}, lost {losses}"
                + (f", other {other}" if other else ""))
        if row["error"]:
            line += f" | {row['error']}"
        print(line, flush=True)

    return on_game


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if not args.dsn:
        raise SystemExit("--dsn is required unless DATABASE_URL is set")
    if args.games < 1 or args.workers < 1 or args.trees < 1 or args.simulations < 1:
        parser.error("--games, --workers, --trees and --simulations must be at least 1")
    if args.games % 2:
        print("Note: with an odd number of games, one deal is played from one seat only")
    config = MatchConfig(
        contender=args.contender, opponent=args.opponent, games=args.games, seed=args.seed,
        max_steps=args.max_steps, device=play_device(parser, args.device), trees=args.trees,
        simulations=args.simulations, c_puct=args.c_puct, prior_temperature=args.prior_temp)

    rows, summary = run_match(args.dsn, config, workers=args.workers, on_game=_report(config))

    print(f"\n{config.contender} vs {config.opponent}")
    print(f"  won {summary['wins']}, lost {summary['losses']}, "
          f"unfinished {summary['unfinished']}, errors {summary['errors']}")
    print(f"  win rate {100 * summary['win_rate']:.1f}% +/-{100 * summary['margin']:.1f} "
          f"of decided games")
    print(f"  {summary['wall_seconds'] / 60:.1f} min in total with {args.workers} worker(s) "
          f"on {config.device}; {summary['seconds_per_game']:.0f}s per game, "
          f"{summary['seconds_per_decision']:.2f}s per contender decision")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        payload = {"config": asdict(config), "workers": args.workers,
                   "summary": summary, "games": rows}
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"  saved to {args.output}")


if __name__ == "__main__":
    main()
