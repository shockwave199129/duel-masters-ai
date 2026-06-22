#!/usr/bin/env python3
"""Tests for Attack Chance (Rule 112.3f)."""

import sys
sys.path.insert(0, "dm_engine")

from core.enums import Phase, Civilization, CardType, CardSubtype, Keyword
from core.state import GameState, TurnInfo, PlayerState, AttackContext
from core.zones import Creature, HandCard
from core.cards import CardDefinition
from core.actions import use_attack_chance, pass_action
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions, _attack_chance_condition_met


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


class FakeEffect:
    def __init__(self, raw_text: str):
        self.raw_text = raw_text


def card(cid, name, cost=3, card_type=CardType.CREATURE, power=2000,
         civilizations=None, keywords=frozenset(), effects=tuple()):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=cost,
        power=power, card_type=card_type, card_subtype=CardSubtype.NONE,
        civilizations=frozenset(civilizations or [Civilization.FIRE]),
        races=frozenset(), keywords=keywords, effects=effects,
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


def state_with_attack(attacker_civs, spell_effects=()):
    attacker_def = card(1, "Attacker", civilizations=attacker_civs)
    spell_def = card(
        2, "Attack Chance Spell",
        card_type=CardType.SPELL,
        civilizations=[Civilization.FIRE],
        keywords=frozenset({Keyword.ATTACK_CHANCE}),
        effects=spell_effects,
    )
    attacker = Creature(definition=attacker_def, uid="atk1", controller=0, owner=0)
    hand_spell = HandCard(definition=spell_def, uid="spell1")
    s = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", battle_zone=[attacker]),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.ATTACK_DECLARE, first_player=0),
        attack_context=AttackContext(
            attacker_uid="atk1",
            attacker_player=0,
            target_type="player",
            target_uid="player_1",
        ),
    )
    s.players[0].hand = [hand_spell]
    return s


print("=" * 70)
print("Testing Attack Chance (Rule 112.3f)")
print("=" * 70)

fire_spell = card(
    3, "Fire AC", card_type=CardType.SPELL,
    keywords=frozenset({Keyword.ATTACK_CHANCE}),
    effects=(FakeEffect("Attack Chance: Fire"),),
)
fire_attacker = card(4, "Fire Guy", civilizations=[Civilization.FIRE])
water_attacker = card(5, "Water Guy", civilizations=[Civilization.WATER])

check(
    "Fire condition matches fire attacker",
    _attack_chance_condition_met(fire_spell, fire_attacker),
)
check(
    "Fire condition rejects water attacker",
    not _attack_chance_condition_met(fire_spell, water_attacker),
)

s = state_with_attack([Civilization.FIRE], ())
actions = get_legal_actions(s)
ac_actions = [a for a in actions if a.action_type == use_attack_chance(0, "", 0).action_type]
check("Attack Chance offered for matching attacker", len(ac_actions) == 1)

s2 = state_with_attack([Civilization.WATER], (FakeEffect("Attack Chance: Fire"),))
actions2 = get_legal_actions(s2)
ac_actions2 = [a for a in actions2 if a.action_type.value == "use_attack_chance"]
check("Attack Chance blocked for mismatched attacker", len(ac_actions2) == 0)

after = execute_action(s, ac_actions[0], validate=False)
check("Spell cast to graveyard", len(after.players[0].hand) == 0)
check("Spell in graveyard", len(after.players[0].graveyard) == 1)

s3 = state_with_attack([Civilization.FIRE])
s3.turn_info.phase = Phase.MAIN
main_actions = get_legal_actions(s3)
check(
    "No Attack Chance outside attack declare",
    all(a.action_type.value != "use_attack_chance" for a in main_actions),
)

print("\n" + "=" * 70)
