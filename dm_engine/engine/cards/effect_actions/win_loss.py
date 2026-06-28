"""engine/cards/effect_actions/win_loss.py — Win/loss condition and explosion effects."""
from __future__ import annotations

from core.cards import CardDefinition
from core.state import GameState, PendingTrigger
from engine.special_cards.forbidden_explosion import perform_forbidden_explosion

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
    Handle FORBIDDEN_EXPLOSION effect (Rule 701.29).

    Flips 5 Final Forbidden Fields in the battle zone and reassembles them into
    1 Final Forbidden Creature with Forbidden Core underneath (309.1).
    """
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    field_uid = data.get("field_uid") or effect.get("field_uid")
    field_uids = list(data.get("field_uids") or effect.get("field_uids") or [])
    if field_uid and field_uid not in field_uids:
        field_uids.append(field_uid)

    assembled = (
        data.get("assembled_creature_definition")
        or effect.get("assembled_creature_definition")
    )
    if not isinstance(assembled, CardDefinition):
        assembled = None
    core = (
        data.get("forbidden_core_definition")
        or effect.get("forbidden_core_definition")
    )
    if not isinstance(core, CardDefinition):
        core = None

    perform_forbidden_explosion(
        state,
        controller,
        field_uids=field_uids or None,
        assembled_creature_def=assembled,
        forbidden_core_def=core,
    )
