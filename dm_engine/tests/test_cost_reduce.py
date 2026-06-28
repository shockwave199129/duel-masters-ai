"""
test_cost_reduce.py — verify COST_REDUCE static effect registration.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.cards import CardDefinition, CardEffect
from core.enums import (
    CardSubtype, CardType, Civilization, EffectAction,
    EffectType, Keyword, Phase, TriggerEvent,
)
from core.global_effects import GlobalEffect, GlobalEffectType
from core.player_state import PlayerState
from core.state import GameState, TurnInfo
from core.zones import Creature

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
        id=cid,
        slug=name.lower().replace(" ", "_"),
        name=name,
        cost=3,
        power=power,
        card_type=CardType.CREATURE,
        card_subtype=CardSubtype.NONE,
        civilizations=frozenset({Civilization.FIRE}),
        races=frozenset({"Human"}),
        keywords=frozenset(),
        effects=tuple(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
    )


def cost_reduce_effect(amount, scope="own"):
    return CardEffect(
        card_id=0,
        ability_index=0,
        raw_text=f"Your spells cost {amount} less",
        effect_type=EffectType.STATIC,
        effect_action=EffectAction.COST_REDUCE,
        effect_target={"scope": scope, "amount": amount},
        effect_value={"amount": amount},
        trigger_event=TriggerEvent.NONE,
        trigger_condition={},
        is_optional=False,
        is_replacement=False,
        active_in_phase=("any",),
        active_in_zone=("battle_zone",),
        parse_confidence=1.0,
    )


def cost_increase_effect(amount, scope="opponent"):
    return CardEffect(
        card_id=0,
        ability_index=0,
        raw_text=f"Your opponent's spells cost {amount} more",
        effect_type=EffectType.STATIC,
        effect_action=EffectAction.COST_INCREASE,
        effect_target={"scope": scope, "amount": amount},
        effect_value={"amount": amount},
        trigger_event=TriggerEvent.NONE,
        trigger_condition={},
        is_optional=False,
        is_replacement=False,
        active_in_phase=("any",),
        active_in_zone=("battle_zone",),
        parse_confidence=1.0,
    )


def make_test_state(p0_battle=None, p1_battle=None):
    filler = card_def(99, "DeckFiller")
    return GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0", deck=[filler],
                battle_zone=p0_battle or [],
            ),
            PlayerState(
                player_index=1, player_name="P1", deck=[filler],
                battle_zone=p1_battle or [],
            ),
        ),
        turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
    )


# ── Test 1: No cost reducer ─────────────────────────────────────────────────
print("Test 1: No cost reducer")
state = make_test_state()
check(
    "get_cost_modifiers(0, 999) == 0 with empty board",
    state.global_effects.get_cost_modifiers(0, 999) == 0,
    f"got {state.global_effects.get_cost_modifiers(0, 999)}",
)

# ── Test 2: With COST_REDUCE creature ───────────────────────────────────────
print("\nTest 2: Single COST_REDUCE creature (own)")
defn = card_def(100, "Cost Reducer")
defn = CardDefinition(
    id=defn.id, slug=defn.slug, name=defn.name, cost=defn.cost, power=defn.power,
    card_type=defn.card_type, card_subtype=defn.card_subtype,
    civilizations=defn.civilizations, races=defn.races, keywords=defn.keywords,
    effects=(cost_reduce_effect(1, scope="own"),),
    evolution_source_races=defn.evolution_source_races,
    evolution_source_types=defn.evolution_source_types,
    is_multiface=defn.is_multiface,
)
creature = Creature(definition=defn, controller=0, owner=0)
creature.has_summoning_sickness = False
state = make_test_state(p0_battle=[creature])

creature.apply_static_effects(state)

p0_mod = state.global_effects.get_cost_modifiers(0, 999)
check(
    "Player 0 gets -1 cost reduction",
    p0_mod == -1,
    f"got {p0_mod}",
)
p1_mod = state.global_effects.get_cost_modifiers(1, 999)
check(
    "Player 1 gets no reduction (scope=own)",
    p1_mod == 0,
    f"got {p1_mod}",
)

# ── Test 3: Multiple cost reducers stack ─────────────────────────────────────
print("\nTest 3: Multiple COST_REDUCE creatures stack")
defn2 = card_def(101, "Cost Reducer 2")
defn2 = CardDefinition(
    id=defn2.id, slug=defn2.slug, name=defn2.name, cost=defn2.cost, power=defn2.power,
    card_type=defn2.card_type, card_subtype=defn2.card_subtype,
    civilizations=defn2.civilizations, races=defn2.races, keywords=defn2.keywords,
    effects=(cost_reduce_effect(1, scope="own"),),
    evolution_source_races=defn2.evolution_source_races,
    evolution_source_types=defn2.evolution_source_types,
    is_multiface=defn2.is_multiface,
)
creature_a = Creature(definition=defn, controller=0, owner=0)
creature_a.has_summoning_sickness = False
creature_b = Creature(definition=defn2, controller=0, owner=0)
creature_b.has_summoning_sickness = False
state = make_test_state(p0_battle=[creature_a, creature_b])

creature_a.apply_static_effects(state)
creature_b.apply_static_effects(state)

p0_stacked = state.global_effects.get_cost_modifiers(0, 999)
check(
    "Player 0 gets -2 from two reducers",
    p0_stacked == -2,
    f"got {p0_stacked}",
)

# ── Test 4: COST_INCREASE affects opponent ───────────────────────────────────
print("\nTest 4: COST_INCREASE creature (opponent scope)")
defn3 = card_def(102, "Tax Brummer")
defn3 = CardDefinition(
    id=defn3.id, slug=defn3.slug, name=defn3.name, cost=defn3.cost, power=defn3.power,
    card_type=defn3.card_type, card_subtype=defn3.card_subtype,
    civilizations=defn3.civilizations, races=defn3.races, keywords=defn3.keywords,
    effects=(cost_increase_effect(1, scope="opponent"),),
    evolution_source_races=defn3.evolution_source_races,
    evolution_source_types=defn3.evolution_source_types,
    is_multiface=defn3.is_multiface,
)
tax_creature = Creature(definition=defn3, controller=0, owner=0)
tax_creature.has_summoning_sickness = False
state = make_test_state(p0_battle=[tax_creature])

tax_creature.apply_static_effects(state)

p0_tax = state.global_effects.get_cost_modifiers(0, 999)
check(
    "Player 0 (controller) unaffected by opponent-scoped increase",
    p0_tax == 0,
    f"got {p0_tax}",
)
p1_tax = state.global_effects.get_cost_modifiers(1, 999)
check(
    "Player 1 (opponent) gets +1 cost increase",
    p1_tax == 1,
    f"got {p1_tax}",
)

# ── Summary ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"Results: {passed}/{total} passed")
if passed == total:
    print("All tests passed!")
else:
    print("FAILURES:")
    for name, ok, detail in results:
        if not ok:
            print(f"  {FAIL} {name} — {detail}")
    sys.exit(1)
