"""engine/cards/effect_actions/win_loss.py — Win/loss condition and explosion effects."""
from __future__ import annotations

from core.state import GameState, PendingTrigger

# ── Shared helpers ──────────────────────────────────────────────────────────

def _effect_value(trigger: PendingTrigger) -> dict:
    return dict(trigger.effect.effect_value)



def _trigger_data(trigger: PendingTrigger) -> dict:
    return dict(trigger.trigger_data)



# ── Effect action implementations ──────────────────────────────────────────

def _do_win_by_effect(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle WIN_CONDITION effect: the controller wins the game (Rule 104.2c).
    
    Rule 104.2c: If a player meets both a win and lose condition simultaneously,
    the player wins.
    """
    state.game_result = ("win", controller)



def _do_lose_by_effect(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle LOSE_CONDITION effect: the controller loses the game (Rule 104.2c).
    """
    opponent = 1 - controller
    state.game_result = ("win", opponent)



def _do_forbidden_explosion(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle FORBIDDEN_EXPLOSION effect: flip Final Forbidden Field (Rule 701.29).

    Effect value:
    - field_uid: UID of the Final Forbidden Field in the field zone
    """
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    field_uid = data.get("field_uid") or effect.get("field_uid")
    if not field_uid:
        return

    p_state = state.players[controller]
    for idx, field_def in enumerate(p_state.field_zone):
        if getattr(field_def, "uid", None) == field_uid or getattr(field_def, "id", None) == field_uid:
            # Flip the Final Forbidden Field to its other face
            if hasattr(field_def, "flipped_definition") and field_def.flipped_definition:
                p_state.field_zone[idx] = field_def.flipped_definition
            break
