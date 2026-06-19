"""
tests/test_evolution_rules.py — Evolution Rules Smoke Test

Quick validation that evolution mechanics are properly implemented.
Full integration tests are in action_generator, sba_checker, and zone_mover tests.
"""

import sys
sys.path.insert(0, 'dm_engine')

from core.zones import Creature, EvolutionStackEntry, _new_uid
from core.cards import CardDefinition, CardType
from core.enums import CardSubtype


def check(name: str, condition: bool) -> None:
    """Test helper."""
    status = "✅" if condition else "❌"
    print(f"  {status} {name}")
    if not condition:
        raise AssertionError(f"Test failed: {name}")


print("\n" + "=" * 70)
print("  DM ENGINE — EVOLUTION RULES SMOKE TEST")
print("=" * 70)

# ─────────────────────────────────────────────────────────────────────────────
# 1. Evolution Stack Helpers (rule 801)
# ─────────────────────────────────────────────────────────────────────────────

print("\n── 1. Evolution Stack Helpers (rule 801) ──")

creature = Creature(definition=None, uid=_new_uid(), controller=0, owner=0, entered_turn=1)
check("New creature has no stack", not creature.is_evolution_creature())

entry = EvolutionStackEntry(definition=None, uid=_new_uid(), owner=0, entered_turn=1)
creature.push_to_evolution_stack(entry)
check("Stack added successfully", creature.is_evolution_creature())
check("Stack size is 1", len(creature.evolution_stack) == 1)

popped = creature.pop_from_evolution_stack()
check("Popped entry matches", popped.uid == entry.uid)
check("Stack is empty after pop", not creature.is_evolution_creature())

# ─────────────────────────────────────────────────────────────────────────────
# 2. NEO Evolution Helpers (rule 802)
# ─────────────────────────────────────────────────────────────────────────────

print("\n── 2. NEO Evolution State Helpers (rule 802) ──")

neo_def = CardDefinition(
    id=1, slug="neo", name="NEO Creature", cost=2,
    civilizations=frozenset(), card_type=CardType.CREATURE,
    card_subtype=CardSubtype.NEO, power=1500,
    races=frozenset(), keywords=frozenset(), effects=tuple(),
    evolution_source_races=frozenset(), evolution_source_types=frozenset(),
    is_multiface=False,
)

neo_creature = Creature(definition=neo_def, uid=_new_uid(), controller=0, owner=0, entered_turn=5)
check("NEO without stack not NEO-evolved", not neo_creature.is_neo_evolution_creature())

neo_entry = EvolutionStackEntry(definition=None, uid=_new_uid(), owner=0, entered_turn=5, neo_evolution_placed=True)
neo_creature.push_to_evolution_stack(neo_entry)
check("NEO with stack is NEO-evolved", neo_creature.is_neo_evolution_creature())

# Now remove the entry to simulate loss of underlying card
neo_creature.pop_from_evolution_stack()
neo_creature.temp_flags["_neo_evolved_this_turn"] = True
check("Summoning sickness recovery works (same turn)", neo_creature.check_neo_summoning_sickness_recovery(5))
check("Summoning sickness recovery fails (different turn)", not neo_creature.check_neo_summoning_sickness_recovery(6))

# Create a fresh NEO creature for the info test
neo_info_creature = Creature(definition=neo_def, uid=_new_uid(), controller=0, owner=0, entered_turn=5)
neo_info_entry = EvolutionStackEntry(definition=None, uid=_new_uid(), owner=0, entered_turn=5, neo_evolution_placed=True)
neo_info_creature.push_to_evolution_stack(neo_info_entry)

under_info = neo_info_creature.get_neo_underlying_entry_info()
check("NEO entry info retrieval works", under_info is not None)
if under_info:
    entry, was_via_neo = under_info
    check("NEO entry flag is set", was_via_neo)

