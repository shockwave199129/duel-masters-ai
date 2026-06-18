"""engine/trigger_resolver.py — pending triggered effect ordering."""

from __future__ import annotations

from core.cards import CardEffect
from core.state import GameState, PendingTrigger
from core.zones import Creature
from engine.effect_executor import execute_pending_trigger


def should_fire_creature_leave_trigger(creature: Creature) -> bool:
    """Return False for Psychic/Dragheart cells that should not fire leave triggers."""
    return not creature.is_psychic_cell


def queue_trigger(state: GameState, trigger: PendingTrigger) -> GameState:
    """Return a copy with one trigger added to the pending queue."""
    s = state.copy()
    s.effect_stack.add_trigger(trigger)
    return s


def resolve_pending_triggers(state: GameState) -> GameState:
    """
    Resolve pending triggers in queue order.

    Triggers are assumed to have already been ordered by turn-player priority
    before being added, but we still evaluate trigger conditions here so the
    executor only runs valid triggers.
    """
    s = state.copy()
    while s.effect_stack.pending_triggers and not s.is_terminal():
        trigger = s.effect_stack.pop_next_trigger()
        if trigger is None:
            break
        if not _trigger_condition_matches(s, trigger):
            continue
        s = execute_pending_trigger(s, trigger)
    return s


def order_simultaneous_triggers(
    triggers: list[PendingTrigger],
    turn_player: int,
) -> list[PendingTrigger]:
    """Order simultaneous triggers by turn player, then non-turn player."""
    return [
        *[trigger for trigger in triggers if trigger.controller == turn_player],
        *[trigger for trigger in triggers if trigger.controller != turn_player],
    ]


def _trigger_condition_matches(state: GameState, trigger: PendingTrigger) -> bool:
    """Best-effort matcher for the structured trigger_condition payload."""
    condition = trigger.effect.trigger_condition or {}
    if not condition:
        return True

    if (expected := condition.get("controller")) is not None and expected != trigger.controller:
        return False
    if (expected := condition.get("source_uid")) is not None and expected != trigger.source_uid:
        return False
    if (expected := condition.get("source_card_id")) is not None and expected != trigger.source_card_id:
        return False
    if (expected := condition.get("target_uid")) is not None and expected != trigger.trigger_data.get("target_uid"):
        return False

    # Optional source/target creature checks.
    subject_uid = condition.get("subject_uid") or condition.get("target_subject_uid") or trigger.trigger_data.get("subject_uid")
    if subject_uid:
        found = state.find_creature_anywhere(subject_uid)
        if found is None:
            return False
        _, creature = found
        if (expected := condition.get("min_power")) is not None and creature.compute_power(state) < int(expected):
            return False
        if (expected := condition.get("max_power")) is not None and creature.compute_power(state) > int(expected):
            return False
        if (expected := condition.get("must_have_keyword")) is not None and not creature.has_keyword(expected):
            return False

    return True
