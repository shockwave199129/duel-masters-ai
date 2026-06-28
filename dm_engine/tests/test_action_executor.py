"""
tests/test_action_executor.py — basic action execution and phase flow.
"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.actions import (
    attack_player, cast_spell, charge_mana, cross_gear, deploy_field,
    execute_tamaseed, fortify_castle, generate_cross_gear, pass_action,
    summon_creature,
)
from core.cards import CardDefinition, CardEffect
from core.enums import CardSubtype, CardType, Civilization, EffectAction, EffectType, ManaUsage, Phase, TriggerEvent
from core.player_state import PlayerState
from core.state import GameState, TurnInfo
from core.zones import Creature as ZCreature, HandCard, ManaCard, ShieldCard
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
attacker = ZCreature(definition=c, controller=0, owner=0, entered_turn=2, has_summoning_sickness=False)
s.players[0].battle_zone = [attacker]
after = execute_action(s, attack_player(0, attacker.uid, attacker.id))
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

# ── Spell Casting Tests (rules 600-608) ─────────────────────────────────────

def spell_card(cid, name, cost, effects):
    """Helper: create a CardDefinition for a spell with given effects."""
    return CardDefinition(
        id=cid, slug=name, name=name, cost=cost,
        power=None, card_type=CardType.SPELL, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]), races=frozenset(),
        keywords=frozenset(), effects=tuple(effects),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )

def make_effect(effect_action, amount=None):
    """Helper: create a CardEffect with EffectType.SPELL."""
    effect_value = {"amount": amount} if amount is not None else {}
    return CardEffect(
        card_id=0, ability_index=0, raw_text="test",
        effect_type=EffectType.SPELL,
        trigger_event=TriggerEvent.ON_CAST,
        effect_action=effect_action,
        trigger_condition={},
        effect_target={},
        effect_value=effect_value,
        is_optional=False, is_replacement=False,
        active_in_phase=("main",), active_in_zone=("hand",),
        parse_confidence=1.0,
    )


# Test 1: Spell with DRAW effect draws cards when cast
print("\n--- Spell: DRAW effect ---")
draw_spell = spell_card(200, "Draw Spell", 1,
    [make_effect(EffectAction.DRAW, amount=2)])
s = state(Phase.MAIN)
s.players[0].deck = [c, c, c, c, c]
hc = HandCard(definition=draw_spell)
mana = ManaCard(definition=c)
s.players[0].hand = [hc]
s.players[0].mana_zone = [mana]
deck_before = len(s.players[0].deck)
after = execute_action(s, cast_spell(0, hc.uid, draw_spell.id,
    [ManaUsage(mana.uid, Civilization.FIRE)]))
check("DRAW spell draws 2 cards", len(after.players[0].hand) == 2)
check("Deck decreases by 2", len(after.players[0].deck) == deck_before - 2)
check("Spell ends up in graveyard", len(after.players[0].graveyard) == 1
       and after.players[0].graveyard[0].definition.id == draw_spell.id)

# Test 2: Spell with DESTROY effect destroys the target creature
print("\n--- Spell: DESTROY effect ---")
target_creature_def = card(201, "Target Creature", cost=3)
target_creature = ZCreature(
    definition=target_creature_def, controller=1, owner=1,
    entered_turn=1, has_summoning_sickness=False,
)
# Build a destroy spell whose effect carries target_uid in trigger_data.
# The action_executor creates PendingTrigger with empty trigger_data,
# so we test the pipeline by verifying the spell card is cast and the
# effect is queued. For a full destroy test, we use validate=False and
# a direct trigger_data injection via a manual PendingTrigger.
destroy_spell = spell_card(202, "Destroy Spell", 1,
    [make_effect(EffectAction.DESTROY)])
s = state(Phase.MAIN)
s.players[1].battle_zone = [target_creature]
hc = HandCard(definition=destroy_spell)
mana = ManaCard(definition=c)
s.players[0].hand = [hc]
s.players[0].mana_zone = [mana]
# Use validate=False since action_generator doesn't know about our custom card
after = execute_action(s, cast_spell(0, hc.uid, destroy_spell.id,
    [ManaUsage(mana.uid, Civilization.FIRE)]), validate=False)
check("Destroy spell moves to graveyard", len(after.players[0].graveyard) == 1)
# Without target_uid in trigger_data, destroy has no target → creature survives
check("DESTROY without target_uid does not destroy", len(after.players[1].battle_zone) == 1)

# Now test that DESTROY actually works when target_uid is provided via trigger_data.
# We manually queue a PendingTrigger with target_uid to verify the pipeline.
from core.state import PendingTrigger
from engine.trigger_resolver import resolve_pending_triggers
s2 = state(Phase.MAIN)
target2 = ZCreature(
    definition=target_creature_def, controller=1, owner=1,
    entered_turn=1, has_summoning_sickness=False,
)
s2.players[1].battle_zone = [target2]
destroy_fx = make_effect(EffectAction.DESTROY)
trigger = PendingTrigger(
    effect=destroy_fx,
    source_uid="spell-uid",
    source_card_id=202,
    controller=0,
    trigger_data={"target_uid": target2.uid},
)
s2.effect_stack.add_trigger(trigger)
s2 = resolve_pending_triggers(s2)
check("DESTROY with target_uid destroys creature", len(s2.players[1].battle_zone) == 0)
check("Destroyed creature goes to graveyard", len(s2.players[1].graveyard) == 1)

# Test 3: Multiple spell effects on one card all resolve in order
print("\n--- Spell: Multiple effects (DRAW + DRAW) ---")
multi_spell = spell_card(300, "Multi Draw", 1, [
    make_effect(EffectAction.DRAW, amount=1),
    make_effect(EffectAction.DRAW, amount=1),
])
s = state(Phase.MAIN)
s.players[0].deck = [c, c, c, c, c, c]
hc = HandCard(definition=multi_spell)
mana = ManaCard(definition=c)
s.players[0].hand = [hc]
s.players[0].mana_zone = [mana]
deck_before = len(s.players[0].deck)
after = execute_action(s, cast_spell(0, hc.uid, multi_spell.id,
    [ManaUsage(mana.uid, Civilization.FIRE)]), validate=False)
check("Multi-effect spell draws 2 cards total", len(after.players[0].hand) == 2)
check("Deck decreases by 2", len(after.players[0].deck) == deck_before - 2)
check("Multi-effect spell in graveyard", len(after.players[0].graveyard) == 1)

# Test 4: Spell with no SPELL-type effects (only TRIGGERED effects)
print("\n--- Spell: No SPELL-type effects ---")
triggered_effect = CardEffect(
    card_id=400, ability_index=0, raw_text="On enter: draw",
    effect_type=EffectType.TRIGGERED,
    trigger_event=TriggerEvent.ON_CAST,
    effect_action=EffectAction.DRAW,
    trigger_condition={},
    effect_target={},
    effect_value={"amount": 1},
    is_optional=False, is_replacement=False,
    active_in_phase=("main",), active_in_zone=("hand",),
    parse_confidence=1.0,
)
no_spell_fx = spell_card(400, "Triggered Only", 1, [triggered_effect])
s = state(Phase.MAIN)
s.players[0].deck = [c, c, c]
hc = HandCard(definition=no_spell_fx)
mana = ManaCard(definition=c)
s.players[0].hand = [hc]
s.players[0].mana_zone = [mana]
deck_before = len(s.players[0].deck)
after = execute_action(s, cast_spell(0, hc.uid, no_spell_fx.id,
    [ManaUsage(mana.uid, Civilization.FIRE)]), validate=False)
check("Non-SPELL effect spell moves to graveyard", len(after.players[0].graveyard) == 1)
check("TRIGGERED effect not queued as SPELL, no draw", len(after.players[0].deck) == deck_before)

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
