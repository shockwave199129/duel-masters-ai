"""
tests/test_is_replacement_validation.py — verify that is_replacement_effect()
checks BOTH the boolean field and the effect_type enum.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.cards import CardEffect
from core.enums import EffectAction, EffectType, TriggerEvent

PASS = "✅"
FAIL = "❌"
results = []

def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))
    return ok

def make_effect(is_replacement_bool, effect_type):
    return CardEffect(
        card_id=1,
        ability_index=0,
        raw_text="test effect",
        effect_type=effect_type,
        trigger_event=TriggerEvent.ON_DESTROY,
        effect_action=EffectAction.NONE,
        trigger_condition={},
        effect_target={},
        effect_value={},
        is_optional=False,
        is_replacement=is_replacement_bool,
        active_in_phase=("any",),
        active_in_zone=("battle_zone",),
        parse_confidence=0.9,
    )

print("\n" + "═" * 60)
print("  IS_REPLACEMENT VALIDATION TESTS")
print("═" * 60)

# Test 1: is_replacement=True, effect_type=STATIC → True (boolean wins)
e1 = make_effect(True, EffectType.STATIC)
check("is_replacement=True + STATIC → True", e1.is_replacement_effect())

# Test 2: is_replacement=False, effect_type=REPLACEMENT → True (enum wins)
e2 = make_effect(False, EffectType.REPLACEMENT)
check("is_replacement=False + REPLACEMENT → True", e2.is_replacement_effect())

# Test 3: is_replacement=True, effect_type=REPLACEMENT → True (both)
e3 = make_effect(True, EffectType.REPLACEMENT)
check("is_replacement=True + REPLACEMENT → True", e3.is_replacement_effect())

# Test 4: is_replacement=False, effect_type=STATIC → False (neither)
e4 = make_effect(False, EffectType.STATIC)
check("is_replacement=False + STATIC → False", not e4.is_replacement_effect())

# Test 5: needs_rag_fallback with confidence < 0.70
e5 = CardEffect(
    card_id=1, ability_index=0, raw_text="low confidence",
    effect_type=EffectType.STATIC, trigger_event=TriggerEvent.ON_DESTROY,
    effect_action=EffectAction.NONE, trigger_condition={},
    effect_target={}, effect_value={}, is_optional=False,
    is_replacement=False, active_in_phase=("any",),
    active_in_zone=("battle_zone",), parse_confidence=0.5,
)
check("needs_rag_fallback() True for confidence 0.5", e5.needs_rag_fallback())

# Test 6: needs_rag_fallback with confidence >= 0.70
e6 = CardEffect(
    card_id=1, ability_index=0, raw_text="high confidence",
    effect_type=EffectType.STATIC, trigger_event=TriggerEvent.ON_DESTROY,
    effect_action=EffectAction.NONE, trigger_condition={},
    effect_target={}, effect_value={}, is_optional=False,
    is_replacement=False, active_in_phase=("any",),
    active_in_zone=("battle_zone",), parse_confidence=0.85,
)
check("needs_rag_fallback() False for confidence 0.85", not e6.needs_rag_fallback())

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
