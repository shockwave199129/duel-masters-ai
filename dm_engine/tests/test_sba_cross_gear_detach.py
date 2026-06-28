"""
tests/test_sba_cross_gear_detach.py — Rule 703.4f: standalone Cross Gear
not attached to a creature is placed in the graveyard.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.cards import CardDefinition
from core.enums import CardSubtype, CardType, Civilization, Phase
from core.player_state import PlayerState
from core.state import GameState, TurnInfo
from core.zones import Creature
from engine.sba_checker import check_state_based_actions

PASS = "✅"
FAIL = "❌"
results = []

def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))
    return ok

def card(cid, name, card_type=CardType.CREATURE, power=1000):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=1, power=power if card_type == CardType.CREATURE else None,
        card_type=card_type, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]), races=frozenset(),
        keywords=frozenset(), effects=tuple(),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )

def bare_state():
    filler = card(99, "deck")
    return GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", deck=[filler]),
            PlayerState(player_index=1, player_name="P1", deck=[filler]),
        ),
        turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
    )

print("\n" + "═"*60)
print("  SBA TESTS — 703.4f CROSS GEAR DETACH")
print("═"*60)

# Test 1: standalone cross gear → graveyard
s = bare_state()
gear = Creature(definition=card(10, "lone_blade", card_type=CardType.CROSS_GEAR),
                controller=0, owner=0)
s.players[0].battle_zone = [gear]
after = check_state_based_actions(s)
check("Standalone cross gear moved to graveyard",
      len(after.players[0].battle_zone) == 0 and len(after.players[0].graveyard) == 1)
check("Cross gear died_from marker correct",
      after.players[0].graveyard[0].died_from == "sba_cross_gear_standalone")

# Test 2: cross gear attached to a creature (removed from BZ, in attached_cards) stays
s = bare_state()
host = Creature(definition=card(20, "warrior"), controller=0, owner=0)
gear_def = card(21, "equipped_blade", card_type=CardType.CROSS_GEAR)
host.attached_cards.append(gear_def)  # attached = in host.attached_cards, not in BZ
s.players[0].battle_zone = [host]
after = check_state_based_actions(s)
check("Attached cross gear (not in BZ) stays",
      len(after.players[0].battle_zone) == 1)

# Test 3: both players' cross gears handled
s = bare_state()
s.players[0].battle_zone = [Creature(
    definition=card(30, "p0_gear", card_type=CardType.CROSS_GEAR), controller=0)]
s.players[1].battle_zone = [Creature(
    definition=card(31, "p1_gear", card_type=CardType.CROSS_GEAR), controller=1)]
after = check_state_based_actions(s)
check("Both players' cross gears destroyed",
      len(after.players[0].battle_zone) == 0 and len(after.players[1].battle_zone) == 0 and
      len(after.players[0].graveyard) == 1 and len(after.players[1].graveyard) == 1)

# Test 4: normal creature not affected
s = bare_state()
creat = Creature(definition=card(40, "normal_creature"), controller=0)
s.players[0].battle_zone = [creat]
after = check_state_based_actions(s)
check("Normal creature unaffected",
      len(after.players[0].battle_zone) == 1 and len(after.players[0].graveyard) == 0)

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
