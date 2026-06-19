"""test_replacement_apnap.py — Replacement effect APNAP priority (Rule 609.8)."""

import sys
sys.path.insert(0, "dm_engine")

from core.state import GameState, TurnInfo, PlayerState, EffectStack
from core.enums import Phase, Zone
from engine.replacement import ReplacementEffect, ReplacementEffectRegistry, EventType
from core.cards import CardDefinition
from core.zones import Creature


def check(name: str, condition: bool, detail: str = "") -> bool:
    """Print PASS/FAIL."""
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


def make_game_state(turn_player: int = 0) -> GameState:
    """Create a minimal game state for testing."""
    p0 = PlayerState(player_index=0, player_name="P0")
    p1 = PlayerState(player_index=1, player_name="P1")
    state = GameState(
        players=[p0, p1],
        turn_info=TurnInfo(active_player=turn_player, turn_number=1),
        effect_stack=EffectStack(),
    )
    return state


# ──────────────────────────────────────────────────────────────────────────────

def test_single_replacement():
    """Single replacement effect — always chosen."""
    registry = ReplacementEffectRegistry()
    effect = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c1",
        source_card_id=101,
        controller=0,
        replacement_action="flip_face",
    )
    registry.register(effect)
    
    state = make_game_state(turn_player=0)
    chosen = registry.check_and_apply(
        EventType.DESTROY, state, target_uid="c1", controller=0
    )
    check("Single replacement chosen", chosen is effect)


def test_turn_player_before_non_turn_player():
    """Turn player's replacement fires before non-turn player's."""
    registry = ReplacementEffectRegistry()
    
    eff_p0 = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c1",
        source_card_id=101,
        controller=0,
        applies_to="all_creatures",
        replacement_action="flip_face",
    )
    eff_p1 = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c2",
        source_card_id=102,
        controller=1,
        applies_to="all_creatures",
        replacement_action="banish",
    )
    
    registry.register(eff_p1)  # Register non-turn player first
    registry.register(eff_p0)  # Register turn player second
    
    state = make_game_state(turn_player=0)
    chosen = registry.check_and_apply(EventType.DESTROY, state)
    check(
        "Turn player before non-turn player",
        chosen is eff_p0,
        f"expected eff_p0, got {chosen.source_card_id}",
    )


def test_registration_order_within_tier():
    """Within same player tier, earlier registration fires first."""
    registry = ReplacementEffectRegistry()
    
    eff_1 = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c1",
        source_card_id=101,
        controller=0,
        applies_to="all_creatures",
        replacement_action="flip",
    )
    eff_2 = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c2",
        source_card_id=102,
        controller=0,
        applies_to="all_creatures",
        replacement_action="banish",
    )
    eff_3 = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c3",
        source_card_id=103,
        controller=0,
        applies_to="all_creatures",
        replacement_action="prevent",
    )
    
    # Register in reverse order — registration order should win
    registry.register(eff_3)
    registry.register(eff_2)
    registry.register(eff_1)
    
    state = make_game_state(turn_player=0)
    chosen = registry.check_and_apply(EventType.DESTROY, state)
    check(
        "Earlier registration fires first (same player)",
        chosen is eff_3,  # Last registered among same player tier
        f"got {chosen.source_card_id}",
    )


def test_timestamp_tracking():
    """Timestamp counter increments with each registration."""
    registry = ReplacementEffectRegistry()
    
    eff_1 = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c1",
        source_card_id=101,
        controller=0,
    )
    eff_2 = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c2",
        source_card_id=102,
        controller=0,
    )
    
    registry.register(eff_1)
    ts_1 = eff_1.timestamp
    registry.register(eff_2)
    ts_2 = eff_2.timestamp
    
    check(
        "Timestamps increment",
        ts_1 < ts_2,
        f"ts_1={ts_1}, ts_2={ts_2}",
    )


