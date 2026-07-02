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
from core.enums import ActionType, CardType, Phase
from core.observation import Observation
from core.state import GameState
from db.card_database import CardDatabase
from decks.prebuilt import load_prebuilt_game_json
from engine.action_executor import execute_action
from engine.action_generator import _get_mana_combinations, get_legal_actions
from engine.game_runner import validate_invariants
from rules import RuleKnowledgeService
from training.eval import run_logged_game as shared_run_logged_game

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
    parser = argparse.ArgumentParser(description="Run neural bot games")
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--deck-json", type=Path, default=DEFAULT_DECK_JSON)
    parser.add_argument("--model-path", type=Path, default=None)
    parser.add_argument(
        "--mode",
        choices=["neural-vs-random", "neural-vs-neural", "human-vs-neural"],
        default="neural-vs-random",
    )
    parser.add_argument("--human-player", type=int, choices=[0, 1], default=0)
    parser.add_argument("--epsilon", type=float, default=0.05)
    parser.add_argument("--max-steps", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=None, help="Optional seed for reproducible shuffle/bot choices")
    parser.add_argument("--first-player", type=int, choices=[0, 1], default=0)
    parser.add_argument("--show-steps", action="store_true", help="Print a readable turn-by-turn action log")
    parser.add_argument("--report-path", type=Path, default=None, help="Optional text file to save the action log")
    parser.add_argument("--encoder-version", type=int, choices=[2, 3], default=None)
    parser.add_argument("--chroma-path", type=Path, default=None, help="Optional ChromaDB path for rule explanations/debug context")
    parser.add_argument("--explain-actions", action="store_true", help="Include rule-retrieval context for neural choices in reports")
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


def _emit_block(emit, text: str) -> None:
    for line in text.splitlines():
        emit(line)


_PLAY_ACTION_TYPES = {
    ActionType.SUMMON_CREATURE,
    ActionType.CAST_SPELL,
    ActionType.GENERATE_CROSS_GEAR,
    ActionType.FORTIFY_CASTLE,
    ActionType.DEPLOY_FIELD,
    ActionType.EXECUTE_TAMASEED,
    ActionType.USE_G_ZERO,
}


def _play_verb(card_type: CardType) -> str:
    if card_type == CardType.CREATURE:
        return "summon"
    if card_type == CardType.SPELL:
        return "cast"
    if card_type == CardType.CROSS_GEAR:
        return "generate"
    if card_type == CardType.CASTLE:
        return "fortify"
    if card_type == CardType.FIELD:
        return "deploy"
    if card_type == CardType.TAMASEED:
        return "execute"
    return "play"


def _playability_reason(state: GameState, player: int, card, actions: list[Action]) -> str:
    if any(action.card_uid == card.uid and action.action_type in _PLAY_ACTION_TYPES for action in actions):
        return "playable now"
    if state.current_phase != Phase.MAIN:
        return f"not playable in {state.current_phase.name}; wait for MAIN"

    definition = card.definition
    if definition.card_type not in {
        CardType.CREATURE,
        CardType.SPELL,
        CardType.CROSS_GEAR,
        CardType.CASTLE,
        CardType.FIELD,
        CardType.TAMASEED,
    }:
        return f"{definition.card_type.value} is not supported as a normal main-phase play yet"

    if definition.is_evolution():
        player_state = state.players[player]
        has_base = any(
            not creature.is_ignored
            and (
                bool(definition.evolution_source_races & creature.races)
                or bool(definition.evolution_source_types and creature.definition.card_type in definition.evolution_source_types)
            )
            for creature in player_state.battle_zone
        )
        if not has_base:
            return "needs a valid evolution base in your battle zone"

    combos = _get_mana_combinations(
        state.players[player].mana_zone,
        definition.cost,
        definition.civilizations,
    )
    if combos:
        return "blocked by another rule/effect"

    untapped = [mana for mana in state.players[player].mana_zone if not mana.is_tapped]
    required_civs = set(definition.civilizations)
    available_civs = {civ for mana in untapped for civ in mana.civilizations}
    missing = sorted(civ.value for civ in required_civs - available_civs)
    if missing:
        return f"missing untapped civilization: {', '.join(missing)}"

    min_required_cards = max(definition.cost, len(required_civs))
    if len(untapped) < min_required_cards:
        return f"needs {min_required_cards} untapped mana cards; you have {len(untapped)}"
    return "no valid mana payment combination"


