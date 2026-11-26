"""
tests/test_choice_resolution.py - player choices resolve and queued triggers run.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.cards import CardDefinition, CardEffect
from core.enums import (
    CardSubtype, CardType, Civilization, EffectAction, EffectType, Phase, TriggerEvent,
)
from core.player_state import PlayerState
from core.state import GameState, PendingTrigger, TurnInfo
from core.zones import Creature
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.trigger_resolver import resolve_pending_triggers

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


def card(cid, effects=()):
    return CardDefinition(
        id=cid, slug=f"c{cid}", name=f"c{cid}", cost=1, power=1000, card_type=CardType.CREATURE,
        card_subtype=CardSubtype.NONE, civilizations=frozenset([Civilization.FIRE]),
        races=frozenset(), keywords=frozenset(), effects=tuple(effects),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


def effect(action=EffectAction.DRAW, optional=False, target=None, value=None,
           event=TriggerEvent.ON_SUMMON):
    return CardEffect(
        card_id=1, ability_index=0, raw_text="test", effect_type=EffectType.TRIGGERED,
        trigger_event=event, effect_action=action, trigger_condition={},
        effect_target=target or {}, effect_value=value or {"amount": 1}, is_optional=optional,
        is_replacement=False, active_in_phase=("any",), active_in_zone=("battle_zone",),
        parse_confidence=1.0,
    )


def make_state():
    return GameState(
        players=[
            PlayerState(player_index=i, player_name=f"P{i}", deck=[card(90)] * 5) for i in range(2)
        ],
        turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
    )


def pause(state, *effects):
    for item in effects:
        state.effect_stack.add_trigger(
            PendingTrigger(effect=item, source_uid="src", source_card_id=1, controller=0)
        )
    return resolve_pending_triggers(state)


def answer(state, wanted):
    action = next(a for a in get_legal_actions(state) if wanted(a))
    return execute_action(state, action, validate=False)


for use, drawn in ((True, 1), (False, 0)):
    paused = pause(make_state(), effect(optional=True))
    after = answer(paused, lambda a: a.choice is use)
    check(f"Answering {use} clears the question", after.effect_stack.awaited_choice is None)
    check(f"Answering {use} draws {drawn}", len(after.players[0].hand) == drawn)

paused = pause(make_state(), effect(optional=True), effect(optional=False))
check("A pending question holds back later triggers",
      len(paused.effect_stack.pending_triggers) == 1 and not paused.players[0].hand)
after = answer(paused, lambda a: a.choice is True)
check("Later triggers run once it is answered",
      len(after.players[0].hand) == 2 and not after.effect_stack.pending_triggers)

state = make_state()
victim = Creature(definition=card(2), controller=1, owner=1)
state.players[1].battle_zone.append(victim)
paused = pause(state, effect(EffectAction.DESTROY, target={"type": "creature", "zone": "battle_zone"}))
after = answer(paused, lambda a: a.target_uid == victim.uid)
check("Chosen target is destroyed", not after.players[1].battle_zone and after.effect_stack.awaited_choice is None)

# A trigger queued by a state-based action resolves within the same action
state = make_state()
dying = Creature(definition=card(3, [effect(event=TriggerEvent.ON_DESTROY)]), controller=0, owner=0)
dying.definition = CardDefinition(**{**dying.definition.__dict__, "power": 0})
state.players[0].battle_zone.append(dying)
dying.apply_static_effects(state)
after = execute_action(state, get_legal_actions(state)[-1], validate=False)
check("Destroy trigger resolves without waiting for a spell",
      not after.effect_stack.pending_triggers and len(after.players[0].hand) >= 1,
      f"pending={len(after.effect_stack.pending_triggers)} hand={len(after.players[0].hand)}")

if FAILURES:
    print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
    sys.exit(1)
