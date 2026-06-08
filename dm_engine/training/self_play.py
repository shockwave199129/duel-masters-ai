"""Self-play recording for generation-0 neural bots."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.action_encoder import ACTION_ENCODER_VERSION, ACTION_VECTOR_SIZE_V2, encode_action_v2
from bot.neural_bot import NeuralBot
from bot.state_encoder import OBSERVATION_ENCODER_VERSION, OBSERVATION_VECTOR_SIZE_V2, encode_observation_v2
from core.actions import Action
from core.enums import GameResult
from core.state import GameState
from core.initializer import initialize_game
from decks.prebuilt import _apply_extra_zones, prebuilt_deck_from_dict
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.game_runner import validate_invariants
from training.rewards import blend_targets, heuristic_state_value

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
    chosen_index: int,
    deck_slots: tuple[int, int],
    deck_ids: tuple[int | None, int | None],
    deck_names: tuple[str, str],
    first_player: int,
    db,
) -> dict[str, Any]:
    player = action.player
    state_features = encode_observation_v2(state, player)
    legal_action_features = [
        encode_action_v2(legal_action, state=state, db=db)
        for legal_action in legal_actions
    ]
    policy_target = [0.0] * len(legal_actions)
    if 0 <= chosen_index < len(policy_target):
        policy_target[chosen_index] = 1.0
    heuristic_target = heuristic_state_value(state, player)
    return {
        "schema_version": 2,
        "observation_version": OBSERVATION_ENCODER_VERSION,
        "action_version": ACTION_ENCODER_VERSION,
        "observation_vector_size": OBSERVATION_VECTOR_SIZE_V2,
        "action_vector_size": ACTION_VECTOR_SIZE_V2,
        "game_id": game_id,
        "seed": seed,
        "step": step,
        "player": player,
        "original_deck_index": deck_slots[player],
        "deck_slots": list(deck_slots),
        "deck_ids": list(deck_ids),
        "deck_names": list(deck_names),
        "player_deck_id": deck_ids[player],
        "player_deck_name": deck_names[player],
        "first_player": first_player,
        "turn": state.turn_number,
        "phase": state.current_phase.name,
        "state_features": state_features,
        "legal_action_features": legal_action_features,
        "chosen_index": chosen_index,
        "policy_target": policy_target,
        "chosen_features": state_features + legal_action_features[chosen_index],
        "chosen_action": repr(action),
        "action_repr": repr(action),
        "legal_actions": [repr(legal_action) for legal_action in legal_actions],
        "legal_action_count": len(legal_actions),
        "winner": None,
        "value_target": 0.0,
        "heuristic_target": heuristic_target,
        "blended_target": heuristic_target,
    }


def _finalize_records(records: list[dict[str, Any]], state: GameState, *, terminal_weight: float = 0.65) -> None:
    winner = _winner_from_result(state.result)
    terminal = state.is_terminal()
    for record in records:
        record["winner"] = winner
        record["terminal"] = terminal
        record["result"] = state.result.value
        value_target = _target_for_player(record["player"], winner, terminal)
        record["value_target"] = value_target
        record["terminal_weight"] = terminal_weight
        record["blended_target"] = blend_targets(
            value_target,
            float(record.get("heuristic_target", 0.0)),
            terminal_weight=terminal_weight,
        )


def _mark_records_error(records: list[dict[str, Any]], error: Exception) -> None:
    message = f"{type(error).__name__}: {error}"
    for record in records:
        record["engine_error"] = message


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _balanced_bit(index: int, offset: int) -> int:
    """Alternate 0/1 with a random offset so even runs stay balanced."""
    return (index + offset) % 2


def run_recorded_game(
    *,
    db,
    deck_json: str | Path | None,
    output_path: str | Path,
    seed: int,
    game_id: str,
    game_index: int = 0,
    seat_flip: bool = False,
    max_steps: int = 1000,
    epsilon: float = 0.05,
    first_player: int | None = 0,
    model_path: str | Path | None = None,
    terminal_weight: float = 0.65,
    use_database_decks: bool = False,
    deck_source: str | None = None,
    allow_mirror_matches: bool = False,
) -> tuple[GameState, list[dict[str, Any]]]:
    """Run one neural-vs-neural game and append finalized decision rows."""
    rng = random.Random(seed)
    state, deck_slots, deck_ids, deck_names, actual_first_player = _load_self_play_state(
        deck_json=deck_json,
        db=db,
        first_player=first_player,
        seed=seed,
        game_id=game_id,
        seat_flip=seat_flip,
        rng=rng,
        use_database_decks=use_database_decks,
        deck_source=deck_source,
        allow_mirror_matches=allow_mirror_matches,
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
        action = bots[acting_player].choose_from_actions(state, legal_actions, db=db)
        chosen_index = legal_actions.index(action)
        records.append(
            _record_decision(
                game_id=game_id,
                seed=seed,
                step=step,
                state=state,
                action=action,
                legal_actions=legal_actions,
                chosen_index=chosen_index,
                deck_slots=deck_slots,
                deck_ids=deck_ids,
                deck_names=deck_names,
                first_player=actual_first_player,
                db=db,
            )
        )

        try:
            state = execute_action(state, action, db=db, validate=False)
            validate_invariants(state)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Stopping %s after engine error at step %s", game_id, step)
            _mark_records_error(records, exc)
            break

    _finalize_records(records, state, terminal_weight=terminal_weight)
    _append_jsonl(Path(output_path), records)
    return state, records


def run_self_play_games(
    *,
    db,
    deck_json: str | Path | None,
    output_path: str | Path,
    games: int = 15,
    seed_start: int = 1,
    max_steps: int = 1000,
    epsilon: float = 0.05,
    first_player: int | None = 0,
    randomize_seating: bool = True,
    model_path: str | Path | None = None,
    terminal_weight: float = 0.65,
    overwrite: bool = False,
    use_database_decks: bool = False,
    deck_source: str | None = None,
    allow_mirror_matches: bool = False,
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
    schedule_rng = random.Random(seed_start)
    seat_offset = schedule_rng.randrange(2)
    first_player_offset = schedule_rng.randrange(2)

    for index in range(games):
        seed = seed_start + index
        game_id = f"gen0-v2-{index + 1:06d}"
        seat_flip = randomize_seating and _balanced_bit(index, seat_offset) == 1
        scheduled_first_player = first_player
        if randomize_seating:
            scheduled_first_player = _balanced_bit(index, first_player_offset)
        state, records = run_recorded_game(
            db=db,
            deck_json=deck_json,
            output_path=output,
            seed=seed,
            game_index=index,
            seat_flip=seat_flip,
            game_id=game_id,
            max_steps=max_steps,
            epsilon=epsilon,
            first_player=scheduled_first_player,
            model_path=model_path,
            terminal_weight=terminal_weight,
            use_database_decks=use_database_decks,
            deck_source=deck_source,
            allow_mirror_matches=allow_mirror_matches,
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
    deck_json: str | Path | None,
    db,
    first_player: int | None,
    seed: int,
    game_id: str,
    seat_flip: bool,
    rng: random.Random,
    use_database_decks: bool,
    deck_source: str | None,
    allow_mirror_matches: bool,
) -> tuple[GameState, tuple[int, int], tuple[int | None, int | None], tuple[str, str], int]:
    if use_database_decks:
        sampled = db.sample_training_decks(
            rng,
            count=2,
            source=deck_source,
            allow_mirror=allow_mirror_matches,
        )
        deck_slots = (1, 0) if seat_flip else (0, 1)
        assigned = (sampled[deck_slots[0]], sampled[deck_slots[1]])
        p0 = assigned[0][1]
        p1 = assigned[1][1]
        deck_ids = (assigned[0][0], assigned[1][0])
        deck_names = (p0.main_deck.name, p1.main_deck.name)
    else:
        if deck_json is None:
            raise ValueError("deck_json is required unless use_database_decks=True")
        p0, p1, deck_slots, deck_names = _load_json_deck_pair(
            deck_json,
            db,
            seat_flip=seat_flip,
        )
        deck_ids = (None, None)

    actual_first_player = first_player if first_player is not None else rng.randrange(2)
    state = initialize_game(
        p0.main_deck,
        p1.main_deck,
        first_player=actual_first_player,
        seed=seed,
        game_id=game_id,
    )
    _apply_extra_zones(state, 0, p0)
    _apply_extra_zones(state, 1, p1)
    return state, deck_slots, deck_ids, deck_names, actual_first_player


def _load_json_deck_pair(
    deck_json: str | Path,
    db,
    *,
    seat_flip: bool,
):
    data = json.loads(Path(deck_json).read_text(encoding="utf-8"))
    players = data.get("players")
    if not isinstance(players, list) or len(players) != 2:
        raise ValueError("Prebuilt game JSON must contain exactly two players")

    deck_slots = (1, 0) if seat_flip else (0, 1)
    p0 = prebuilt_deck_from_dict(players[deck_slots[0]], db)
    p1 = prebuilt_deck_from_dict(players[deck_slots[1]], db)
    return p0, p1, deck_slots, (p0.main_deck.name, p1.main_deck.name)
