"""engine/cards/effect_actions/combat.py — Combat-related mandatory action effects."""
from __future__ import annotations

from core.state import GameState, PendingTrigger
from core.zones import Creature
from engine.zone_mover import (
    move_shield_to_standby,
)

# ── Shared helpers ──────────────────────────────────────────────────────────

def _find_creature(state: GameState, uid: str) -> tuple[int, Creature] | None:
    if not uid:
        return None
    return state.find_creature_anywhere(uid)



def _trigger_data(trigger: PendingTrigger) -> dict:
    return dict(trigger.trigger_data)



# ── Effect action implementations ──────────────────────────────────────────

def _do_break_shield(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    target_player = data.get("target_player", 1 - controller)
    shield_uid = data.get("shield_uid")
    shield_index = data.get("shield_index")
    if shield_index is None and shield_uid is not None:
        shield = state.players[target_player].find_shield(shield_uid)
        if shield is None:
            return
        shield_index = next((i for i, s in enumerate(state.players[target_player].shield_zone) if s.uid == shield.uid), None)
    if shield_index is None:
        return
    if 0 <= shield_index < len(state.players[target_player].shield_zone):
        move_shield_to_standby(state, target_player, shield_index)



def _do_must_attack(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle MUST_ATTACK effect: creature must attack if able.
    
    Sets a flag that the action generator will check to force attack.
    """
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    creature.temp_flags["must_attack"] = True



def _do_must_block(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle MUST_BLOCK effect: creature must block if able.
    
    Sets a flag that the action generator will check to force block.
    """
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    creature.temp_flags["must_block"] = True



def _do_cannot_block(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle CANNOT_BLOCK effect: creature cannot be chosen as a blocker.
    
    Sets a flag that the action generator will check to prevent blocking.
    """
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    creature.temp_flags["cannot_block"] = True
