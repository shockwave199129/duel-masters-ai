"""
tests/test_sba_cannot_attack_tap.py — a creature that can't attack is not
tapped for it (rule 703.4e concerns God links).
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.cards import CardDefinition
from core.enums import CardSubtype, CardType, Civilization, Keyword, Phase
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
print("  CAN'T ATTACK: stops attacks, nothing else")
print("═"*60)

# Rule 703.4e is about invalid God links, so nothing taps such a creature
s = bare_state()
s.players[0].battle_zone = [Creature(
    definition=card(1, "wall", keywords=frozenset({Keyword.CANNOT_ATTACK, Keyword.BLOCKER})),
    controller=0, owner=0, is_tapped=False, has_summoning_sickness=False)]
after = check_state_based_actions(s)
wall = after.players[0].battle_zone[0]
check("It is not tapped", not wall.is_tapped)
check("It cannot attack", not wall.can_attack())
check("It can still block", wall.is_blocker() and not wall.is_tapped)

s = bare_state()
normal = Creature(definition=card(3, "normal_warrior"), controller=0, owner=0,
                  has_summoning_sickness=False)
s.players[0].battle_zone = [normal]
check("Other creatures can attack", normal.can_attack())

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
