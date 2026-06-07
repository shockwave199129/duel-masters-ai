"""
tests/test_action_executor.py — basic action execution and phase flow.
"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.actions import (
    attack_player, charge_mana, cross_gear, deploy_field, execute_tamaseed,
    fortify_castle, generate_cross_gear, pass_action, summon_creature,
)
from core.cards import CardDefinition
from core.enums import CardSubtype, CardType, Civilization, ManaUsage, Phase
from core.player_state import PlayerState
from core.state import GameState, TurnInfo
from core.zones import HandCard, ManaCard, ShieldCard
from engine.action_executor import execute_action

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))


def card(cid, name, cost=1, card_type=CardType.CREATURE):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=cost,
        power=1000 if card_type == CardType.CREATURE else None,
        card_type=card_type, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]), races=frozenset(),
        keywords=frozenset(), effects=tuple(),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


def state(phase=Phase.MAIN):
    filler = card(99, "deck")
    return GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", deck=[filler]),
            PlayerState(player_index=1, player_name="P1", deck=[filler]),
        ),
        turn_info=TurnInfo(turn_number=2, active_player=0, phase=phase, first_player=0),
    )


print("\n" + "═"*60)
print("  DM ENGINE — ACTION EXECUTOR TESTS")
print("═"*60)

c = card(1, "creature")
s = state(Phase.MANA_CHARGE)
hc = HandCard(definition=c)
s.players[0].hand = [hc]
after = execute_action(s, charge_mana(0, hc.uid, c.id))
check("Charge removes card from hand", len(after.players[0].hand) == 0)
check("Charge adds card to mana", len(after.players[0].mana_zone) == 1)

s = state(Phase.MAIN)
hc = HandCard(definition=c)
mana = ManaCard(definition=c)
s.players[0].hand = [hc]
s.players[0].mana_zone = [mana]
after = execute_action(s, summon_creature(0, hc.uid, c.id, [ManaUsage(mana.uid, Civilization.FIRE)]))
check("Summon removes hand card", len(after.players[0].hand) == 0)
check("Summon adds battle creature", len(after.players[0].battle_zone) == 1)
check("Summon taps paid mana", after.players[0].mana_zone[0].is_tapped)

gear_def = card(2, "gear", card_type=CardType.CROSS_GEAR)
s = state(Phase.MAIN)
hc = HandCard(definition=gear_def)
mana = ManaCard(definition=c)
s.players[0].hand = [hc]
s.players[0].mana_zone = [mana]
after = execute_action(s, generate_cross_gear(0, hc.uid, gear_def.id, [ManaUsage(mana.uid, Civilization.FIRE)]))
check("Generate Cross Gear moves card to battle zone", len(after.players[0].battle_zone) == 1)
check("Generate Cross Gear taps mana", after.players[0].mana_zone[0].is_tapped)

target = after.players[0].battle_zone[0]
normal = HandCard(definition=c)
s = state(Phase.MAIN)
s.players[0].battle_zone = [target]
s.players[0].hand = [normal]
s.players[0].mana_zone = [ManaCard(definition=c)]
summoned = execute_action(s, summon_creature(0, normal.uid, c.id, [ManaUsage(s.players[0].mana_zone[0].uid, Civilization.FIRE)]))
gear = summoned.players[0].battle_zone[0]
target_creature = summoned.players[0].battle_zone[1]
summoned.players[0].mana_zone = [ManaCard(definition=c)]
after = execute_action(
    summoned,
    cross_gear(0, gear.uid, gear.id, target_creature.uid, [ManaUsage(summoned.players[0].mana_zone[0].uid, Civilization.FIRE)]),
    validate=False,
)
check("Cross Gear leaves battle zone after crossing", len(after.players[0].battle_zone) == 1)
check("Cross Gear attaches to target", len(after.players[0].battle_zone[0].attached_cards) == 1)

castle_def = card(3, "castle", card_type=CardType.CASTLE)
s = state(Phase.MAIN)
castle_hand = HandCard(definition=castle_def)
shield = ShieldCard(definition=c)
mana = ManaCard(definition=c)
s.players[0].hand = [castle_hand]
s.players[0].shield_zone = [shield]
s.players[0].mana_zone = [mana]
after = execute_action(s, fortify_castle(0, castle_hand.uid, castle_def.id, [ManaUsage(mana.uid, Civilization.FIRE)], shield.uid))
check("Castle leaves hand when fortified", len(after.players[0].hand) == 0)
check("Castle attaches under shield", len(after.players[0].shield_zone[0].fortified_castles) == 1)

field_def = card(4, "field", card_type=CardType.FIELD)
tamaseed_def = card(5, "tamaseed", card_type=CardType.TAMASEED)
for label, defn, ctor in (
    ("Field", field_def, deploy_field),
    ("Tamaseed", tamaseed_def, execute_tamaseed),
):
    s = state(Phase.MAIN)
    hc = HandCard(definition=defn)
    mana = ManaCard(definition=c)
    s.players[0].hand = [hc]
    s.players[0].mana_zone = [mana]
    after = execute_action(s, ctor(0, hc.uid, defn.id, [ManaUsage(mana.uid, Civilization.FIRE)]))
    check(f"{label} moves to battle zone", len(after.players[0].battle_zone) == 1)

s = state(Phase.ATTACK)
creature = after.players[0].battle_zone[0]
creature.has_summoning_sickness = False
s.players[0].battle_zone = [creature]
after = execute_action(s, attack_player(0, creature.uid, creature.id))
check("Attack creates context", after.attack_context is not None)
check("Attack taps attacker", after.players[0].battle_zone[0].is_tapped)
check("Attack moves to declaration phase", after.current_phase == Phase.ATTACK_DECLARE)

s = state(Phase.START_OF_TURN)
s.players[0].deck = [c]
s.players[1].deck = [c]
s.turn_info.turn_number = 1
after = execute_action(s, pass_action(0, "start_of_turn"))
after = execute_action(after, pass_action(0, "draw"))
check("First player skips first draw", len(after.players[0].hand) == 0)
check("Draw phase advances to mana charge", after.current_phase == Phase.MANA_CHARGE)

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
