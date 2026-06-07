"""
engine/effect_executor.py — execute parsed card effects incrementally.
"""

from __future__ import annotations

from core.enums import EffectAction
from core.state import GameState, PendingTrigger
from engine.sba_checker import check_state_based_actions
from engine.zone_mover import draw_card, move_battle_to_graveyard


def execute_pending_trigger(state: GameState, trigger: PendingTrigger) -> GameState:
    """Execute one pending trigger and run SBAs after it resolves."""
    s = state.copy()
    effect = trigger.effect
    action = effect.effect_action
    controller = trigger.controller

    if action == EffectAction.DRAW:
        amount = int(effect.effect_value.get("amount", 1) or 1)
        for _ in range(amount):
            draw_card(s, controller)

    elif action == EffectAction.DESTROY:
        target_uid = trigger.trigger_data.get("target_uid")
        if target_uid:
            found = s.find_creature_anywhere(target_uid)
            if found:
                target_player, _ = found
                move_battle_to_graveyard(s, target_player, target_uid, reason="effect")

    # Unsupported effects intentionally resolve as no-ops until implemented.
    return check_state_based_actions(s)
