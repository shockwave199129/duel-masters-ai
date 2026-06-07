"""
engine/game_runner.py — drive complete games with legal-action policies.
"""

from __future__ import annotations

from collections.abc import Callable

from core.actions import Action
from core.enums import Phase
from core.state import GameState
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.sba_checker import check_turn_limit


Policy = Callable[[GameState, list[Action]], Action]


def run_game(
    initial_state: GameState,
    policy: Policy,
    db=None,
    max_steps: int = 1000,
) -> GameState:
    """Run a game using a policy that chooses from legal actions."""
    state = initial_state.copy()
    for _ in range(max_steps):
        if state.is_terminal():
            return state
        legal_actions = get_legal_actions(state, db)
        if not legal_actions:
            raise RuntimeError("No legal actions available")
        action = policy(state, legal_actions)
        state = execute_action(state, action, db=db, validate=False)
        state = check_turn_limit(state)
        validate_invariants(state)
    return state


def validate_invariants(state: GameState) -> None:
    """Catch basic corruption while running self-play/debug games."""
    seen_uids: set[str] = set()
    for player in state.players:
        for zone_cards in (
            player.hand,
            player.mana_zone,
            player.battle_zone,
            player.shield_zone,
            player.graveyard,
        ):
            for card in zone_cards:
                uid = getattr(card, "uid", None)
                if uid is None:
                    continue
                if uid in seen_uids:
                    raise AssertionError(f"Duplicate card uid detected: {uid}")
                seen_uids.add(uid)

    active = state.players[state.active_player]
    if active.has_charged_mana_this_turn and state.current_phase == Phase.DRAW:
        raise AssertionError("Mana charge flag leaked after start-of-turn reset")