def _emit_hand_playability(state: GameState, player: int, actions: list[Action], emit) -> None:
    hand = state.players[player].hand
    if not hand:
        return
    emit("")
    emit("Hand playability:")
    for card in hand:
        definition = card.definition
        verb = _play_verb(definition.card_type)
        civs = "/".join(civ.value[0] for civ in definition.civilizations) or "colorless"
        reason = _playability_reason(state, player, card, actions)
        emit(f"  - {definition.name}: {verb}, cost {definition.cost}, {civs} -> {reason}")


def _choose_human_action(
    *,
    state: GameState,
    actions: list[Action],
    db: CardDatabase,
    human_player: int,
    emit,
) -> Action:
    """Prompt a human to choose one of the executable candidate actions."""
    emit("")
    _emit_block(emit, Observation.build(state, human_player).display())
    emit("")
    emit("Legal actions:")
    for index, action in enumerate(actions, start=1):
        emit(f"  {index}. {_describe_action(state, action, db)}")
    _emit_hand_playability(state, human_player, actions, emit)

    while True:
        choice = input(f"Choose action [1-{len(actions)}] or q to quit: ").strip().lower()
        if choice in {"q", "quit", "exit"}:
            raise KeyboardInterrupt("Human player quit")
        try:
            selected = int(choice)
        except ValueError:
            emit("Please enter a number from the legal action list.")
            continue
        if 1 <= selected <= len(actions):
            return actions[selected - 1]
        emit("Choice out of range.")


def _run_human_game(
    *,
    initial_state: GameState,
    neural_bot: NeuralBot,
    human_player: int,
    db: CardDatabase,
    deck_names: tuple[str, str],
    max_steps: int,
    emit,
    explain_actions: bool = False,
) -> GameState:
    """Run an interactive human-vs-neural game in the terminal."""
    state = initial_state.copy()
    neural_player = 1 - human_player
    emit("Game setup")
    emit(f"  Human: Player {human_player} using {deck_names[human_player]}")
    emit(f"  NeuralBot: Player {neural_player} using {deck_names[neural_player]}")
    emit(f"  First player: Player {state.active_player}")
    emit(f"  Starting state: {_zone_summary(state)}")

    for step in range(1, max_steps + 1):
        if state.is_terminal():
            break
        legal_actions = neural_bot.generate_candidate_actions(state, db=db)
        if not legal_actions:
            raise RuntimeError("No legal actions available")

        acting_player = legal_actions[0].player
        emit("")
        emit(f"Step {step}: Player {acting_player} - {state.current_phase.name}")
        emit(f"  Legal actions: {len(legal_actions)}")
        if acting_player == human_player:
            action = _choose_human_action(
                state=state,
                actions=legal_actions,
                db=db,
                human_player=human_player,
                emit=emit,
            )
        else:
            action = neural_bot.choose_from_actions(state, legal_actions, db=db)
            emit(f"  Neural action: {_describe_action(state, action, db)}")
            if explain_actions:
                scores = neural_bot.score_actions(state, legal_actions, db=db)
                score = scores[legal_actions.index(action)] if scores else None
                explanation = neural_bot.explain_action_score(state, action, score)
                if explanation:
                    emit("  Rule context:")
                    for line in explanation.splitlines():
                        emit(f"    {line}")

        state = execute_action(state, action, db=db, validate=False)
        validate_invariants(state)
        emit(f"  After action: {_zone_summary(state)}")

    emit("")
    emit("Game result")
    emit(f"  Result: {state.result.value}")
    emit(f"  Winner: {state.winner()}")
    emit(f"  Final turn/phase: turn {state.turn_number}, {state.current_phase.name}")
    return state