def test_complex_apnap_scenario():
    """Complex: mixed players, multiple replacements."""
    registry = ReplacementEffectRegistry()
    
    # Turn player (0): three replacements
    p0_1 = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c1",
        source_card_id=101,
        controller=0,
        applies_to="all_creatures",
        replacement_action="flip",
    )
    p0_2 = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c2",
        source_card_id=102,
        controller=0,
        applies_to="all_creatures",
        replacement_action="banish",
    )
    p0_3 = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c3",
        source_card_id=103,
        controller=0,
        applies_to="all_creatures",
        replacement_action="prevent",
    )
    
    # Non-turn player (1): two replacements
    p1_1 = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c4",
        source_card_id=104,
        controller=1,
        applies_to="all_creatures",
        replacement_action="banish",
    )
    p1_2 = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c5",
        source_card_id=105,
        controller=1,
        applies_to="all_creatures",
        replacement_action="flip",
    )
    
    # Register in mixed order
    registry.register(p0_1)
    registry.register(p1_2)  # non-turn player
    registry.register(p0_2)
    registry.register(p1_1)  # non-turn player
    registry.register(p0_3)
    
    state = make_game_state(turn_player=0)
    chosen = registry.check_and_apply(EventType.DESTROY, state)
    
    # Should choose the first-registered effect from turn player (p0_1)
    check(
        "Complex APNAP scenario",
        chosen is p0_1,
        f"expected p0_1 (101), got {chosen.source_card_id}",
    )


def test_non_turn_player_active():
    """When non-turn player is active, their replacements fire first."""
    registry = ReplacementEffectRegistry()
    
    eff_p0 = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c1",
        source_card_id=101,
        controller=0,
        applies_to="all_creatures",
        replacement_action="flip",
    )
    eff_p1 = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c2",
        source_card_id=102,
        controller=1,
        applies_to="all_creatures",
        replacement_action="banish",
    )
    
    registry.register(eff_p0)
    registry.register(eff_p1)
    
    # Turn player is 1
    state = make_game_state(turn_player=1)
    chosen = registry.check_and_apply(EventType.DESTROY, state)
    
    # P1 (turn player) should fire first
    check(
        "Non-turn player (now active) fires first",
        chosen is eff_p1,
        f"expected eff_p1, got {chosen.source_card_id}",
    )


def test_replacement_marked_used():
    """Chosen replacement is marked as used."""
    registry = ReplacementEffectRegistry()
    
    eff = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c1",
        source_card_id=101,
        controller=0,
        applies_to="all_creatures",
        replacement_action="flip",
    )
    
    registry.register(eff)
    state = make_game_state(turn_player=0)
    
    check("Before apply, not used", eff.is_used == False)
    
    chosen = registry.check_and_apply(EventType.DESTROY, state)
    
    check("After apply, marked used", eff.is_used == True)
    check("Chosen effect is marked", chosen.is_used == True)


def test_used_replacement_skipped():
    """Already-used replacements are not re-offered."""
    registry = ReplacementEffectRegistry()
    
    eff_used = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c1",
        source_card_id=101,
        controller=0,
        applies_to="all_creatures",
        replacement_action="flip",
        is_used=True,  # Already marked used
    )
    eff_fresh = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c2",
        source_card_id=102,
        controller=0,
        applies_to="all_creatures",
        replacement_action="banish",
    )
    
    registry.register(eff_used)
    registry.register(eff_fresh)
    
    state = make_game_state(turn_player=0)
    chosen = registry.check_and_apply(EventType.DESTROY, state)
    
    check(
        "Used replacement skipped, fresh chosen",
        chosen is eff_fresh,
        f"got {chosen.source_card_id}",
    )


def test_no_applicable_replacement():
    """No applicable replacement returns None."""
    registry = ReplacementEffectRegistry()
    
    eff = ReplacementEffect(
        event_type=EventType.DESTROY,
        source_uid="c1",
        source_card_id=101,
        controller=0,
        applies_to="self",  # Only applies to itself
        replacement_action="flip",
    )
    
    registry.register(eff)
    state = make_game_state(turn_player=0)
    
    # Try to apply with different target
    chosen = registry.check_and_apply(
        EventType.DESTROY, state, target_uid="different", controller=0
    )
    
    check("No applicable replacement returns None", chosen is None)


if __name__ == "__main__":
    print("=" * 60)
    print("Replacement Effect APNAP Priority Tests (Rule 609.8)")
    print("=" * 60)
    
    test_single_replacement()
    test_turn_player_before_non_turn_player()
    test_registration_order_within_tier()
    test_timestamp_tracking()
    test_complex_apnap_scenario()
    test_non_turn_player_active()
    test_replacement_marked_used()
    test_used_replacement_skipped()
    test_no_applicable_replacement()
    
    print("=" * 60)
    print("All replacement APNAP tests completed")
    print("=" * 60)
