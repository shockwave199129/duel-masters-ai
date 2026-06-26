"""
engine/trigger_registry.py — Data-driven trigger dispatch system.

Replaces imperative trigger queueing with a central registry that maps
TriggerEvent → list of (source_uid, CardEffect) for all permanents.

This allows the engine to fire triggers by event type rather than
hardcoding trigger logic in action handlers, phase controller, etc.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict

from core.enums import TriggerEvent
from core.cards import CardEffect


@dataclass
class RegisteredTrigger:
    """A trigger effect registered on a permanent."""
    source_uid: str
    source_card_id: int
    ability_index: int
    effect: CardEffect


class TriggerRegistry:
    """
    Central registry mapping TriggerEvent to registered trigger effects.
    
    Registered per permanent (creature, cross gear, etc.) when it enters
    the battle zone (or other relevant zones). Unregistered when it leaves.
    
    Thread-safe for single-threaded game engine (not for concurrent access).
    """
    
    def __init__(self):
        # trigger_event -> list of RegisteredTrigger
        self._by_event: dict[TriggerEvent, list[RegisteredTrigger]] = defaultdict(list)
        # source_uid -> list of (trigger_event, RegisteredTrigger) for fast unregister
        self._by_source: dict[str, list[tuple[TriggerEvent, RegisteredTrigger]]] = defaultdict(list)
    
    def register(self, source_uid: str, source_card_id: int, ability_index: int, 
                 effect: CardEffect) -> None:
        """Register a triggered effect from a permanent."""
        if effect.trigger_event == TriggerEvent.NONE:
            return
        
        rt = RegisteredTrigger(
            source_uid=source_uid,
            source_card_id=source_card_id,
            ability_index=ability_index,
            effect=effect,
        )
        self._by_event[effect.trigger_event].append(rt)
        self._by_source[source_uid].append((effect.trigger_event, rt))
    
    def unregister_source(self, source_uid: str) -> None:
        """Unregister all triggers for a given source (when permanent leaves play)."""
        if source_uid not in self._by_source:
            return
        
        for trigger_event, rt in self._by_source[source_uid]:
            if rt in self._by_event[trigger_event]:
                self._by_event[trigger_event].remove(rt)
        
        del self._by_source[source_uid]
    
    def get_triggers(self, event: TriggerEvent, controller: Optional[int] = None) -> list[RegisteredTrigger]:
        """
        Get all triggers matching the given event, optionally filtered by controller.
        
        Args:
            event: The TriggerEvent to match
            controller: If provided, only return triggers whose source is controlled by this player
        """
        triggers = self._by_event.get(event, [])
        
        if controller is not None:
            # Filter by controller - need to check the source card's controller
            # This requires access to game state, so we return all and let caller filter
            pass  # Caller should filter using game_state
        
        return triggers
    
    def has_triggers(self, event: TriggerEvent) -> bool:
        """Check if any triggers are registered for this event."""
        return bool(self._by_event.get(event))


# Global registry instance (attached to GameState at runtime)
# Access via: state.trigger_registry

def fire_trigger(
    state,  # GameState - avoid circular import
    event: TriggerEvent,
    trigger_data: dict,
    source_uid: Optional[str] = None,
) -> None:
    """
    Fire all triggers matching the given event.
    
    Creates PendingTrigger objects and adds them to the effect stack
    for resolution via the existing trigger_resolver.
    
    Args:
        state: Current GameState (must have trigger_registry attached)
        event: The TriggerEvent that occurred
        trigger_data: Context dict for condition evaluation (e.g., {"target_uid": "...", "from_zone": "..."})
        source_uid: Optional source card UID that caused the event (for "self" conditions)
    """
    registry = state.trigger_registry
    if not registry or not registry.has_triggers(event):
        return
    
    # Get all matching triggers
    matching = registry.get_triggers(event)
    if not matching:
        return
    
    # Filter by active_in_phase and controller, evaluate conditions
    # Import here to avoid circular
    from engine.trigger_resolver import _eval_condition
    
    # Separate by controller for APNAP ordering
    turn_player = state.active_player
    non_turn = state.inactive_player
    
    turn_triggers = []
    non_turn_triggers = []
    
    for rt in matching:
        effect = rt.effect
        
        # Check phase gating
        if not _effect_active_in_phase(effect, state.current_phase):
            continue
        
        # Check controller - the source card's controller
        source_card = _find_card_by_uid(state, rt.source_uid)
        if not source_card:
            continue
        
        # Build a mock trigger object for condition evaluation
        mock_trigger = type('MockTrigger', (), {
            'controller': source_card.controller,
            'source_uid': rt.source_uid,
            'source_card_id': rt.source_card_id,
            'trigger_data': {**trigger_data, "source_uid": rt.source_uid, "source_card_id": rt.source_card_id},
            'effect': effect,
        })()
        
        # Evaluate trigger condition
        if not _eval_condition(state, mock_trigger, effect.trigger_condition or {}):
            continue
        
        # Separate by controller for APNAP
        if source_card.controller == turn_player:
            turn_triggers.append(rt)
        else:
            non_turn_triggers.append(rt)
    
    # Queue triggers in APNAP order: turn player first, then non-turn player
    for rt in turn_triggers:
        _queue_trigger(state, rt, trigger_data)
    
    for rt in non_turn_triggers:
        _queue_trigger(state, rt, trigger_data)


def _find_card_by_uid(state, uid: str):
    """Find a card in any zone by UID. Returns object with .controller attribute."""
    for player_idx in (0, 1):
        p_state = state.players[player_idx]
        for zone in (p_state.battle_zone, p_state.mana_zone, p_state.shield_zone,
                     p_state.hand, p_state.graveyard, p_state.ultra_gr_zone,
                     p_state.hyperspatial_zone):
            for card in zone:
                if card.uid == uid:
                    return card
    return None


def _effect_active_in_phase(effect, phase) -> bool:
    """Check if effect is active in current phase (inline to avoid circular import)."""
    active_phases = effect.active_in_phase
    if not active_phases:
        return False
    if "any" in active_phases:
        return True
    phase_name = phase.name.lower()
    if phase_name in active_phases:
        return True
    if hasattr(phase, 'is_attack_subphase') and phase.is_attack_subphase() and "attack" in active_phases:
        return True
    return False


def _queue_trigger(state, rt: RegisteredTrigger, trigger_data: dict) -> None:
    """Create and queue a PendingTrigger for resolution."""
    from engine.trigger_resolver import PendingTrigger
    from core.enums import Keyword
    
    # Build trigger data with source info
    full_trigger_data = {
        **trigger_data,
        "source_uid": rt.source_uid,
        "source_card_id": rt.source_card_id,
        "ability_index": rt.ability_index,
    }
    
    # Find the controller from the source card
    source_card = _find_card_by_uid(state, rt.source_uid)
    controller = source_card.controller if source_card else 0
    
    # Determine APNAP priority (Rule 101.4a):
    # 0 = S-Trigger (shield trigger keyword — always first)
    # 1 = active player's triggers
    # 2 = non-active player's triggers
    apnap_priority = -1  # unassigned legacy default
    if source_card is not None:
        defn = getattr(source_card, "definition", None)
        if defn is not None and Keyword.SHIELD_TRIGGER in defn.keywords:
            apnap_priority = 0
        elif controller == state.active_player:
            apnap_priority = 1
        else:
            apnap_priority = 2
    
    pending = PendingTrigger(
        effect=rt.effect,
        source_uid=rt.source_uid,
        source_card_id=rt.source_card_id,
        controller=controller,
        trigger_data=full_trigger_data,
        priority=apnap_priority,
    )
    state.effect_stack.add_pending_trigger(pending)