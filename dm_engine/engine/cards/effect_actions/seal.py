"""engine/cards/effect_actions/seal.py — Seal attachment effects."""
from __future__ import annotations

from core.state import GameState, PendingTrigger
from core.zones import Creature
from core.cards import CardDefinition

# ── Shared helpers ──────────────────────────────────────────────────────────

def _find_creature(state: GameState, uid: str) -> tuple[int, Creature] | None:
    if not uid:
        return None
    return state.find_creature_anywhere(uid)



def _trigger_data(trigger: PendingTrigger) -> dict:
    return dict(trigger.trigger_data)



# ── Effect action implementations ──────────────────────────────────────────

def _do_attach_seal(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    seal_def = data.get("seal_definition")
    found = _find_creature(state, target_uid)
    if not found or not isinstance(seal_def, CardDefinition):
        return
    _, creature = found
    creature.seals.append(seal_def)



def _do_remove_seal(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    if creature.seals:
        creature.seals.pop(0)
