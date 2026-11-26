"""
tests/test_trigger_scope.py - triggers react to the right cards, and free spells resolve.
"""

import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.cards import CardDefinition, CardEffect
from core.enums import (
    CardSubtype, CardType, Civilization, EffectAction, EffectType, Phase, TriggerEvent,
)
from core.player_state import PlayerState
from core.state import GameState, TurnInfo
from core.zones import Creature, HandCard
from engine.shield_break_window import execute_free_from_hand
from engine.trigger_registry import fire_trigger
from engine.trigger_resolver import _eval_condition, resolve_pending_triggers
from engine.trigger_scope import Watch, classify
from engine.zone_mover import move_hand_to_battle

FAILURES: list[str] = []
_ids = itertools.count(1)


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


# ── Reading the printed text ────────────────────────────────────────────────
for text, expected in (
    ("■ [[When you put]] this [[creature]], draw a card.", Watch("self")),
    ("■ Whenever this creature attacks, untap it.", Watch("self")),
    ("■ When you summon this creature, return them.", Watch("self")),
    ("■ Whenever one of your creatures wins a battle, draw.", Watch("own")),
    ("■ Whenever you summon a [[Dragon]], you may draw.", Watch("own", "dragon")),
    ("■ Whenever you put another creature into the battle zone, draw.", Watch("own", "", True)),
    ("■ Whenever you put a Dragon in the battle zone, draw.", Watch("own", "dragon")),
    ("■ Whenever an opponent's creature attacks, you may draw a card.", Watch("opponent")),
    ("■ Whenever another creature is destroyed, you may draw a card.", Watch("any", "", True)),
    ("■ Whenever you cast a spell, untap this creature.", Watch("never")),
    ("■ Blocker (Whenever an opponent's creature attacks, you may tap this.)", Watch("never")),
    ("■ Saver: water creature (When one of yours would be destroyed, destroy this instead.)", Watch("never")),
    ("■ When this would leave, discard a card instead.", Watch("never")),
    ("■ When you put this creature, draw. (When it would leave, it stays instead.)", Watch("self")),
    ("■ Whenever one of your water or darkness dragons attacks, draw.", Watch("never")),
):
    check(f"classify: {text[:60]}", classify(text) == expected, str(classify(text)))


def effect(text, event=TriggerEvent.ON_ENTER_BATTLE_ZONE, effect_type=EffectType.TRIGGERED,
           action=EffectAction.DRAW, condition=None):
    return CardEffect(
        card_id=1, ability_index=0, raw_text=text, effect_type=effect_type,
        trigger_event=event, effect_action=action, trigger_condition=condition or {},
        effect_target={}, effect_value={"amount": 1}, is_optional=False, is_replacement=False,
        active_in_phase=("any",), active_in_zone=("battle_zone",), parse_confidence=1.0,
    )


def card(name, effects=(), card_type=CardType.CREATURE, races=()):
    return CardDefinition(
        id=next(_ids), slug=name, name=name, cost=3, power=3000 if card_type == CardType.CREATURE else None,
        card_type=card_type, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]), races=frozenset(races), keywords=frozenset(),
        effects=tuple(effects), evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


def make_state():
    return GameState(
        players=[
            PlayerState(player_index=i, player_name=f"P{i}", deck=[card(f"d{i}{n}") for n in range(8)])
            for i in range(2)
        ],
        turn_info=TurnInfo(turn_number=3, active_player=0, phase=Phase.MAIN),
    )


def summon(state, definition, player):
    held = HandCard(definition=definition)
    state.players[player].hand.append(held)
    move_hand_to_battle(state, player, held.uid)
    return resolve_pending_triggers(state)


def hand_size(state, player=0):
    return len(state.players[player].hand)


# ── Bug 1: only the right card sets an ability off ──────────────────────────
mine = card("Mine", [effect("■ When you put this creature, draw a card.")])
state = summon(make_state(), mine, 0)
check("'This creature' triggers for itself", hand_size(state) == 1)
state = summon(state, card("Other"), 0)
state = summon(state, card("Enemy"), 1)
check("'This creature' ignores other creatures", hand_size(state) == 1, str(hand_size(state)))

watcher = card("Watcher", [effect("■ Whenever you summon a Dragon, draw a card.", TriggerEvent.ON_SUMMON)])
state = summon(make_state(), watcher, 0)
for definition, player, label, total in (
    (card("Human", races=("Human",)), 0, "a non-Dragon", 0),
    (card("Foe dragon", races=("Armored Dragon",)), 1, "the opponent's Dragon", 0),
    (card("My dragon", races=("Armored Dragon",)), 0, "your Dragon", 1),
):
    state = summon(state, definition, player)
    check(f"Dragon watcher after {label}: drawn {total}", hand_size(state) == total, str(hand_size(state)))

foe_watcher = card("Alarm", [effect("■ Whenever an opponent's creature attacks, draw a card.",
                                    TriggerEvent.ON_ATTACK)])
state = summon(make_state(), foe_watcher, 0)
for player, total in ((0, 0), (1, 1)):
    attacker = Creature(definition=card("attacker"), controller=player, owner=player)
    state.players[player].battle_zone.append(attacker)
    fire_trigger(state, TriggerEvent.ON_ATTACK, {"source_uid": attacker.uid, "controller": player}, attacker.uid)
    state = resolve_pending_triggers(state)
    check(f"Opponent watcher after player {player} attacks: drawn {total}", hand_size(state) == total)


class _Trigger:
    controller = 0
    source_uid = source_card_id = None

    def __init__(self, event_player):
        self.trigger_data = {"controller": event_player}


state = make_state()
for word, event_player, expected in (
    ("self", 0, True), ("you", 0, True), ("self", 1, False), ("opponent", 1, True), ("opponent", 0, False),
):
    got = _eval_condition(state, _Trigger(event_player), {"controller": word})
    check(f"Condition controller={word}, event by player {event_player}: {expected}", got == expected)

# ── Bug 2: a free spell still does something ────────────────────────────────
spell = card("Free spell", [effect("■ Draw a card.", TriggerEvent.NONE, EffectType.SPELL)], CardType.SPELL)
state = make_state()
held = HandCard(definition=spell)
state.players[0].hand.append(held)
execute_free_from_hand(state, 0, held.uid, reason="shield_trigger")
state = resolve_pending_triggers(state)
check("Free spell resolves its effect", hand_size(state) == 1, str(hand_size(state)))
check("Free spell ends in the graveyard", [c.definition.name for c in state.players[0].graveyard] == ["Free spell"])

field_card = card("Free field", card_type=CardType.FIELD)
state = make_state()
held = HandCard(definition=field_card)
state.players[0].hand.append(held)
execute_free_from_hand(state, 0, held.uid, reason="shield_trigger")
check("Free field enters the battle zone",
      [c.definition.name for c in state.players[0].battle_zone] == ["Free field"])

if FAILURES:
    print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
    sys.exit(1)
