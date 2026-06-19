"""test_cost_modifiers.py — Cost modifier lookup (Rule 112.2b)."""

import sys
sys.path.insert(0, "dm_engine")

from core.state import GameState, TurnInfo, PlayerState, EffectStack
from core.global_effects import GlobalEffect, GlobalEffectRegistry, GlobalEffectType


def check(name: str, condition: bool, detail: str = "") -> bool:
    """Print PASS/FAIL."""
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


# ──────────────────────────────────────────────────────────────────────────────

def test_no_modifiers():
    """No cost modifiers — returns 0."""
    registry = GlobalEffectRegistry()
    mod = registry.get_cost_modifiers(player=0, card_id=100)
    check("No modifiers", mod == 0)


def test_single_cost_reduce():
    """Single COST_REDUCE effect."""
    registry = GlobalEffectRegistry()
    
    effect = GlobalEffect(
        effect_type=GlobalEffectType.COST_REDUCE,
        applied_by_uid="c1",
        applied_by_card=101,
        controller=0,
        target_player=None,
        cost_mod_amount=2,
    )
    registry.add(effect)
    
    mod = registry.get_cost_modifiers(player=0, card_id=100)
    check("Cost reduce", mod == -2, f"got {mod}")


def test_single_cost_increase():
    """Single COST_INCREASE effect."""
    registry = GlobalEffectRegistry()
    
    effect = GlobalEffect(
        effect_type=GlobalEffectType.COST_INCREASE,
        applied_by_uid="c1",
        applied_by_card=101,
        controller=0,
        target_player=None,
        cost_mod_amount=1,
    )
    registry.add(effect)
    
    mod = registry.get_cost_modifiers(player=0, card_id=100)
    check("Cost increase", mod == 1, f"got {mod}")


def test_mixed_modifiers():
    """Mixed reduce and increase."""
    registry = GlobalEffectRegistry()
    
    registry.add(GlobalEffect(
        effect_type=GlobalEffectType.COST_REDUCE,
        applied_by_uid="c1",
        applied_by_card=101,
        controller=0,
        target_player=None,
        cost_mod_amount=3,
    ))
    registry.add(GlobalEffect(
        effect_type=GlobalEffectType.COST_INCREASE,
        applied_by_uid="c2",
        applied_by_card=102,
        controller=0,
        target_player=None,
        cost_mod_amount=1,
    ))
    
    mod = registry.get_cost_modifiers(player=0, card_id=100)
    check("Mixed modifiers", mod == -2, f"got {mod}")


def test_player_filtering():
    """Modifiers filter by target player."""
    registry = GlobalEffectRegistry()
    
    registry.add(GlobalEffect(
        effect_type=GlobalEffectType.COST_REDUCE,
        applied_by_uid="c1",
        applied_by_card=101,
        controller=0,
        target_player=0,  # Only for player 0
        cost_mod_amount=2,
    ))
    registry.add(GlobalEffect(
        effect_type=GlobalEffectType.COST_INCREASE,
        applied_by_uid="c2",
        applied_by_card=102,
        controller=1,
        target_player=1,  # Only for player 1
        cost_mod_amount=1,
    ))
    
    mod_p0 = registry.get_cost_modifiers(player=0, card_id=100)
    mod_p1 = registry.get_cost_modifiers(player=1, card_id=100)
    
    check("Player 0 gets P0 effect", mod_p0 == -2, f"got {mod_p0}")
    check("Player 1 gets P1 effect", mod_p1 == 1, f"got {mod_p1}")


def test_stack_multiple_reductions():
    """Multiple reductions stack."""
    registry = GlobalEffectRegistry()
    
    for i in range(3):
        registry.add(GlobalEffect(
            effect_type=GlobalEffectType.COST_REDUCE,
            applied_by_uid=f"c{i}",
            applied_by_card=100 + i,
            controller=0,
            target_player=None,
            cost_mod_amount=1,
        ))
    
    mod = registry.get_cost_modifiers(player=0, card_id=100)
    check("Stacking reductions", mod == -3, f"got {mod}")


if __name__ == "__main__":
    print("=" * 60)
    print("Cost Modifier Tests (Rule 112.2b)")
    print("=" * 60)
    
    test_no_modifiers()
    test_single_cost_reduce()
    test_single_cost_increase()
    test_mixed_modifiers()
    test_player_filtering()
    test_stack_multiple_reductions()
    
    print("=" * 60)
    print("All tests completed")
    print("=" * 60)
