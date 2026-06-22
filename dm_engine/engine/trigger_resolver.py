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
    
    Rule 101.4d: While resolving an effect, other triggers cannot interrupt.
    If currently_resolving_effect is True, triggers added during resolution
    are left in the queue (standby) and not processed until the effect finishes.
    """
    s = state.copy()
    while s.effect_stack.pending_triggers and not s.is_terminal():
        trigger = s.effect_stack.pop_next_trigger()
        if trigger is None:
            break
        if not _trigger_condition_matches(s, trigger):
            continue
        # If we're in the middle of resolving an effect, triggers can only
        # interrupt if they're replacement effects (rule 101.4d).
        # For now, just note the flag — most triggers aren't replacement effects yet.
        s = execute_pending_trigger(s, trigger)
    return s


def order_simultaneous_triggers(
    triggers: list[PendingTrigger],
    turn_player: int,
) -> list[PendingTrigger]:
    """
    Order simultaneous triggers by APNAP (Active Player, Non-Active Player):
    
    Three-tier sort:
      1. Turn player's triggers first, then non-turn player's triggers
      2. Within each tier, sort by priority (lower = earlier; -1 = not set, sorted last)
      3. Within same tier/priority, preserve registration order (stable sort)
    
    Rule 101.4: Turn player declares order of their simultaneous triggers;
    non-turn player declares order of theirs.
    """
    def sort_key(trigger: PendingTrigger) -> tuple:
        is_turn_player = 0 if trigger.controller == turn_player else 1
        # Priority -1 (not set) sorts last within tier; else sort by priority value (lower = earlier)
        if trigger.priority < 0:
            # Not set: sorts last (high value)
            priority_val = (1, 999999)
        else:
            # Set: sorts earlier (low value first)
            priority_val = (0, trigger.priority)
        return (is_turn_player, priority_val)
    
    return sorted(triggers, key=sort_key)


def _trigger_condition_matches(state: GameState, trigger: PendingTrigger) -> bool:
    """Best-effort matcher for the structured trigger_condition payload."""
    condition = trigger.effect.trigger_condition or {}
    if not condition:
        return True

    return _eval_condition(state, trigger, condition)


def _eval_condition(state: GameState, trigger: PendingTrigger, condition: dict) -> bool:
    """Evaluate a single condition dict against the current game state.

    Supports the original flat keys (controller, source_uid, etc.) plus:
      - from_zone / to_zone: zone-change checks against trigger_data
      - min_turn / max_turn: turn counter checks against state.turn_info
      - shield_count_min / shield_count_max: controller shield count
      - any_of: list of sub-conditions (OR — any match passes)
      - not: single sub-condition dict (negation)
    """
    # --- OR combinator ---
    if "any_of" in condition:
        sub_conditions = condition["any_of"]
        if not isinstance(sub_conditions, list):
            return False
        return any(
            _eval_condition(state, trigger, sub) for sub in sub_conditions
        )

    # --- Negation ---
    if "not" in condition:
        sub = condition["not"]
        if not isinstance(sub, dict):
            return True
        return not _eval_condition(state, trigger, sub)

    # --- Original flat keys ---

    if (expected := condition.get("controller")) is not None and expected != trigger.controller:
        return False
    if (expected := condition.get("source_uid")) is not None and expected != trigger.source_uid:
        return False
    if (expected := condition.get("source_card_id")) is not None and expected != trigger.source_card_id:
        return False
    if (expected := condition.get("target_uid")) is not None and expected != trigger.trigger_data.get("target_uid"):
        return False

    # --- Zone-change conditions ---
    if (expected := condition.get("from_zone")) is not None:
        actual_from = trigger.trigger_data.get("from_zone")
        if actual_from is None or str(actual_from) != str(expected):
            return False
    if (expected := condition.get("to_zone")) is not None:
        actual_to = trigger.trigger_data.get("to_zone")
        if actual_to is None or str(actual_to) != str(expected):
            return False

    # --- Turn counter conditions ---
    turn_num = state.turn_info.turn_number
    if (expected := condition.get("min_turn")) is not None and turn_num < int(expected):
        return False
    if (expected := condition.get("max_turn")) is not None and turn_num > int(expected):
        return False

    # --- Player shield count conditions ---
    controller_shield_count = state.players[trigger.controller].shield_count
    if (expected := condition.get("shield_count_min")) is not None and controller_shield_count < int(expected):
        return False
    if (expected := condition.get("shield_count_max")) is not None and controller_shield_count > int(expected):
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