def _run_logged_game(
    *,
    initial_state: GameState,
    bot0,
    bot1,
    db: CardDatabase,
    deck_names: tuple[str, str],
    max_steps: int,
    emit,
    explain_actions: bool = False,
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
        candidate_bot = bot0 if isinstance(bot0, NeuralBot) else bot1
        if isinstance(candidate_bot, NeuralBot):
            legal_actions = candidate_bot.generate_candidate_actions(state, db=db)
        else:
            legal_actions = get_legal_actions(state, db)
        if not legal_actions:
            raise RuntimeError("No legal actions available")

        acting_player = legal_actions[0].player
        bot = bot0 if acting_player == 0 else bot1
        if isinstance(bot, NeuralBot):
            action = bot.choose_from_actions(state, legal_actions, db=db)
            score = None
            if explain_actions:
                scores = bot.score_actions(state, legal_actions, db=db)
                score = scores[legal_actions.index(action)] if scores else None
        else:
            action = bot.rng.choice(legal_actions)
            score = None

        turn_header = (state.turn_number, acting_player)
        if turn_header != current_turn_header:
            current_turn_header = turn_header
            emit(f"Player {acting_player} ({deck_names[acting_player]}) - Turn {state.turn_number}")

        emit(f"  Step {step}: {state.current_phase.name}")
        emit(f"    Legal actions: {len(legal_actions)}")
        emit(f"    Chosen action: {_describe_action(state, action, db)}")
        if explain_actions and isinstance(bot, NeuralBot):
            explanation = bot.explain_action_score(state, action, score)
            if explanation:
                emit("    Rule context:")
                for line in explanation.splitlines():
                    emit(f"      {line}")

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
    rule_service = RuleKnowledgeService.from_card_database(
        db,
        chroma_path=str(args.chroma_path) if args.chroma_path is not None else None,
    )
    deck_names = _read_deck_names(args.deck_json)
    state = load_prebuilt_game_json(
        args.deck_json,
        db,
        first_player=args.first_player,
        seed=args.seed,
        game_id="v2-neural-game",
    )

    bot0_seed = args.seed
    bot1_seed = args.seed + 1 if args.seed is not None else None
    if args.mode == "human-vs-neural":
        neural_seed = bot1_seed if args.human_player == 0 else bot0_seed
        neural_bot = NeuralBot(
            model_path=args.model_path,
            epsilon=args.epsilon,
            seed=neural_seed,
            encoder_version=args.encoder_version,
            rule_service=rule_service,
        )
    else:
        neural_bot = None
    bot0 = NeuralBot(
        model_path=args.model_path,
        epsilon=args.epsilon,
        seed=bot0_seed,
        encoder_version=args.encoder_version,
        rule_service=rule_service,
    )
    bot1 = (
        NeuralBot(
            model_path=args.model_path,
            epsilon=args.epsilon,
            seed=bot1_seed,
            encoder_version=args.encoder_version,
            rule_service=rule_service,
        )
        if args.mode == "neural-vs-neural"
        else RandomBot(seed=bot1_seed)
    )

    report_lines: list[str] = []

    def emit(line: str) -> None:
        report_lines.append(line)
        if args.show_steps or args.mode == "human-vs-neural":
            print(line)

    if args.mode == "human-vs-neural":
        assert neural_bot is not None
        final_state = _run_human_game(
            initial_state=state,
            neural_bot=neural_bot,
            human_player=args.human_player,
            db=db,
            deck_names=deck_names,
            max_steps=args.max_steps,
            explain_actions=args.explain_actions,
            emit=emit,
        )
    elif args.show_steps or args.report_path is not None:
        final_state = shared_run_logged_game(
            initial_state=state,
            bot0=bot0,
            bot1=bot1,
            db=db,
            deck_names=deck_names,
            max_steps=args.max_steps,
            explain_actions=args.explain_actions,
            emit=emit,
        )
    else:
        final_state = shared_run_logged_game(
            initial_state=state,
            bot0=bot0,
            bot1=bot1,
            db=db,
            deck_names=deck_names,
            max_steps=args.max_steps,
            explain_actions=args.explain_actions,
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
