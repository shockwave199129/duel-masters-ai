"""
tests/test_sba_cannot_attack_tap.py — Rule 703.4e: creatures with
'cannot attack' keyword must be tapped.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.cards import CardDefinition
from core.enums import CardSubtype, CardType, Civilization, GameResult, Keyword, Phase
from core.player_state import PlayerState
from core.state import GameState, TurnInfo
from core.zones import Creature
from engine.sba.checker import check_state_based_actions

PASS = "✅"
FAIL = "❌"
results = []

def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))
    return ok

def card(cid, name, power=1000, keywords=frozenset()):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=1, power=power,
        card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]), races=frozenset(),
        keywords=keywords, effects=tuple(),
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
print("  SBA TESTS — 703.4e CANNOT_ATTACK → tap")
print("═"*60)

# Test 1: creature with CANNOT_ATTACK starts untapped → SBA taps it
s = bare_state()
creat = Creature(definition=card(1, "decrepit_warrior", keywords=frozenset({Keyword.CANNOT_ATTACK})),
                 controller=0, owner=0, is_tapped=False)
s.players[0].battle_zone = [creat]
after = check_state_based_actions(s)
check("Untapped cannot_attack creature becomes tapped",
      after.players[0].battle_zone[0].is_tapped)

# Test 2: creature already tapped → no change
s = bare_state()
creat = Creature(definition=card(2, "already_tapped", keywords=frozenset({Keyword.CANNOT_ATTACK})),
                 controller=0, owner=0, is_tapped=True)
s.players[0].battle_zone = [creat]
after = check_state_based_actions(s)
check("Already-tapped cannot_attack creature stays tapped",
      after.players[0].battle_zone[0].is_tapped and len(after.players[0].battle_zone) == 1)

# Test 3: creature without CANNOT_ATTACK not affected
s = bare_state()
creat = Creature(definition=card(3, "normal_warrior"), controller=0, owner=0, is_tapped=False)
s.players[0].battle_zone = [creat]
after = check_state_based_actions(s)
check("Normal creature stays untapped",
      not after.players[0].battle_zone[0].is_tapped)

# Test 4: both players' creatures handled
s = bare_state()
s.players[0].battle_zone = [Creature(
    definition=card(1, "p0_creature", keywords=frozenset({Keyword.CANNOT_ATTACK})),
    controller=0, owner=0, is_tapped=False)]
s.players[1].battle_zone = [Creature(
    definition=card(2, "p1_creature", keywords=frozenset({Keyword.CANNOT_ATTACK})),
    controller=1, owner=1, is_tapped=False)]
after = check_state_based_actions(s)
check("Both players' cannot_attack creatures tapped",
      after.players[0].battle_zone[0].is_tapped and after.players[1].battle_zone[0].is_tapped)

# Test 5: ignored creature (has seal) is skipped
s = bare_state()
seal_card = card(99, "seal_card", power=0)
creat = Creature(definition=card(5, "sealed_creature", keywords=frozenset({Keyword.CANNOT_ATTACK})),
                 controller=0, owner=0, is_tapped=False)
creat.seals = [seal_card]  # sealed creatures are ignored (rule 116.2)
s.players[0].battle_zone = [creat]
after = check_state_based_actions(s)
check("Ignored (sealed) cannot_attack creature skipped",
      not after.players[0].battle_zone[0].is_tapped)

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
