"""
test_cannot_attack_players.py — verify CANNOT_ATTACK_PLAYERS keyword check.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.cards import CardDefinition
from core.enums import CardSubtype, CardType, Civilization, Keyword, Phase
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

def card(cid, name, power=1000, keywords=()):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=1, power=power,
        card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]), races=frozenset(),
        keywords=frozenset(keywords), effects=tuple(),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )

def creature(defn, controller=0, sick=False):
    c = Creature(defn)
    c.controller = controller
    c.owner = controller
    c.has_summoning_sickness = sick
    return c

# ── Test 1: Normal creature CAN attack players ──
print("\n[Test 1] Normal creature (no keyword) can attack players")
basic_card = card(1, "Basic Creature")
basic = creature(basic_card)
check("can_attack_players() returns True", basic.can_attack_players() is True)

# ── Test 2: Creature WITH CANNOT_ATTACK_PLAYERS keyword CANNOT ──
print("\n[Test 2] Creature with CANNOT_ATTACK_PLAYERS keyword cannot attack players")
no_atk_card = card(2, "No Attack Creature", keywords=(Keyword.CANNOT_ATTACK_PLAYERS,))
no_atk = creature(no_atk_card)
check("has CANNOT_ATTACK_PLAYERS keyword", no_atk_card.keywords == frozenset({Keyword.CANNOT_ATTACK_PLAYERS}))
check("can_attack_players() returns False", no_atk.can_attack_players() is False)

# ── Test 3: Temp flag also blocks ──
print("\n[Test 3] Temp flag cannot_attack_players also blocks")
flag_card = card(3, "Temp Blocked Creature")
flag_creature = creature(flag_card)
flag_creature.temp_flags["cannot_attack_players"] = True
check("temp flag is set", flag_creature.temp_flags.get("cannot_attack_players", False) is True)
check("can_attack_players() returns False", flag_creature.can_attack_players() is False)

# ── Summary ──
total = len(results)
passed = sum(1 for _, ok, _ in results if ok)
failed = total - passed

print("\n" + "=" * 40)
print(f"SUMMARY: {passed}/{total} passed, {failed} failed")
print("=" * 40)

if failed:
    print("\nFailed checks:")
    for name, ok, detail in results:
        if not ok:
            print(f"  {FAIL} {name}" + (f" — {detail}" if detail else ""))
    sys.exit(1)
else:
    print("All checks passed!")
    sys.exit(0)
