"""Train an ActionCriticNet model from self-play JSONL."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

DM_ENGINE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DM_ENGINE_ROOT.parent
if str(DM_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(DM_ENGINE_ROOT))

from bot.neural_model import DEFAULT_DROPOUT, DEFAULT_HIDDEN_SIZE, DEFAULT_NUM_BLOCKS
from training.train_actor_critic import train_actor_critic_model

logger = logging.getLogger("train_actor_critic")

DEFAULT_INPUT = PROJECT_ROOT / "data" / "self_play" / "gen0_v2_games.jsonl"
DEFAULT_OUTPUT = DM_ENGINE_ROOT / "models" / "gen1_v2_action_critic.pt"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train ActionCriticNet from self-play JSONL")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--hidden-size", type=int, default=DEFAULT_HIDDEN_SIZE)
    parser.add_argument("--num-blocks", type=int, default=DEFAULT_NUM_BLOCKS)
    parser.add_argument("--dropout", type=float, default=DEFAULT_DROPOUT)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--policy-loss-mode", choices=["pairwise", "mse"], default="pairwise")
    parser.add_argument(
        "--value-target-field",
        choices=["value_target", "heuristic_target", "blended_target"],
        default="blended_target",
    )
    parser.add_argument("--value-coef", type=float, default=1.0)
    parser.add_argument("--entropy-coef", type=float, default=0.0)
    parser.add_argument(
        "--no-balance-players",
        action="store_true",
        help="Do not equalize decision counts between player 0 and player 1",
    )
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    args = _build_parser().parse_args()
    summary = train_actor_critic_model(
        input_path=args.input,
        output_path=args.output,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        hidden_size=args.hidden_size,
        num_blocks=args.num_blocks,
        dropout=args.dropout,
        seed=args.seed,
        policy_loss_mode=args.policy_loss_mode,
        value_target_field=args.value_target_field,
        value_coef=args.value_coef,
        entropy_coef=args.entropy_coef,
        balance_players=not args.no_balance_players,
    )
    logger.info(
        "Training done: rows=%s epochs=%s final_loss=%.6f policy=%.6f value=%.6f entropy=%.6f output=%s",
        summary.rows,
        summary.epochs,
        summary.final_loss,
        summary.policy_loss,
        summary.value_loss,
        summary.entropy,
        summary.output_path,
    )


if __name__ == "__main__":
    main()
