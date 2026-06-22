#!/usr/bin/env python3
"""Tests for Sabaki Z (Rule 112.3d, 509.5d)."""

import sys
sys.path.insert(0, "dm_engine")

from core.enums import Phase, Civilization, CardType, CardSubtype, Keyword, ActionType
from core.state import GameState, TurnInfo, PlayerState, ShieldBreakWindow
from core.zones import HandCard, ShieldCard
from core.cards import CardDefinition
from core.actions import use_sabaki_z, pass_action
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.shield_break_window import finish_declarations


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


class FakeEffect:
    def __init__(self, raw_text: str):
        self.raw_text = raw_text


def card(cid, name, card_type=CardType.CREATURE, keywords=frozenset(), effects=()):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=5,
        power=3000 if card_type == CardType.CREATURE else None,
        card_type=card_type, card_subtype=CardSubtype.NONE,
        civilizations=frozenset({Civilization.DARKNESS}),
        races=frozenset(), keywords=keywords, effects=effects,
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


print("=" * 70)
print("Testing Sabaki Z (Rule 112.3d)")
print("=" * 70)

emblem_def = card(
    1, "Judgment Crest",
    card_type=CardType.SPELL,
    effects=(FakeEffect("Emblem of Judgment"),),
)
check("Emblem detection", emblem_def.has_emblem_of_judgment())

sabaki_def = card(2, "Sabaki Card", keywords=frozenset({Keyword.SABAKI_Z}))
emblem_shield = ShieldCard(definition=emblem_def, uid="emblem1")
sabaki_hand = HandCard(definition=sabaki_def, uid="sabaki1")

s = GameState(
    players=(
        PlayerState(player_index=0, player_name="P0", deck=[card(99, "f0")]),
        PlayerState(player_index=1, player_name="P1", hand=[sabaki_hand], deck=[card(98, "f1")]),
    ),
    turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.END_OF_ATTACK, first_player=0),
)
win = ShieldBreakWindow(defending_player=1, standby_shields=[emblem_shield])
s.effect_stack.shield_break_window = win
s.effect_stack.shield_trigger_queue = [(1, emblem_shield)]

finish_declarations(s)
check("Emblem moved to hand", len(s.players[1].hand) == 2)
check("Resolve phase active", s.effect_stack.shield_break_window.phase == "resolve")

actions = get_legal_actions(s)
sz_actions = [a for a in actions if a.action_type == ActionType.USE_SABAKI_Z]
check("Sabaki Z offered after hand add", len(sz_actions) == 1)

after = execute_action(s, sz_actions[0], validate=False)
check("Emblem discarded", any(c.died_from == "sabaki_z_discard" for c in after.players[1].graveyard))
check("Sabaki Z creature summoned", len(after.players[1].battle_zone) == 1)
check("Sabaki Z card left hand", len(after.players[1].hand) == 0)

print("\n" + "=" * 70)
