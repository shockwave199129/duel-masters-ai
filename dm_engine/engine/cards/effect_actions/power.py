"""engine/cards/effect_actions/power.py — Power modification effects."""
from __future__ import annotations

from core.state import GameState, PendingTrigger
from core.zones import Creature
from core.zones import PowerModifier

# ── Shared helpers ──────────────────────────────────────────────────────────

def _effect_value(trigger: PendingTrigger) -> dict:
    return dict(trigger.effect.effect_value)



def _find_creature(state: GameState, uid: str) -> tuple[int, Creature] | None:
    if not uid:
        return None
    return state.find_creature_anywhere(uid)



def _source_uid(trigger: PendingTrigger) -> str:
    return trigger.source_uid


def _trigger_data(trigger: PendingTrigger) -> dict:
    return dict(trigger.trigger_data)



# ── Effect action implementations ──────────────────────────────────────────

def _do_power_modify(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    amount = int(effect.get("amount", 0) or 0)
    if amount == 0:
        return
    creature.power_modifiers.append(
        PowerModifier(
            source_uid=_source_uid(trigger),
            amount=amount,
            duration=effect.get("duration", "while_in_play"),
        )
    )



def _do_power_fix(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    fixed = effect.get("fixed_value")
    if fixed is None:
        fixed = effect.get("amount")
    if fixed is None:
        return
    creature.temp_flags["_power_fix"] = int(fixed)
