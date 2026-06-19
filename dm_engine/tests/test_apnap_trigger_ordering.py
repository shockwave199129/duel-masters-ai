"""test_apnap_trigger_ordering.py — APNAP trigger resolution order (Rule 101.4)."""

import sys
sys.path.insert(0, "dm_engine")

from core.cards import CardEffect, EffectAction, TriggerEvent, EffectType
from core.state import PendingTrigger
from engine.trigger_resolver import order_simultaneous_triggers


def make_effect(card_id: int = 100) -> CardEffect:
    """Create a minimal CardEffect for testing."""
    return CardEffect(
        card_id=card_id,
        ability_index=0,
        raw_text="Test",
        effect_type=EffectType.TRIGGERED,
        trigger_event=TriggerEvent.ON_SUMMON,
        effect_action=EffectAction.DRAW,
        trigger_condition={},
        effect_target={},
        effect_value={},
        is_optional=False,
        is_replacement=False,
        active_in_phase=(),
        active_in_zone=(),
        parse_confidence=1.0,
    )


def check(name: str, condition: bool, detail: str = "") -> bool:
    """Print PASS/FAIL."""
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


# ──────────────────────────────────────────────────────────────────────────────

def test_single_trigger():
    """Single trigger unchanged."""
    trigger = PendingTrigger(
        effect=make_effect(101),
        source_uid="c1",
        source_card_id=101,
        controller=0,
        priority=-1,
    )
    ordered = order_simultaneous_triggers([trigger], turn_player=0)
    check("Single trigger", len(ordered) == 1 and ordered[0] == trigger)


def test_turn_player_first():
    """Turn player triggers before non-turn player."""
    t_p0 = PendingTrigger(make_effect(101), "c1", 101, 0, priority=-1)
    t_p1 = PendingTrigger(make_effect(102), "c2", 102, 1, priority=-1)
    ordered = order_simultaneous_triggers([t_p1, t_p0], turn_player=0)
    check(
        "Turn player (0) before non-turn (1)",
        ordered[0].controller == 0 and ordered[1].controller == 1,
    )


def test_priority_ordering():
    """Lower priority value fires first."""
    t0 = PendingTrigger(make_effect(101), "c1", 101, 0, priority=0)
    t1 = PendingTrigger(make_effect(102), "c2", 102, 0, priority=1)
    t2 = PendingTrigger(make_effect(103), "c3", 103, 0, priority=2)
    ordered = order_simultaneous_triggers([t2, t0, t1], turn_player=0)
    priorities = [t.priority for t in ordered]
    check("Priority order (0,1,2)", priorities == [0, 1, 2])


def test_unset_priority_last():
    """Priority -1 (unset) sorts last within tier."""
    t0 = PendingTrigger(make_effect(101), "c1", 101, 0, priority=0)
    t1 = PendingTrigger(make_effect(102), "c2", 102, 0, priority=-1)
    ordered = order_simultaneous_triggers([t1, t0], turn_player=0)
    check(
        "Unset priority last",
        ordered[0].priority == 0 and ordered[1].priority == -1,
    )


def test_complex_apnap():
    """Complex: mixed players and priorities."""
    # Turn player: pri=1, pri=-1, pri=0
    p0_1 = PendingTrigger(make_effect(101), "c1", 101, 0, priority=1)
    p0_u = PendingTrigger(make_effect(102), "c2", 102, 0, priority=-1)
    p0_0 = PendingTrigger(make_effect(103), "c3", 103, 0, priority=0)
    # Non-turn: pri=1, pri=0
    p1_1 = PendingTrigger(make_effect(104), "c4", 104, 1, priority=1)
    p1_0 = PendingTrigger(make_effect(105), "c5", 105, 1, priority=0)
    
    ordered = order_simultaneous_triggers([p0_1, p1_1, p0_u, p1_0, p0_0], turn_player=0)
    ids = [t.source_card_id for t in ordered]
    # Expected: P0(pri=0,1,unset), P1(pri=0,1) = 103, 101, 102, 105, 104
    expected = [103, 101, 102, 105, 104]
    check("Complex APNAP", ids == expected, f"got {ids}")


def test_empty_list():
    """Empty list handled."""
    ordered = order_simultaneous_triggers([], turn_player=0)
    check("Empty list", len(ordered) == 0)


def test_many_triggers():
    """Many triggers sort by priority."""
    triggers = [
        PendingTrigger(make_effect(1000 + i), f"c{i}", 1000 + i, 0, priority=i % 3)
        for i in range(10)
    ]
    ordered = order_simultaneous_triggers(triggers, turn_player=0)
    priorities = [t.priority for t in ordered]
    # Should see 0s first, then 1s, then 2s
    try:
        idx_0 = next(i for i, p in enumerate(priorities) if p == 0)
        idx_1 = next(i for i, p in enumerate(priorities) if p == 1)
        idx_2 = next(i for i, p in enumerate(priorities) if p == 2)
        check("Many triggers", idx_0 < idx_1 < idx_2)
    except StopIteration:
        check("Many triggers", False, "Missing priority value")


if __name__ == "__main__":
    print("=" * 60)
    print("APNAP Trigger Ordering Tests (Rule 101.4)")
    print("=" * 60)
    
    test_single_trigger()
    test_turn_player_first()
    test_priority_ordering()
    test_unset_priority_last()
    test_complex_apnap()
    test_empty_list()
    test_many_triggers()
    
    print("=" * 60)
    print("All APNAP tests completed")
    print("=" * 60)
