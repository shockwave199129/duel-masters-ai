"""
tests/test_sba_checker.py — state-based action behavior.
"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.cards import CardDefinition
from core.enums import CardSubtype, CardType, Civilization, GameResult, Phase
from core.player_state import PlayerState
from core.state import AttackContext, GameState, TurnInfo
from core.zones import Creature
from engine.sba_checker import check_state_based_actions

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))


def card(cid, name, power):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=1, power=power,
        card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]), races=frozenset(),
        keywords=frozenset(), effects=tuple(),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


def bare_state(phase=Phase.MAIN):
    filler = card(99, "deck", 1000)
    return GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", deck=[filler]),
            PlayerState(player_index=1, player_name="P1", deck=[filler]),
        ),
        turn_info=TurnInfo(turn_number=2, active_player=0, phase=phase),
    )


print("\n" + "═"*60)
print("  DM ENGINE — SBA TESTS")
print("═"*60)

s = bare_state()
s.players[0].battle_zone = [Creature(definition=card(1, "zero1", 0), controller=0)]
s.players[1].battle_zone = [Creature(definition=card(2, "zero2", 0), controller=1)]
after = check_state_based_actions(s)
check("Both zero-power creatures destroyed simultaneously",
      len(after.players[0].graveyard) == 1 and len(after.players[1].graveyard) == 1)

s = bare_state(Phase.DIRECT_ATTACK)
s.players[1].shield_zone = []
s.attack_context = AttackContext(
    attacker_uid="atk",
    attacker_player=0,
    target_type="player",
    target_uid="player_1",
    shields_broken=1,
)
after = check_state_based_actions(s)
check("Breaking last shield is not direct attack win", after.result == GameResult.IN_PROGRESS)

s.attack_context.received_direct_attack = True
after = check_state_based_actions(s)
check("Explicit direct attack event wins", after.result == GameResult.PLAYER_0_WINS)

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
