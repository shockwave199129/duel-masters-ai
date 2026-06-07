"""Self-play recording for generation-0 neural bots."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.action_encoder import encode_action
from bot.neural_bot import NeuralBot
from bot.state_encoder import encode_observation
from core.actions import Action
from core.enums import GameResult
from core.state import GameState
from core.initializer import initialize_game
from decks.prebuilt import _apply_extra_zones, prebuilt_deck_from_dict
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.game_runner import validate_invariants

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SelfPlaySummary:
    games: int
    decisions: int
    player0_wins: int
    player1_wins: int
    no_winner_terminal: int
    unfinished: int


def _winner_from_result(result: GameResult) -> int | None:
    if result == GameResult.PLAYER_0_WINS:
        return 0
    if result == GameResult.PLAYER_1_WINS:
        return 1
    return None


def _target_for_player(player: int, winner: int | None, terminal: bool) -> float:
    if winner is None:
        return 0.0
    if not terminal:
        return 0.0
    return 1.0 if winner == player else -1.0


def _record_decision(
    *,
    game_id: str,
    seed: int,
    step: int,
    state: GameState,
    action: Action,
    legal_actions: list[Action],
    deck_slots: tuple[int, int],
    first_player: int,
) -> dict[str, Any]:
    player = action.player
    observation = encode_observation(state, player)
    action_vector = encode_action(action)
    return {
        "game_id": game_id,
        "seed": seed,
        "step": step,
        "player": player,
        "original_deck_index": deck_slots[player],
        "deck_slots": list(deck_slots),
        "first_player": first_player,
        "turn": state.turn_number,
        "phase": state.current_phase.name,
        "observation": observation,
        "action": action_vector,
        "features": observation + action_vector,
        "chosen_action": repr(action),
        "action_repr": repr(action),
        "legal_actions": [repr(legal_action) for legal_action in legal_actions],
        "legal_action_count": len(legal_actions),
        "winner": None,
        "value_target": 0.0,
    }


def _finalize_records(records: list[dict[str, Any]], state: GameState) -> None:
    winner = _winner_from_result(state.result)
    terminal = state.is_terminal()
    for record in records:
        record["winner"] = winner
        record["terminal"] = terminal
        record["result"] = state.result.value
        record["value_target"] = _target_for_player(record["player"], winner, terminal)


def _mark_records_error(records: list[dict[str, Any]], error: Exception) -> None:
    message = f"{type(error).__name__}: {error}"
    for record in records:
        record["engine_error"] = message


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def run_recorded_game(
    *,
    db,
    deck_json: str | Path,
    output_path: str | Path,
    seed: int,
    game_id: str,
    game_index: int = 0,
    max_steps: int = 1000,
    epsilon: float = 0.05,
    first_player: int | None = 0,
    randomize_seating: bool = True,
    model_path: str | Path | None = None,
) -> tuple[GameState, list[dict[str, Any]]]:
    """Run one neural-vs-neural game and append finalized decision rows."""
    rng = random.Random(seed)
    state, deck_slots, actual_first_player = _load_self_play_state(
        deck_json=deck_json,
        db=db,
        first_player=first_player,
        seed=seed,
        game_index=game_index,
        game_id=game_id,
        randomize_seating=randomize_seating,
        rng=rng,
    )
    bots = {
        0: NeuralBot(model_path=model_path, epsilon=epsilon, seed=seed),
        1: NeuralBot(model_path=model_path, epsilon=epsilon, seed=seed + 1),
    }
    records: list[dict[str, Any]] = []

    for step in range(max_steps):
        if state.is_terminal():
            break
        legal_actions = get_legal_actions(state, db)
        if not legal_actions:
            raise RuntimeError("No legal actions available")

        acting_player = legal_actions[0].player
        action = bots[acting_player].choose_from_actions(state, legal_actions)
        records.append(
            _record_decision(
                game_id=game_id,
                seed=seed,
                step=step,
                state=state,
                action=action,
                legal_actions=legal_actions,
                deck_slots=deck_slots,
                first_player=actual_first_player,
            )
        )

        try:
            state = execute_action(state, action, db=db, validate=False)
            validate_invariants(state)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Stopping %s after engine error at step %s", game_id, step)
            _mark_records_error(records, exc)
            break

    _finalize_records(records, state)
    _append_jsonl(Path(output_path), records)
    return state, records


def run_self_play_games(
    *,
    db,
    deck_json: str | Path,
    output_path: str | Path,
    games: int = 15,
    seed_start: int = 1,
    max_steps: int = 1000,
    epsilon: float = 0.05,
    first_player: int | None = 0,
    randomize_seating: bool = True,
    model_path: str | Path | None = None,
    overwrite: bool = False,
) -> SelfPlaySummary:
    """Run and record several neural-vs-neural games."""
    output = Path(output_path)
    if overwrite and output.exists():
        output.unlink()

    player0_wins = 0
    player1_wins = 0
    no_winner_terminal = 0
    unfinished = 0
    decisions = 0

    for index in range(games):
        seed = seed_start + index
        game_id = f"gen0-{index + 1:06d}"
        state, records = run_recorded_game(
            db=db,
            deck_json=deck_json,
            output_path=output,
            seed=seed,
            game_index=index,
            game_id=game_id,
            max_steps=max_steps,
            epsilon=epsilon,
            first_player=first_player,
            randomize_seating=randomize_seating,
            model_path=model_path,
        )
        decisions += len(records)
        winner = _winner_from_result(state.result)
        if winner == 0:
            player0_wins += 1
        elif winner == 1:
            player1_wins += 1
        elif state.is_terminal():
            no_winner_terminal += 1
        else:
            unfinished += 1
        logger.info(
            "Recorded %s decisions for %s: result=%s winner=%s",
            len(records),
            game_id,
            state.result.value,
            winner,
        )

    return SelfPlaySummary(
        games=games,
        decisions=decisions,
        player0_wins=player0_wins,
        player1_wins=player1_wins,
        no_winner_terminal=no_winner_terminal,
        unfinished=unfinished,
    )


def _load_self_play_state(
    *,
    deck_json: str | Path,
    db,
    first_player: int | None,
    seed: int,
    game_index: int,
    game_id: str,
    randomize_seating: bool,
    rng: random.Random,
) -> tuple[GameState, tuple[int, int], int]:
    data = json.loads(Path(deck_json).read_text(encoding="utf-8"))
    players = data.get("players")
    if not isinstance(players, list) or len(players) != 2:
        raise ValueError("Prebuilt game JSON must contain exactly two players")

    deck_slots = (0, 1)
    if randomize_seating and (game_index + rng.randrange(2)) % 2 == 1:
        deck_slots = (1, 0)

    actual_first_player = first_player
    if randomize_seating:
        actual_first_player = (game_index + rng.randrange(2)) % 2

    p0 = prebuilt_deck_from_dict(players[deck_slots[0]], db)
    p1 = prebuilt_deck_from_dict(players[deck_slots[1]], db)
    state = initialize_game(
        p0.main_deck,
        p1.main_deck,
        first_player=actual_first_player,
        seed=seed,
        game_id=game_id,
    )
    _apply_extra_zones(state, 0, p0)
    _apply_extra_zones(state, 1, p1)
    return state, deck_slots, actual_first_player if actual_first_player is not None else state.active_player
