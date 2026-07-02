"""Shared evaluation helpers for bot matchups."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import json

from core.state import GameState
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.game_runner import validate_invariants


@dataclass(frozen=True)
class MatchupResult:
    opponent: str
    games: int
    win_rate: float
    avg_steps: float
    unfinished_rate: float
    avg_shields_remaining: float


def _zone_summary(state: GameState) -> str:
    parts = []
    for player_index, player in enumerate(state.players):
        parts.append(
            f"P{player_index}: shields={len(player.shield_zone)} hand={len(player.hand)} "
            f"mana={len(player.mana_zone)} battle={len(player.battle_zone)} deck={len(player.deck)}"
        )
    return " | ".join(parts)


def run_logged_game(
    *,
    initial_state: GameState,
    bot0,
    bot1,
    db,
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
        candidate_bot = bot0 if hasattr(bot0, "generate_candidate_actions") else bot1
        if hasattr(candidate_bot, "generate_candidate_actions"):
            legal_actions = candidate_bot.generate_candidate_actions(state, db=db)
        else:
            legal_actions = get_legal_actions(state, db)
        if not legal_actions:
            raise RuntimeError("No legal actions available")

        acting_player = legal_actions[0].player
        bot = bot0 if acting_player == 0 else bot1
        if hasattr(bot, "choose_from_actions"):
            action = bot.choose_from_actions(state, legal_actions, db=db)
        else:
            action = bot.choose_action(state, db=db)
        score = None
        if explain_actions and hasattr(bot, "score_actions"):
            scores = bot.score_actions(state, legal_actions, db=db)
            score = scores[legal_actions.index(action)] if scores else None

        turn_header = (state.turn_number, acting_player)
        if turn_header != current_turn_header:
            current_turn_header = turn_header
            emit(f"Player {acting_player} ({deck_names[acting_player]}) - Turn {state.turn_number}")

        emit(f"  Step {step}: {state.current_phase.name}")
        emit(f"    Legal actions: {len(legal_actions)}")
        emit(f"    Chosen action: {action}")
        if explain_actions and hasattr(bot, "explain_action_score"):
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


def summarize_matchups(results: list[dict[str, object]]) -> list[MatchupResult]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in results:
        grouped.setdefault(str(row["opponent"]), []).append(row)

    summary: list[MatchupResult] = []
    for opponent, matchup_rows in grouped.items():
        games = len(matchup_rows)
        wins = sum(1 for row in matchup_rows if row["winner"] == 0)
        unfinished = sum(1 for row in matchup_rows if row["result"] not in {"player_0_wins", "player_1_wins"})
        avg_steps = sum(int(row["steps"]) for row in matchup_rows) / games if games else 0.0
        avg_shields_remaining = (
            sum(int(row.get("p0_shields", 0)) + int(row.get("p1_shields", 0)) for row in matchup_rows) / games
            if games
            else 0.0
        )
        summary.append(
            MatchupResult(
                opponent=opponent,
                games=games,
                win_rate=wins / games if games else 0.0,
                avg_steps=avg_steps,
                unfinished_rate=unfinished / games if games else 0.0,
                avg_shields_remaining=avg_shields_remaining,
            )
        )
    return summary


def write_eval_reports(
    *,
    results: list[dict[str, object]],
    json_path: str | Path | None = None,
    csv_path: str | Path | None = None,
) -> list[MatchupResult]:
    summary = summarize_matchups(results)
    payload = {
        "results": results,
        "summary": [result.__dict__ for result in summary],
    }
    if json_path is not None:
        path = Path(json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if csv_path is not None:
        path = Path(csv_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "opponent",
                    "games",
                    "win_rate",
                    "avg_steps",
                    "unfinished_rate",
                    "avg_shields_remaining",
                ],
            )
            writer.writeheader()
            writer.writerows(result.__dict__ for result in summary)
    return summary
