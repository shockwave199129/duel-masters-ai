"""
tests/test_power_calculation_layers.py — Layer 7 (POWER_TOUGHNESS) effects
from the layer system are consumed by compute_power().

Validates that:
  - Layer 7a fix effects override computed power
  - Layer 7b modifiers stack on top of computed power
  - Layer effects are independent from global_effects
  - No regression when no layer effects are registered
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.cards import CardDefinition
from core.enums import (
    CardSubtype, CardType, Civilization, GlobalEffectType, Phase,
)
from core.global_effects import GlobalEffect, GlobalEffectRegistry
from core.player_state import PlayerState
from core.state import GameState, TurnInfo
from core.zones import Creature
from engine.layers import Layer, LayerEffectRegistry

PASS = "✅"
FAIL = "❌"
results = []

def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))
    return ok

def card_def(cid, name, power=1000):
    return CardDefinition(
        id=cid, slug=name.lower().replace(" ", "_"), name=name, cost=3,
        power=power, card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]),
        races=frozenset({"Human"}), keywords=frozenset(), effects=tuple(),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )

def make_state(p0_battle=None):
    filler = card_def(99, "deck")
    return GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", deck=[filler],
                        battle_zone=p0_battle or []),
            PlayerState(player_index=1, player_name="P1", deck=[filler]),
        ),
        turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
    )

print("\n" + "═" * 60)
print("  POWER CALCULATION LAYER TESTS")
print("═" * 60)

# Test 1: No layer effects → base power unchanged
s = make_state()
creat = Creature(definition=card_def(1, "plain", power=3000), controller=0, owner=0)
s.players[0].battle_zone = [creat]
power = creat.compute_power(s)
check("No layer effects: power = base", power == 3000, f"got {power}")

# Test 2: Layer 7b modifier adds to base power
s = make_state()
creat = Creature(definition=card_def(2, "buff_target", power=2000), controller=0, owner=0)
s.players[0].battle_zone = [creat]
s.layer_effects.add(
    GlobalEffect(
        effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
        applied_by_uid="layer_src", applied_by_card=900,
        controller=0, target_player=None, power_mod_amount=1500,
    ),
    Layer.POWER_TOUGHNESS,
)
power = creat.compute_power(s)
check("Layer 7b: 2000 + 1500 = 3500", power == 3500, f"got {power}")

# Test 3: Layer 7a fix overrides base + modifiers
s = make_state()
creat = Creature(definition=card_def(3, "fix_target", power=2000), controller=0, owner=0)
s.players[0].battle_zone = [creat]
s.layer_effects.add(
    GlobalEffect(
        effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
        applied_by_uid="layer_mod", applied_by_card=901,
        controller=0, target_player=None, power_mod_amount=1500,
    ),
    Layer.POWER_TOUGHNESS,
)
s.layer_effects.add(
    GlobalEffect(
        effect_type=GlobalEffectType.ALL_CREATURES_POWER_FIX,
        applied_by_uid="layer_fix", applied_by_card=902,
        controller=0, target_player=0, power_mod_amount=500,
    ),
    Layer.POWER_TOUGHNESS,
)
power = creat.compute_power(s)
check("Layer 7a fix: 500 (overrides 2000+1500)", power == 500, f"got {power}")

# Test 4: Layer effects only apply to matching controller
s = make_state()
creat = Creature(definition=card_def(4, "p1_creature", power=3000), controller=1, owner=1)
s.players[1].battle_zone = [creat]
s.layer_effects.add(
    GlobalEffect(
        effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
        applied_by_uid="layer_p0", applied_by_card=903,
        controller=0, target_player=0, power_mod_amount=2000,
    ),
    Layer.POWER_TOUGHNESS,
)
power = creat.compute_power(s)
check("Layer 7b: controller 1 not affected by controller 0 layer effect",
      power == 3000, f"got {power}")

# Test 5: Layer effects stack with global_effects
s = make_state()
creat = Creature(definition=card_def(5, "stacked", power=1000), controller=0, owner=0)
s.players[0].battle_zone = [creat]
s.global_effects.add(
    GlobalEffect(
        effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
        applied_by_uid="global_mod", applied_by_card=904,
        controller=0, target_player=None, power_mod_amount=500,
    )
)
s.layer_effects.add(
    GlobalEffect(
        effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
        applied_by_uid="layer_mod2", applied_by_card=905,
        controller=0, target_player=None, power_mod_amount=300,
    ),
    Layer.POWER_TOUGHNESS,
)
power = creat.compute_power(s)
check("Global + layer: 1000 + 500 (global) + 300 (layer) = 1800",
      power == 1800, f"got {power}")

# Test 6: Layer effects cleaned up when creature leaves
s = make_state()
creat = Creature(definition=card_def(6, "leaving", power=2000), controller=0, owner=0)
s.players[0].battle_zone = [creat]
s.layer_effects.add(
    GlobalEffect(
        effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
        applied_by_uid=creat.uid,  # matches the creature's uid for cleanup
        applied_by_card=906,
        controller=0, target_player=None, power_mod_amount=1000,
    ),
    Layer.POWER_TOUGHNESS,
)
power_before = creat.compute_power(s)
check("Before cleanup: 2000 + 1000 = 3000", power_before == 3000, f"got {power_before}")
creat.remove_static_effects(s)
power_after = creat.compute_power(s)
check("After cleanup: 2000 (layer effect removed)", power_after == 2000, f"got {power_after}")

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