# ─────────────────────────────────────────────────────────────────────────────
# 3. G-NEO Evolution Helpers (rule 803)
# ─────────────────────────────────────────────────────────────────────────────

print("\n── 3. G-NEO Evolution State Helpers (rule 803) ──")

gneo_def = CardDefinition(
    id=2, slug="gneo", name="G-NEO Creature", cost=3,
    civilizations=frozenset(), card_type=CardType.CREATURE,
    card_subtype=CardSubtype.G_NEO, power=2000,
    races=frozenset(), keywords=frozenset(), effects=tuple(),
    evolution_source_races=frozenset(), evolution_source_types=frozenset(),
    is_multiface=False,
)

gneo_creature = Creature(definition=gneo_def, uid=_new_uid(), controller=0, owner=0, entered_turn=1)
check("G-NEO without stack not G-NEO-evolved", not gneo_creature.is_neo_evolution_creature())

gneo_entry = EvolutionStackEntry(definition=None, uid=_new_uid(), owner=0, entered_turn=1)
gneo_creature.push_to_evolution_stack(gneo_entry)
check("G-NEO with stack is G-NEO-evolved", gneo_creature.is_neo_evolution_creature())

# ─────────────────────────────────────────────────────────────────────────────
# 4. Star Evolution Replacement Detection (rule 813)
# ─────────────────────────────────────────────────────────────────────────────

print("\n── 4. Star Evolution Replacement Detection (rule 813) ──")

from engine.zone_mover import should_apply_star_evo_replacement, should_apply_gneo_all_leave_replacement

star_evo_def = CardDefinition(
    id=3, slug="star-evo", name="Star Evolution", cost=4,
    civilizations=frozenset(), card_type=CardType.CREATURE,
    card_subtype=CardSubtype.EVOLUTION, power=2500,
    races=frozenset(), keywords=frozenset(), effects=tuple(),
    evolution_source_races=frozenset(), evolution_source_types=frozenset(),
    is_multiface=False,
)

star_creature = Creature(definition=star_evo_def, uid=_new_uid(), controller=0, owner=0, entered_turn=1)
check("Star Evolution without stack doesn't trigger", not should_apply_star_evo_replacement(star_creature))

star_entry = EvolutionStackEntry(definition=None, uid=_new_uid(), owner=0, entered_turn=1)
star_creature.push_to_evolution_stack(star_entry)
star_creature.temp_flags["_is_star_evolution"] = True
check("Star Evolution with stack and flag triggers", should_apply_star_evo_replacement(star_creature))

star_creature.temp_flags["_replacement_already_applied"] = True
check("Star Evolution doesn't trigger if replacement already applied", not should_apply_star_evo_replacement(star_creature))

# ─────────────────────────────────────────────────────────────────────────────
# 5. G-NEO All-Leave Replacement Detection (rule 803.2)
# ─────────────────────────────────────────────────────────────────────────────

print("\n── 5. G-NEO All-Leave Replacement Detection (rule 803.2) ──")

gneo_for_leave = Creature(definition=gneo_def, uid=_new_uid(), controller=0, owner=0, entered_turn=1)
check("G-NEO without stack doesn't trigger leave", not should_apply_gneo_all_leave_replacement(gneo_for_leave))

gneo_for_leave.push_to_evolution_stack(EvolutionStackEntry(definition=None, uid=_new_uid(), owner=0, entered_turn=1))
check("G-NEO with stack triggers leave", should_apply_gneo_all_leave_replacement(gneo_for_leave))

gneo_for_leave.temp_flags["_replacement_already_applied"] = True
check("G-NEO doesn't trigger if replacement already applied", not should_apply_gneo_all_leave_replacement(gneo_for_leave))

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print("\n" + "=" * 70)
print("  RESULTS: All evolution rule smoke tests passed!")
print("=" * 70)
print("\nNOTE: Full integration tests are in test_action_generator,")
print("      test_sba_checker, and test_action_executor")
