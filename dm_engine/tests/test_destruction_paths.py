"""
tests/test_destruction_paths.py - one leave-battle-zone path for every removal.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.cards import CardDefinition, CardEffect
from core.enums import (
    CardSubtype, CardType, Civilization, EffectAction, EffectType, Phase, TriggerEvent,
)
from core.player_state import PlayerState
from core.state import AttackContext, GameState, PendingTrigger, TurnInfo
from core.zones import Creature, EvolutionStackEntry
from engine.battle_resolver import resolve_battle
from engine.cards.effect_actions.zone_ops import _do_banish_to_abyss, _do_return_to_hand
from engine.replacement import EventType, ReplacementEffect
from engine.sba_checker import check_state_based_actions

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


def effect(event, action=EffectAction.DRAW):
    return CardEffect(
        card_id=1, ability_index=0, raw_text="test", effect_type=EffectType.TRIGGERED,
        trigger_event=event, effect_action=action, trigger_condition={}, effect_target={},
        effect_value={}, is_optional=False, is_replacement=False, active_in_phase=("any",),
        active_in_zone=("battle_zone",), parse_confidence=1.0,
    )


def card(cid, name, power=1000, card_type=CardType.CREATURE, effects=()):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=1, power=power, card_type=card_type,
        card_subtype=CardSubtype.NONE, civilizations=frozenset([Civilization.FIRE]),
        races=frozenset(), keywords=frozenset(), effects=tuple(effects),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


def make_state():
    filler = card(99, "deck")
    return GameState(
        players=[
            PlayerState(player_index=0, player_name="P0", deck=[filler] * 5),
            PlayerState(player_index=1, player_name="P1", deck=[filler] * 5),
        ],
        turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.BATTLE),
    )


def put(state, player, definition, **kwargs):
    creature = Creature(definition=definition, controller=player, owner=player, **kwargs)
    state.players[player].battle_zone.append(creature)
    creature.apply_static_effects(state)
    return creature


def evolution(state, player, power=1000, effects=()):
    creature = put(state, player, card(2, "EvoTop", power, effects=effects))
    creature.evolution_stack.append(EvolutionStackEntry(definition=card(3, "Base"), owner=player))
    return creature


def attack(state, attacker, target):
    state.attack_context = AttackContext(
        attacker_uid=attacker.uid, attacker_player=attacker.controller,
        target_type="creature", target_uid=target.uid,
    )
    return resolve_battle(state)


def names(cards):
    return sorted(c.definition.name for c in cards)


def fired(state):
    return sorted(t.effect.trigger_event.name for t in state.effect_stack.pending_triggers)


LEAVE_TRIGGERS = (effect(TriggerEvent.ON_DESTROY), effect(TriggerEvent.ON_LEAVE_BATTLE_ZONE))

# H1 + H2: losing a battle fires triggers and keeps every card
state = make_state()
loser = evolution(state, 0, effects=LEAVE_TRIGGERS)
winner = put(state, 1, card(4, "Big", 9000, effects=[effect(TriggerEvent.ON_WIN_BATTLE)]))
after = attack(state, loser, winner)
check("Battle fires destroy and leave triggers",
      {"ON_DESTROY", "ON_LEAVE_BATTLE_ZONE"} <= set(fired(after)), str(fired(after)))
check("Evolution under-cards reach the graveyard", names(after.players[0].graveyard) == ["Base", "EvoTop"],
      str(names(after.players[0].graveyard)))
# H5: an unblocked defender's trigger belongs to its own controller
wins = [t for t in after.effect_stack.pending_triggers if t.effect.trigger_event == TriggerEvent.ON_WIN_BATTLE]
check("Defender's win trigger belongs to the defender", [t.controller for t in wins] == [1],
      str([t.controller for t in wins]))

# Power 0 destruction goes through the same path
state = make_state()
put(state, 0, card(5, "Zero", 0, effects=LEAVE_TRIGGERS))
after = check_state_based_actions(state)
check("Power 0 fires destroy and leave triggers", fired(after) == ["ON_DESTROY", "ON_LEAVE_BATTLE_ZONE"])

# H3: cannot be destroyed
for power, lost in ((-1000, False), (1000, True)):
    state = make_state()
    creature = put(state, 0, card(6, "Wall", power))
    creature.temp_flags["cannot_be_destroyed"] = True
    if lost:
        creature.temp_flags["lost_battle"] = True
    after = check_state_based_actions(state)
    check(f"Indestructible survives (power {power}, lost_battle={lost})", len(after.players[0].battle_zone) == 1)
    check("Prevented battle loss is cleared", "lost_battle" not in after.players[0].battle_zone[0].temp_flags)

# M10: a destroy replacement saves the creature from one battle loss
state = make_state()
creature = put(state, 0, card(7, "Saved"))
creature.temp_flags["lost_battle"] = True
state.replacement_effects.register(ReplacementEffect(
    event_type=EventType.DESTROY, source_uid=creature.uid, source_card_id=7,
    controller=0, replacement_action="prevent",
))
after = check_state_based_actions(state)
check("Replacement keeps a battle loser", len(after.players[0].battle_zone) == 1)
check("Replacement can apply to a later event", not any(e.is_used for e in after.replacement_effects.effects))

# A replacement cannot loop forever on a creature that stays at 0 power
creature = after.players[0].battle_zone[0]
creature.temp_flags["lost_battle"] = False
creature.definition = card(7, "Saved", 0)
after = check_state_based_actions(after)
check("Zero power still ends after one replacement", not after.players[0].battle_zone)

# Attached Cross Gear stays; seals and other attachments go to the graveyard
state = make_state()
creature = put(state, 0, card(8, "Crossed", 0))
creature.attached_cards.append(card(9, "Gear", None, CardType.CROSS_GEAR))
creature.seals.append(card(10, "Seal"))
after = check_state_based_actions(state)
check("Cross Gear stays in the battle zone", names(after.players[0].battle_zone) == ["Gear"])
check("Seals reach the graveyard", names(after.players[0].graveyard) == ["Crossed", "Seal"])


def trigger_for(target, action):
    return PendingTrigger(
        effect=effect(TriggerEvent.ON_SUMMON, action), source_uid="src", source_card_id=1,
        controller=0, trigger_data={"target_uid": target.uid},
    )


state = make_state()
target = evolution(state, 1, effects=[effect(TriggerEvent.ON_LEAVE_BATTLE_ZONE)])
_do_return_to_hand(state, 0, trigger_for(target, EffectAction.RETURN_TO_HAND))
check("Return to hand takes the whole stack", names(state.players[1].hand) == ["Base", "EvoTop"])
check("Return to hand fires leave triggers", fired(state) == ["ON_LEAVE_BATTLE_ZONE"])

# M8: banish an opponent's creature
state = make_state()
target = evolution(state, 1, effects=LEAVE_TRIGGERS)
_do_banish_to_abyss(state, 0, trigger_for(target, EffectAction.BANISH_TO_ABYSS))
check("Banish reaches the opponent's creature", not state.players[1].battle_zone)
check("Banished stack goes to its owner's abyss",
      sorted(d.name for d in state.players[1].abyss_zone) == ["Base", "EvoTop"])
check("Banish is leaving, not destruction", fired(state) == ["ON_LEAVE_BATTLE_ZONE"], str(fired(state)))

if FAILURES:
    print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
    sys.exit(1)
