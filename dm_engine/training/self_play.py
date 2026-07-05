"""Self-play recording for generation-0 neural bots."""

from __future__ import annotations

import json
import logging
import random
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.action_encoder import (
    ACTION_ENCODER_VERSION,
    ACTION_ENCODER_VERSION_V3,
    ACTION_VECTOR_SIZE_V2,
    ACTION_VECTOR_SIZE_V3,
    encode_action_v2,
    encode_action_v3,
)
from bot.state_encoder import (
    OBSERVATION_ENCODER_VERSION,
    OBSERVATION_ENCODER_VERSION_V3,
    OBSERVATION_VECTOR_SIZE_V2,
    OBSERVATION_VECTOR_SIZE_V3,
    encode_observation_v2,
    encode_observation_v3,
)
from core.actions import Action
from core.enums import GameResult
from core.state import GameState
from core.initializer import initialize_game
from decks.prebuilt import _apply_extra_zones, prebuilt_deck_from_dict
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.game_runner import validate_invariants
from bot.factory import make_bot
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


def _encode_decision_features(
    state: GameState,
    player: int,
    legal_actions: list[Action],
    *,
    db,
    encoder_version: int,
    rule_service=None,
) -> tuple[list[float], list[list[float]], int, int, int, int]:
    if encoder_version == 2:
        state_features = encode_observation_v2(state, player)
        legal_action_features = [
            encode_action_v2(legal_action, state=state, db=db)
            for legal_action in legal_actions
        ]
        return (
            state_features,
            legal_action_features,
            OBSERVATION_ENCODER_VERSION,
            ACTION_ENCODER_VERSION,
            OBSERVATION_VECTOR_SIZE_V2,
            ACTION_VECTOR_SIZE_V2,
        )
    if encoder_version == 3:
        state_features = encode_observation_v3(state, player, rule_service=rule_service)
        legal_action_features = [
            encode_action_v3(
                legal_action,
                state=state,
                db=db,
                rule_service=rule_service,
            )
            for legal_action in legal_actions
        ]
        return (
            state_features,
            legal_action_features,
            OBSERVATION_ENCODER_VERSION_V3,
            ACTION_ENCODER_VERSION_V3,
            OBSERVATION_VECTOR_SIZE_V3,
            ACTION_VECTOR_SIZE_V3,
        )
    raise ValueError("encoder_version must be 2 or 3")


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
    encoder_version: int,
    rule_service=None,
    policy_log_prob: float | None = None,
    behavior_log_prob: float | None = None,
    was_random: bool | None = None,
) -> dict[str, Any]:
    player = action.player
    (
        state_features,
        legal_action_features,
        observation_version,
        action_version,
        observation_vector_size,
        action_vector_size,
    ) = _encode_decision_features(
        state,
        player,
        legal_actions,
        db=db,
        encoder_version=encoder_version,
        rule_service=rule_service,
    )
    policy_target = [0.0] * len(legal_actions)
    if 0 <= chosen_index < len(policy_target):
        policy_target[chosen_index] = 1.0
    heuristic_target = heuristic_state_value(state, player)
    return {
        "schema_version": encoder_version,
        "observation_version": observation_version,
        "action_version": action_version,
        "observation_vector_size": observation_vector_size,
        "action_vector_size": action_vector_size,
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
        "policy_log_prob": policy_log_prob,
        "behavior_log_prob": behavior_log_prob,
        "was_random": was_random,
        "chosen_features": state_features + legal_action_features[chosen_index],
        "chosen_action": repr(action),
        "action_repr": repr(action),
        "legal_actions": [repr(legal_action) for legal_action in legal_actions],
        "legal_action_count": len(legal_actions),
        "encoder_version": encoder_version,
        "rule_aware": encoder_version >= 3,
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


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _append_jsonl_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
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
    max_steps: int | None = None,
    epsilon: float = 0.05,
    first_player: int | None = 0,
    model_path: str | Path | None = None,
    bot_specs: tuple[str, str] | None = None,
    terminal_weight: float = 0.65,
    use_database_decks: bool = False,
    deck_source: str | None = None,
    allow_mirror_matches: bool = False,
    policy_encoder_version: int | None = None,
    record_encoder_version: int = 3,
    rule_service=None,
) -> tuple[GameState, int]:
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
    seat_specs = bot_specs or ("neural", "neural")
    bots = {
        0: make_bot(seat_specs[0], seed=seed, rule_service=rule_service, model_path=model_path),
        1: make_bot(seat_specs[1], seed=seed + 1, rule_service=rule_service, model_path=model_path),
    }
    decision_count = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_path = Path(tmpdir) / f"{game_id}.raw.jsonl"
        step = 0
        while max_steps is None or step < max_steps:
            if state.is_terminal():
                break
            legal_actions = bots[state.active_player].generate_candidate_actions(state, db=db)
            if not legal_actions:
                raise RuntimeError("No legal actions available")

            acting_player = legal_actions[0].player
            bot = bots[acting_player]
            if hasattr(bot, "choose_from_actions"):
                action = bot.choose_from_actions(state, legal_actions, db=db)
            else:
                action = bot.choose_action(state, db=db)
            chosen_index = legal_actions.index(action)
            policy_log_prob = getattr(bot, "last_policy_log_prob", None)
            behavior_log_prob = getattr(bot, "last_behavior_log_prob", None)
            was_random = getattr(bot, "last_was_random", None)
            raw_row = _record_decision(
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
                encoder_version=record_encoder_version,
                rule_service=rule_service,
                policy_log_prob=policy_log_prob,
                behavior_log_prob=behavior_log_prob,
                was_random=was_random,
            )
            raw_row["seat_bot_0"] = seat_specs[0]
            raw_row["seat_bot_1"] = seat_specs[1]
            raw_row["policy_model_path"] = str(model_path) if model_path is not None else ""
            _append_jsonl_row(raw_path, raw_row)
            decision_count += 1

            try:
                state = execute_action(state, action, db=db, validate=False)
                validate_invariants(state)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.exception("Stopping %s after engine error at step %s", game_id, step)
                with raw_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"engine_error": f"{type(exc).__name__}: {exc}"}) + "\n")
                break
            step += 1

        with raw_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    if "engine_error" in record:
                        continue
                    _finalize_records([record], state, terminal_weight=terminal_weight)
                    _append_jsonl_row(Path(output_path), record)

    return state, decision_count


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
    bot_p0: str = "neural",
    bot_p1: str = "neural",
    opponent_pool: str | None = None,
    league_dir: str | Path | None = None,
    terminal_weight: float = 0.65,
    overwrite: bool = False,
    use_database_decks: bool = False,
    deck_source: str | None = None,
    allow_mirror_matches: bool = False,
    policy_encoder_version: int | None = None,
    record_encoder_version: int = 3,
    rule_service=None,
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
        game_id = f"gen0-v3-{index + 1:06d}"
        seat_flip = randomize_seating and _balanced_bit(index, seat_offset) == 1
        scheduled_first_player = first_player
        if randomize_seating:
            scheduled_first_player = _balanced_bit(index, first_player_offset)
        seat_specs = (bot_p0, bot_p1)
        if opponent_pool:
            pool = [item.strip() for item in opponent_pool.split(",") if item.strip()]
            opponent_spec = pool[index % len(pool)] if pool else "neural"
            if opponent_spec == "neural" and league_dir is not None:
                league_path = Path(league_dir)
                checkpoints = sorted(league_path.glob("*.pt"))
                if checkpoints:
                    opponent_spec = f"checkpoint:{checkpoints[index % len(checkpoints)]}"
            seat_specs = (bot_p0, opponent_spec)
        state, decisions_written = run_recorded_game(
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
            bot_specs=seat_specs,
            terminal_weight=terminal_weight,
            use_database_decks=use_database_decks,
            deck_source=deck_source,
            allow_mirror_matches=allow_mirror_matches,
            policy_encoder_version=policy_encoder_version,
            record_encoder_version=record_encoder_version,
            rule_service=rule_service,
        )
        decisions += decisions_written
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
            decisions_written,
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
        available_ids = db.list_training_deck_ids(source=deck_source, active_only=True)
        if not available_ids:
            raise ValueError("No active training decks found in the database")
        if allow_mirror_matches:
            selected_ids = [rng.choice(available_ids) for _ in range(2)]
        else:
            if len(available_ids) < 2:
                raise ValueError("Need at least 2 active training decks")
            selected_ids = rng.sample(available_ids, 2)

        unique_card_ids: set[int] = set()
        for deck_id in selected_ids:
            unique_card_ids.update(db.get_training_deck_card_ids(deck_id))
        db.load(sorted(unique_card_ids))

        deck_slots = (1, 0) if seat_flip else (0, 1)
        assigned_ids = (selected_ids[deck_slots[0]], selected_ids[deck_slots[1]])
        assigned = tuple(db.load_training_deck(deck_id) for deck_id in assigned_ids)
        p0 = assigned[0]
        p1 = assigned[1]
        deck_ids = assigned_ids
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
