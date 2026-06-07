"""Run a generation-0 neural bot game from prebuilt deck JSON."""

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

from bot.neural_bot import NeuralBot
from bot.random_bot import RandomBot
from core.actions import Action
from core.state import GameState
from db.card_database import CardDatabase
from decks.prebuilt import load_prebuilt_game_json
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.game_runner import validate_invariants

logger = logging.getLogger("play_neural_game")

DEFAULT_DECK_JSON = DM_ENGINE_ROOT / "decks" / "prebuilt_game.json"


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
    parser = argparse.ArgumentParser(description="Run gen-0 neural bot games")
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--deck-json", type=Path, default=DEFAULT_DECK_JSON)
    parser.add_argument("--model-path", type=Path, default=None)
    parser.add_argument("--mode", choices=["neural-vs-random", "neural-vs-neural"], default="neural-vs-random")
    parser.add_argument("--epsilon", type=float, default=0.05)
    parser.add_argument("--max-steps", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=None, help="Optional seed for reproducible shuffle/bot choices")
    parser.add_argument("--first-player", type=int, choices=[0, 1], default=0)
    parser.add_argument("--show-steps", action="store_true", help="Print a readable turn-by-turn action log")
    parser.add_argument("--report-path", type=Path, default=None, help="Optional text file to save the action log")
    return parser


def _read_deck_names(path: Path) -> tuple[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    players = data.get("players", [])
    names = []
    for index in range(2):
        if index < len(players) and isinstance(players[index], dict):
            names.append(str(players[index].get("name") or f"Deck {index + 1}"))
        else:
            names.append(f"Deck {index + 1}")
    return names[0], names[1]


def _card_name(db: CardDatabase, card_id: int | None) -> str:
    if card_id is None:
        return ""
    card = db.get(card_id)
    return card.name if card is not None else f"card #{card_id}"


def _target_text(state: GameState, action: Action) -> str:
    if not action.target_uid:
        return ""
    if action.target_uid.startswith("player_"):
        return f" targeting {action.target_uid.replace('_', ' ').title()}"
    found = state.find_creature_anywhere(action.target_uid)
    if found is None:
        return f" targeting {action.target_uid[:8]}"
    controller, creature = found
    return f" targeting P{controller}'s {creature.name}"


def _describe_action(state: GameState, action: Action, db: CardDatabase) -> str:
    card_name = _card_name(db, action.card_id)
    card_part = f" {card_name}" if card_name else ""
    target_part = _target_text(state, action)
    mana_part = f" using {len(action.mana_used)} mana" if action.mana_used else ""
    choice_part = f" choice={action.choice}" if action.choice is not None else ""
    selected_part = f" selecting {len(action.selected_uids)} cards" if action.selected_uids else ""
    return (
        f"{action.action_type.value.replace('_', ' ')}"
        f"{card_part}{target_part}{mana_part}{choice_part}{selected_part}"
    )


def _zone_summary(state: GameState) -> str:
    parts = []
    for player_index, player in enumerate(state.players):
        parts.append(
            f"P{player_index}: shields={len(player.shield_zone)} hand={len(player.hand)} "
            f"mana={len(player.mana_zone)} battle={len(player.battle_zone)} deck={len(player.deck)}"
        )
    return " | ".join(parts)


def _run_logged_game(
    *,
    initial_state: GameState,
    bot0,
    bot1,
    db: CardDatabase,
    deck_names: tuple[str, str],
    max_steps: int,
    emit,
) -> GameState:
    state = initial_state.copy()
    emit("Game setup")
    emit(f"  Player 0: {type(bot0).__name__} using {deck_names[0]}")
    emit(f"  Player 1: {type(bot1).__name__} using {deck_names[1]}")
    emit(f"  First player: Player {state.active_player}")
    emit(f"  Starting state: {_zone_summary(state)}")
    emit("")
    current_turn_header: tuple[int, int] | None = None

    for step in range(1, max_steps + 1):
        if state.is_terminal():
            break
        legal_actions = get_legal_actions(state, db)
        if not legal_actions:
            raise RuntimeError("No legal actions available")

        acting_player = legal_actions[0].player
        bot = bot0 if acting_player == 0 else bot1
        if isinstance(bot, NeuralBot):
            action = bot.choose_from_actions(state, legal_actions)
        else:
            action = bot.rng.choice(legal_actions)

        turn_header = (state.turn_number, acting_player)
        if turn_header != current_turn_header:
            current_turn_header = turn_header
            emit(f"Player {acting_player} ({deck_names[acting_player]}) - Turn {state.turn_number}")

        emit(f"  Step {step}: {state.current_phase.name}")
        emit(f"    Legal actions: {len(legal_actions)}")
        emit(f"    Chosen action: {_describe_action(state, action, db)}")

        state = execute_action(state, action, db=db, validate=False)
        validate_invariants(state)
        emit(f"    After action: {_zone_summary(state)}")

    emit("Game result")
    emit(f"  Result: {state.result.value}")
    emit(f"  Winner: {state.winner()}")
    emit(f"  Final turn/phase: turn {state.turn_number}, {state.current_phase.name}")
    return state


def main() -> None:
    _load_env_file(PROJECT_ROOT / "crawler" / ".env")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = _build_parser()
    args = parser.parse_args()
    if not args.dsn:
        parser.error("--dsn is required unless DATABASE_URL is set in crawler/.env")

    db = CardDatabase(args.dsn)
    db.load()
    deck_names = _read_deck_names(args.deck_json)
    state = load_prebuilt_game_json(
        args.deck_json,
        db,
        first_player=args.first_player,
        seed=args.seed,
        game_id="gen0-neural-game",
    )

    bot0_seed = args.seed
    bot1_seed = args.seed + 1 if args.seed is not None else None
    bot0 = NeuralBot(model_path=args.model_path, epsilon=args.epsilon, seed=bot0_seed)
    bot1 = (
        NeuralBot(model_path=args.model_path, epsilon=args.epsilon, seed=bot1_seed)
        if args.mode == "neural-vs-neural"
        else RandomBot(seed=bot1_seed)
    )

    report_lines: list[str] = []

    def emit(line: str) -> None:
        report_lines.append(line)
        if args.show_steps:
            print(line)

    if args.show_steps or args.report_path is not None:
        final_state = _run_logged_game(
            initial_state=state,
            bot0=bot0,
            bot1=bot1,
            db=db,
            deck_names=deck_names,
            max_steps=args.max_steps,
            emit=emit,
        )
    else:
        final_state = _run_logged_game(
            initial_state=state,
            bot0=bot0,
            bot1=bot1,
            db=db,
            deck_names=deck_names,
            max_steps=args.max_steps,
            emit=lambda _line: None,
        )

    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    logger.info(
        "Finished: result=%s winner=%s turn=%s phase=%s history=%s",
        final_state.result.value,
        final_state.winner(),
        final_state.turn_number,
        final_state.current_phase,
        len(final_state.history),
    )


if __name__ == "__main__":
    main()
