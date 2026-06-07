"""
engine/trigger_resolver.py — pending triggered effect ordering.
"""

from __future__ import annotations

from core.state import GameState, PendingTrigger
from engine.effect_executor import execute_pending_trigger


def queue_trigger(state: GameState, trigger: PendingTrigger) -> GameState:
    """Return a copy with one trigger added to the pending queue."""
    s = state.copy()
    s.effect_stack.add_trigger(trigger)
    return s


def resolve_pending_triggers(state: GameState) -> GameState:
    """
    Resolve pending triggers in queue order.

    The queue should be populated in S-Trigger/turn-player priority order by
    the detector that creates the triggers.
    """
    s = state.copy()
    while s.effect_stack.pending_triggers and not s.is_terminal():
        trigger = s.effect_stack.pop_next_trigger()
        if trigger is None:
            break
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
