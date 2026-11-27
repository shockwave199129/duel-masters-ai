"""
tests/test_rng_salt.py - random card effects in real and imagined games.
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.player_state import PlayerState
from core.state import GameState, TurnInfo

results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" - {detail}" if detail else ""))


def _state(salt=""):
    return GameState(
        players=(PlayerState(player_index=0, player_name="P0"),
                 PlayerState(player_index=1, player_name="P1")),
        turn_info=TurnInfo(turn_number=3, active_player=0),
        game_id="g1",
        rng_salt=salt,
    )


def _order(rng):
    cards = list(range(40))
    rng.shuffle(cards)
    return cards


print("DM ENGINE - SEARCH CHANCE TESTS")

check("Real games keep the old seed",
      _order(_state().seeded_chance("uid1")) == _order(random.Random("g1:3:uid1")))
check("Same salt repeats the same result",
      _order(_state("tree1").seeded_chance("uid1"))
      == _order(_state("tree1").seeded_chance("uid1")))
check("Different salts give different results",
      _order(_state("tree1").seeded_chance("uid1"))
      != _order(_state("tree2").seeded_chance("uid1")))
check("A copied state keeps its salt", _state("tree1").copy().rng_salt == "tree1")
check("Real games still use the shared generator", _state().shared_chance("x") is random)

random.seed(5)
expected = random.random()
random.seed(5)
_order(_state("tree1").shared_chance("shuffle", 0, 40))
check("An imagined game leaves the shared generator alone", random.random() == expected)

passed = sum(results)
print(f"RESULTS: {passed}/{len(results)} passed, {len(results) - passed} failed")
sys.exit(0 if passed == len(results) else 1)
