"""engine/trigger_resolver.py — pending triggered effect ordering."""

from __future__ import annotations

import logging

from core.state import AwaitedChoice, GameState, PendingTrigger
from core.zones import Creature
from engine.effect_executor import execute_pending_trigger
from engine.shield_break_window import finish_spell


def should_fire_creature_leave_trigger(creature: Creature) -> bool:
    """Return False for Psychic/Dragheart cells that should not fire leave triggers."""
    return not creature.is_psychic_cell


def queue_trigger(state: GameState, trigger: PendingTrigger) -> GameState:
    """Return a copy with one trigger added to the pending queue."""
    s = state.copy()
    s.effect_stack.add_trigger(trigger)
    return s


logger = logging.getLogger(__name__)
MAX_TRIGGERS_PER_RESOLUTION = 100


def _order_options(state: GameState) -> list[int]:
    """
    Positions of the effects their controller must choose between (rule 605.1).

    Assumes the queue is sorted. Identical effects count once, because their
    order cannot matter.
    """
    pending = state.effect_stack.pending_triggers
    first = pending[0]
    if first.immediate or first.priority >= 0:
        return []
    seen, positions = set(), []
    for index, trigger in enumerate(pending):
        if trigger.immediate or trigger.priority >= 0 or trigger.controller != first.controller:
            continue
        kind = (trigger.source_uid, trigger.effect.card_id, trigger.effect.ability_index,
                trigger.effect.effect_value.get("script"))
        if kind not in seen:
            seen.add(kind)
            positions.append(index)
    return positions if len(positions) > 1 else []


def _ask_order(state: GameState, positions: list[int]) -> None:
    pending = state.effect_stack.pending_triggers
    first = pending[positions[0]]
    options = [f"effect:{index}" for index in positions]
    state.effect_stack.set_choice(AwaitedChoice(
        choice_type="order_effects",
        player=first.controller,
        effect=first.effect,
        source_uid=first.source_uid,
        valid_options=options,
        prompt="Choose which effect resolves first",
        option_cards={
            option: pending[index].source_card_id
            for option, index in zip(options, positions) if pending[index].source_card_id
        },
    ))


def resolve_pending_triggers(state: GameState) -> GameState:
    """
    Resolve waiting effects one at a time.

    A card being used resolves first. Then the turn player resolves theirs in
    any order, then the other player. While shields are being broken, other
    effects wait until the S-Triggers are done (rule 101.4). Conditions are
    checked as each effect resolves (rule 603.4).
    
    Rule 101.4d: While resolving an effect, other triggers cannot interrupt.
    If currently_resolving_effect is True, triggers added during resolution
    are left in the queue (standby) and not processed until the effect finishes.
    """
    s = state.copy()
    resolved = 0
    # Stop at a player choice: the paused effect must be answered first
    while (
        s.effect_stack.pending_triggers
        and not s.effect_stack.is_waiting_for_choice()
        and not s.is_terminal()
    ):
        if resolved >= MAX_TRIGGERS_PER_RESOLUTION:
            logger.warning(
                "Dropped %s trigger(s) after %s resolutions; effects are probably re-triggering each other",
                len(s.effect_stack.pending_triggers),
                resolved,
            )
            s.effect_stack.pending_triggers.clear()
            for player in s.players:  # no spell is left stuck half-used
                for card in list(player.resolving):
                    finish_spell(s, player.player_index, card.uid)
            break
        s.effect_stack.sort_pending(s.active_player)
        if (s.effect_stack.shield_break_window is not None
                and not s.effect_stack.pending_triggers[0].immediate):
            break
        positions = _order_options(s)
        if positions:
            _ask_order(s, positions)
            break
        resolved += 1
        trigger = s.effect_stack.pop_next_trigger(s.active_player)
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


def _controller_matches(expected, trigger: PendingTrigger) -> bool:
    """Parsed conditions name the event's player relative to the ability's controller."""
    if isinstance(expected, int):
        return expected == trigger.controller
    event_player = trigger.trigger_data.get("controller")
    if event_player is None:
        return True
    word = str(expected).strip().lower()
    if word in ("self", "you", "owner", "your", "player"):
        return event_player == trigger.controller
    if word == "opponent":
        return event_player != trigger.controller
    return True  # "any", "either" and wording we cannot read


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

    if (expected := condition.get("controller")) is not None and not _controller_matches(expected, trigger):
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
