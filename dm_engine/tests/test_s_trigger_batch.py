#!/usr/bin/env python3
"""Tests for S-Trigger batch declaration (Rules 112.3a, 509.5a-e)."""

import sys
sys.path.insert(0, "dm_engine")

from core.enums import Phase, Civilization, CardType, CardSubtype, Keyword, ActionType
from core.state import GameState, TurnInfo, PlayerState, AttackContext
from core.zones import Creature, ShieldCard
from core.cards import CardDefinition
from core.actions import use_shield_trigger, pass_action
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.shield_resolver import resolve_shield_break_choice


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


def card(cid, name, card_type=CardType.SPELL, keywords=frozenset()):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=3,
        power=None, card_type=card_type, card_subtype=CardSubtype.NONE,
        civilizations=frozenset({Civilization.FIRE}),
        races=frozenset(), keywords=keywords, effects=(),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


def make_break_state():
    dbl = Creature(
        definition=card(1, "Breaker", card_type=CardType.CREATURE,
                        keywords=frozenset({Keyword.DOUBLE_BREAKER})),
        uid="att1", controller=0, owner=0,
    )
    st1 = ShieldCard(definition=card(10, "ST1", keywords=frozenset({Keyword.SHIELD_TRIGGER})), uid="sh1")
    st2 = ShieldCard(definition=card(11, "ST2", keywords=frozenset({Keyword.SHIELD_TRIGGER})), uid="sh2")
    s = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", battle_zone=[dbl], deck=[card(99, "f0")]),
            PlayerState(player_index=1, player_name="P1", shield_zone=[st1, st2], deck=[card(98, "f1")]),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.DIRECT_ATTACK, first_player=0),
        attack_context=AttackContext(
            attacker_uid="att1",
            attacker_player=0,
            target_type="player",
            target_uid="player_1",
        ),
    )
    return s


print("=" * 70)
print("Testing S-Trigger Batch Declaration")
print("=" * 70)

s = make_break_state()
after_break = resolve_shield_break_choice(s, 0)
win = after_break.effect_stack.shield_break_window
check("Shield break window opened", win is not None)
check("Two shields in batch", win is not None and len(win.standby_shields) == 2)
check("Window in declare phase", win is not None and win.phase == "declare")

declare_actions = get_legal_actions(after_break)
st_actions = [a for a in declare_actions if a.action_type == ActionType.USE_SHIELD_TRIGGER and a.choice]
check("Both S-Triggers offered in declare phase", len(st_actions) == 2)

s2 = execute_action(after_break, st_actions[0], validate=False)
s2 = execute_action(s2, st_actions[1], validate=False)
check("Both S-Triggers declared", len(s2.effect_stack.shield_break_window.declared_s_triggers) == 2)
check("No execution yet in declare phase", len(s2.players[1].hand) == 0)

finish = next(
    a for a in get_legal_actions(s2)
    if a.get_extra().get("step") == "finish_shield_declarations"
)
s3 = execute_action(s2, finish, validate=False)
win3 = s3.effect_stack.shield_break_window
check("Window moved to resolve phase", win3 is not None and win3.phase == "resolve")
check("Declared shields moved to hand first", len(s3.players[1].hand) == 2)

resolve_actions = get_legal_actions(s3)
exec_actions = [a for a in resolve_actions if a.action_type == ActionType.USE_SHIELD_TRIGGER]
check("Execution offered after hand add", len(exec_actions) == 2)

s4 = execute_action(s3, exec_actions[0], validate=False)
check("First S-Trigger executed from hand", len(s4.players[1].graveyard) >= 1)

print("\n" + "=" * 70)
