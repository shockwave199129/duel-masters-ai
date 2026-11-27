"""
tests/test_state_copy.py - the fast game state copy.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.state_copy
from core.cards import CardDefinition
from core.enums import CardSubtype, CardType, Civilization
from core.player_state import PlayerState
from core.state import GameState, TurnInfo
from core.zones import Creature, HandCard, ManaCard, ShieldCard

results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" - {detail}" if detail else ""))


CARD = CardDefinition(
    id=1, slug="card", name="Card", cost=2, power=3000,
    card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE,
    civilizations=frozenset([Civilization.FIRE]), races=frozenset(),
    keywords=frozenset(), effects=tuple(),
    evolution_source_races=frozenset(), evolution_source_types=frozenset(),
    is_multiface=False,
)


def _state():
    players = tuple(PlayerState(player_index=i, player_name=f"P{i}", deck=[CARD, CARD])
                    for i in range(2))
    players[0].hand = [HandCard(CARD)]
    players[0].mana_zone = [ManaCard(CARD)]
    players[0].battle_zone = [Creature(CARD, controller=0, owner=0)]
    players[1].shield_zone = [ShieldCard(CARD)]
    return GameState(players=players, turn_info=TurnInfo(turn_number=3, active_player=0))


print("DM ENGINE - STATE COPY TESTS")

fallbacks = []
real_deepcopy = core.state_copy.deepcopy
core.state_copy.deepcopy = lambda value: fallbacks.append(1) or real_deepcopy(value)

original = _state()
copied = original.copy()
check("A normal state does not need the slow copy", not fallbacks)
check("The copy is a different object", copied is not original
      and copied.players[0] is not original.players[0])
check("Card data is shared", copied.players[0].hand[0].definition is CARD
      and copied.players[0].deck[0] is CARD)
check("Card uids are kept", copied.players[0].hand[0].uid == original.players[0].hand[0].uid)

copied.players[0].hand.clear()
copied.players[0].battle_zone[0].is_tapped = True
copied.players[0].mana_zone[0].is_tapped = True
copied.players[1].deck.pop()
copied.turn_info.turn_number = 9
check("Changing the copy leaves the original alone",
      len(original.players[0].hand) == 1
      and not original.players[0].battle_zone[0].is_tapped
      and not original.players[0].mana_zone[0].is_tapped
      and len(original.players[1].deck) == 2
      and original.turn_number == 3)

# One object held in two places stays one object, as with deepcopy
original.players[0].battle_zone[0].temp_flags["same"] = [original.players[0].mana_zone[0]]
twin = original.copy()
check("Shared references stay shared inside the copy",
      twin.players[0].battle_zone[0].temp_flags["same"][0] is twin.players[0].mana_zone[0])

original.players[0].battle_zone[0].temp_flags["rule"] = lambda: 7
fallback_copy = original.copy()
check("A state that cannot be pickled is still copied",
      fallbacks and fallback_copy.players[0].battle_zone[0].temp_flags["rule"]() == 7
      and fallback_copy.players[0] is not original.players[0])

passed = sum(results)
print(f"RESULTS: {passed}/{len(results)} passed, {len(results) - passed} failed")
sys.exit(0 if passed == len(results) else 1)
