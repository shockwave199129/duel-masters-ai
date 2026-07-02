"""Evaluate bot matchups and write simple matchup metrics."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

DM_ENGINE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DM_ENGINE_ROOT.parent
if str(DM_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(DM_ENGINE_ROOT))

from bot.heuristic_bot import HeuristicBot
from bot.neural_bot import NeuralBot
from bot.random_bot import RandomBot
from db.card_database import CardDatabase
from decks.prebuilt import load_prebuilt_game_json
from rules import RuleKnowledgeService
from training.eval import run_logged_game, write_eval_reports

logger = logging.getLogger("eval_bots")

DEFAULT_DECK_JSON = DM_ENGINE_ROOT / "decks" / "prebuilt_game.json"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate bot matchups")
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--deck-json", type=Path, default=DEFAULT_DECK_JSON)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--opponents", default="random,heuristic")
    parser.add_argument("--games", type=int, default=10)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=1000)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--csv-output", type=Path, default=None)
    return parser


def _make_bot(spec: str, *, seed: int, model_path: Path):
    if spec == "random":
        return RandomBot(seed=seed)
    if spec == "heuristic":
        return HeuristicBot(seed=seed)
    if spec.startswith("checkpoint:") or spec == "neural":
        path = model_path if spec == "neural" else Path(spec.split(":", 1)[1])
        return NeuralBot(model_path=path, seed=seed)
    raise ValueError(f"Unsupported opponent spec: {spec}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    args = _build_parser().parse_args()
    if not args.dsn:
        raise SystemExit("--dsn is required unless DATABASE_URL is set")

    db = CardDatabase(args.dsn)
    db.load()
    rule_service = RuleKnowledgeService.from_card_database(db)
    deck_names = ("Deck 1", "Deck 2")
    results: list[dict[str, object]] = []
    opponent_specs = [item.strip() for item in args.opponents.split(",") if item.strip()]
    for opponent_index, opponent_spec in enumerate(opponent_specs):
        for game_index in range(args.games):
            seed = args.seed + opponent_index * 1000 + game_index
            state = load_prebuilt_game_json(args.deck_json, db, first_player=0, seed=seed, game_id=f"eval-{opponent_spec}-{game_index}")
            bot0 = NeuralBot(model_path=args.model_path, seed=seed, rule_service=rule_service)
            bot1 = _make_bot(opponent_spec, seed=seed + 1, model_path=args.model_path)
            final_state = run_logged_game(
                initial_state=state,
                bot0=bot0,
                bot1=bot1,
                db=db,
                deck_names=deck_names,
                max_steps=args.max_steps,
                emit=lambda _line: None,
            )
            results.append(
                {
                    "opponent": opponent_spec,
                    "winner": final_state.winner(),
                    "result": final_state.result.value,
                    "steps": len(final_state.history),
                    "p0_shields": len(final_state.players[0].shield_zone),
                    "p1_shields": len(final_state.players[1].shield_zone),
                }
            )

    summary = write_eval_reports(results=results, json_path=args.output, csv_path=args.csv_output)
    print(json.dumps([result.__dict__ for result in summary], indent=2))


if __name__ == "__main__":
    main()
