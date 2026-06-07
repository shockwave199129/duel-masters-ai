"""Train an ActionScoreNet model from self-play JSONL."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

DM_ENGINE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DM_ENGINE_ROOT.parent
if str(DM_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(DM_ENGINE_ROOT))

from training.train_action_score import train_action_score_model

logger = logging.getLogger("train_action_score")

DEFAULT_INPUT = PROJECT_ROOT / "data" / "self_play" / "gen0_games.jsonl"
DEFAULT_OUTPUT = DM_ENGINE_ROOT / "models" / "gen1_action_score.pt"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train ActionScoreNet from self-play JSONL")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--hidden-size", type=int, default=128)
    parser.add_argument("--seed", type=int, default=1)
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    args = _build_parser().parse_args()
    summary = train_action_score_model(
        input_path=args.input,
        output_path=args.output,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        hidden_size=args.hidden_size,
        seed=args.seed,
    )
    logger.info(
        "Training done: rows=%s epochs=%s final_loss=%.6f output=%s",
        summary.rows,
        summary.epochs,
        summary.final_loss,
        summary.output_path,
    )


if __name__ == "__main__":
    main()
