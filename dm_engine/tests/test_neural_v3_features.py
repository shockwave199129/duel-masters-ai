"""
tests/test_neural_v3_features.py - v3 neural feature schema and visibility tests.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.action_encoder import ACTION_VECTOR_SIZE_V3, encode_action_v3
from bot.state_encoder import OBSERVATION_VECTOR_SIZE_V3, encode_observation_v3
from core.actions import pass_action
from core.cards import CardDefinition
from core.enums import ActionType, CardSubtype, CardType, Civilization, Keyword, Phase
from core.player_state import PlayerState
from core.state import GameState, TurnInfo
from core.zones import HandCard, ManaCard, Creature

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" - {detail}" if detail else ""))


def _card(cid, name, cost=3, civs=None, power=2000, keywords=None, card_type=CardType.CREATURE):
    return CardDefinition(
        id=cid,
        slug=name.lower().replace(" ", "_"),
        name=name,
        cost=cost,
        power=power if card_type == CardType.CREATURE else None,
        card_type=card_type,
        card_subtype=CardSubtype.NONE,
        civilizations=frozenset(civs or [Civilization.FIRE]),
        races=frozenset(),
        keywords=frozenset(keywords or []),
        effects=tuple(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
    )


def _state_with_opponent_hand(hidden_card):
    p0 = PlayerState(player_index=0, player_name="P0", deck_composition={1: 4})
    p1 = PlayerState(player_index=1, player_name="P1", deck_composition={})
    fire = _card(1, "Fire", keywords=[Keyword.SPEED_ATTACKER])
    blocker = _card(2, "Blocker", civs=[Civilization.WATER], keywords=[Keyword.BLOCKER])
    p0.hand = [HandCard(fire)]
    p0.mana_zone = [ManaCard(fire), ManaCard(fire), ManaCard(fire)]
    p0.battle_zone = [Creature(fire, controller=0, owner=0, has_summoning_sickness=False)]
    p1.hand = [HandCard(hidden_card)]
    p1.battle_zone = [Creature(blocker, controller=1, owner=1, is_tapped=True, has_summoning_sickness=False)]
    return GameState(
        players=(p0, p1),
        turn_info=TurnInfo(turn_number=3, active_player=0, phase=Phase.ATTACK),
    )


print("\n" + "=" * 60)
print("  DM ENGINE - NEURAL V3 FEATURE TESTS")
print("=" * 60)

hidden_a = _card(50, "Hidden A", cost=1, civs=[Civilization.LIGHT])
hidden_b = _card(99, "Hidden B", cost=9, civs=[Civilization.DARKNESS], keywords=[Keyword.SHIELD_TRIGGER])

state_a = _state_with_opponent_hand(hidden_a)
state_b = _state_with_opponent_hand(hidden_b)

features_a = encode_observation_v3(state_a, 0)
features_b = encode_observation_v3(state_b, 0)
check("Observation v3 vector size matches constant", len(features_a) == OBSERVATION_VECTOR_SIZE_V3)
check("Opponent hidden hand contents do not change v3 features", features_a == features_b)

action = pass_action(0, "attack")
action_features = encode_action_v3(action, state=state_a, db=None)
check("Action v3 vector size matches constant", len(action_features) == ACTION_VECTOR_SIZE_V3)

attack = next(a for a in [
    pass_action(0, "attack"),
] if a.action_type == ActionType.PASS)
check("Pass action remains encodable", len(encode_action_v3(attack, state=state_a, db=None)) == ACTION_VECTOR_SIZE_V3)

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
