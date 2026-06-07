"""
tests/test_state_manager.py — Tests for state manager, observation, and information hiding.

Runs without a PostgreSQL connection by constructing CardDefinitions directly.
Covers:
  - GameState initialization
  - Information hiding (own shields hidden, opponent hand hidden)
  - Deck composition known, deck ORDER unknown
  - Global effects (spell restrictions, etc.)
  - PlayerState queries (mana counts, civilization availability)
  - Observation correctness
  - State copy immutability (MCTS safety)
  - Global effect registry queries
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from copy import deepcopy

from core.enums import (
    Civilization, CardType, CardSubtype, Phase, Keyword,
    ActionType, GlobalEffectType, GameResult, Zone,
)
from core.cards import CardDefinition, CardEffect, DeckDefinition
from core.zones import HandCard, ManaCard, ShieldCard, Creature, GraveyardCard
from core.player_state import PlayerState
from core.state import GameState, TurnInfo, PendingTrigger, AwaitedChoice
from core.global_effects import GlobalEffect, GlobalEffectRegistry
from core.observation import Observation
from core.initializer import initialize_game
from core.enums import EffectType, TriggerEvent, EffectAction

PASS = "✅"
FAIL = "❌"
results = []

def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    mark = PASS if ok else FAIL
    print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))
    return ok


# ─── Mock Card Builders ────────────────────────────────────────────────────────

def _no_effects():
    return tuple()

def _make_card(
    card_id: int,
    name: str,
    cost: int,
    power: int,
    civs: list[Civilization],
    card_type: CardType = CardType.CREATURE,
    keywords: list[Keyword] = None,
    subtype: CardSubtype = CardSubtype.NONE,
    races: list[str] = None,
) -> CardDefinition:
    return CardDefinition(
        id=card_id,
        slug=name.lower().replace(" ", "_").replace(",", ""),
        name=name,
        cost=cost,
        power=power if card_type == CardType.CREATURE else None,
        card_type=card_type,
        card_subtype=subtype,
        civilizations=frozenset(civs),
        races=frozenset(races or []),
        keywords=frozenset(keywords or []),
        effects=_no_effects(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
    )

# ── Card pool ─────────────────────────────────────────────────────────────────
BOLSHACK    = _make_card(1,  "Bolshack Dragon",    6,  6000, [Civilization.FIRE],
                         keywords=[Keyword.DOUBLE_BREAKER], races=["Armored Dragon"])
AQUA_HULCUS = _make_card(2,  "Aqua Hulcus",        3,  2000, [Civilization.WATER])
BRONZE_ARM  = _make_card(3,  "Bronze-Arm Tribe",   2,  1000, [Civilization.NATURE])
FAERIE_LIFE = _make_card(4,  "Faerie Life",         2,  None, [Civilization.NATURE],
                         card_type=CardType.SPELL)
TERROR_PIT  = _make_card(5,  "Terror Pit",          6,  None, [Civilization.DARKNESS],
                         card_type=CardType.SPELL, keywords=[Keyword.SHIELD_TRIGGER])
GAIGINGA    = _make_card(6,  "Gaiginga",            7,  7000, [Civilization.FIRE],
                         keywords=[Keyword.DOUBLE_BREAKER, Keyword.SPEED_ATTACKER],
                         races=["Star Dragon"])
CORILE      = _make_card(7,  "Corile",              4,  2000, [Civilization.WATER],
                         keywords=[Keyword.BLOCKER])
ALCADEIAS   = _make_card(8,  "Alcadeias",          10,  9000, [Civilization.LIGHT],
                         keywords=[Keyword.DOUBLE_BREAKER], races=["Angel Command"])
FILLER_9    = _make_card(9,  "Filler Nine",         1,  1000, [Civilization.FIRE])
FILLER_10   = _make_card(10, "Filler Ten",          1,  1000, [Civilization.WATER])
FILLER_11   = _make_card(11, "Filler Eleven",       1,  1000, [Civilization.NATURE])
FILLER_12   = _make_card(12, "Filler Twelve",       1,  1000, [Civilization.LIGHT])

ALL_CARDS = {c.id: c for c in [BOLSHACK, AQUA_HULCUS, BRONZE_ARM, FAERIE_LIFE,
                                 TERROR_PIT, GAIGINGA, CORILE, ALCADEIAS,
                                 FILLER_9, FILLER_10, FILLER_11, FILLER_12]}

def _make_deck(name, owner, counts: dict[CardDefinition, int]) -> DeckDefinition:
    """Build a DeckDefinition (40 cards) for testing."""
    card_counts = {}
    defns = {}
    for card, count in counts.items():
        card_counts[card.id] = count
        defns[card.id] = card

    deck = DeckDefinition(name=name, owner=owner, card_counts=card_counts,
                          card_definitions=defns)
    return deck

DECK_P0 = _make_deck("Fire Deck", "P0", {
    BOLSHACK:   4,
    GAIGINGA:   4,
    FAERIE_LIFE: 4,
    BRONZE_ARM: 4,
    TERROR_PIT: 4,
    AQUA_HULCUS: 4,
    CORILE: 4,
    ALCADEIAS: 4,
    FILLER_9: 4,
    FILLER_10: 4,
})

DECK_P1 = _make_deck("Water Deck", "P1", {
    CORILE:     4,
    AQUA_HULCUS: 4,
    ALCADEIAS:  4,
    FAERIE_LIFE: 4,
    TERROR_PIT: 4,
    BOLSHACK:   4,
    GAIGINGA:   4,
    BRONZE_ARM: 4,
    FILLER_11:  4,
    FILLER_12:  4,
})


# ─── Test Suite ────────────────────────────────────────────────────────────────

print("\n" + "═"*60)
print("  DM ENGINE — STATE MANAGER TEST SUITE")
print("═"*60)

# ═══════════════════════════════════════════════════════════════════
print("\n── 1. Game Initialization ──────────────────────────────────")
# ═══════════════════════════════════════════════════════════════════

state = initialize_game(DECK_P0, DECK_P1, first_player=0, seed=42)

check("GameState created",           state is not None)
check("Phase is START_OF_TURN",      state.current_phase == Phase.START_OF_TURN)
check("Turn 1",                      state.turn_number == 1)
check("Active player is 0",          state.active_player == 0)
check("Result is IN_PROGRESS",       state.result == GameResult.IN_PROGRESS)
check("Not terminal",                not state.is_terminal())

p0 = state.players[0]
p1 = state.players[1]

check("P0 has 5 shields",            p0.shield_count == 5)
check("P1 has 5 shields",            p1.shield_count == 5)
check("P0 hand = 5 cards",           p0.hand_count == 5)
check("P1 hand = 5 cards",           p1.hand_count == 5)
check("P0 deck = 30 cards",          p0.deck_size == 30,  # 40 - 5 shields - 5 hand
      f"got {p0.deck_size}")
check("P1 deck = 30 cards",          p1.deck_size == 30,
      f"got {p1.deck_size}")
check("P0 mana empty at start",      p0.mana_count == 0)
check("P0 battle zone empty",        p0.battle_zone_count == 0)
check("No global effects at start",  state.global_effects.is_empty())


# ═══════════════════════════════════════════════════════════════════
print("\n── 2. Deck Composition (Known) vs Deck Order (Hidden) ──────")
# ═══════════════════════════════════════════════════════════════════

# deck_composition is always known — the player knows what they built
check("P0 deck_composition populated",
      len(p0.deck_composition) > 0)
check("P0 knows they have 4 Bolshacks",
      p0.deck_composition.get(BOLSHACK.id) == 4)
check("P0 knows they have 4 Gaiginga",
      p0.deck_composition.get(GAIGINGA.id) == 4)

# But the actual deck list order is hidden from the player (engine knows it)
# The engine can see deck[0] (top card) but the player observation cannot
check("Engine sees deck order",
      len(p0.deck) == 30)
check("Engine knows top card",
      p0.deck[0] is not None)

# cards_remaining_in_deck_by_id() deduces remaining from seen cards
remaining = p0.cards_remaining_in_deck_by_id()
check("P0 can deduce cards remaining in deck",
      sum(remaining.values()) == 35,
      f"remaining count: {sum(remaining.values())}")


# ═══════════════════════════════════════════════════════════════════
print("\n── 3. Shield Zone — Hidden to Both Players ─────────────────")
# ═══════════════════════════════════════════════════════════════════

# Engine always knows shield contents
check("Engine knows P0 shield 0 card",
      p0.shield_zone[0].definition is not None)
check("Engine knows P1 shield 2 card",
      p1.shield_zone[2].definition is not None)

# Shields are face-down by default
check("All P0 shields face-down initially",
      all(not s.is_revealed for s in p0.shield_zone))
check("All P1 shields face-down initially",
      all(not s.is_revealed for s in p1.shield_zone))

# Simulate revealing a shield (when broken)
s = state.copy()
s.players[0].shield_zone[0].reveal()
check("Shield can be revealed",
      s.players[0].shield_zone[0].is_revealed)
check("Other shields still hidden",
      not s.players[0].shield_zone[1].is_revealed)


# ═══════════════════════════════════════════════════════════════════
print("\n── 4. Observation — Information Hiding ─────────────────────")
# ═══════════════════════════════════════════════════════════════════

# Build state with some board state
s = state.copy()

# Add mana cards for P0
mana1 = ManaCard(definition=BOLSHACK)
mana2 = ManaCard(definition=BRONZE_ARM)
s.players[0].mana_zone.extend([mana1, mana2])

# Add a creature for P0
creature = Creature(
    definition=BOLSHACK,
    controller=0,
    owner=0,
    entered_turn=1,
    has_summoning_sickness=True,
)
s.players[0].battle_zone.append(creature)

# P0's observation
obs_p0 = Observation.build(s, observer=0)
# P1's observation
obs_p1 = Observation.build(s, observer=1)

# P0 sees own hand fully
check("P0 sees own hand cards",
      all(c.is_known for c in obs_p0.self_state.hand_cards))
check("P0 hand count correct",
      obs_p0.self_state.hand_count == 5)

# P0 sees opponent hand count but not contents
check("P0 sees opp hand count",
      obs_p0.opponent_state.hand_count == 5)
check("P0 cannot see opp hand cards",
      all(not c.is_known for c in obs_p0.opponent_state.hand_cards))

# P1 cannot see P0's hand cards
check("P1 cannot see P0 hand contents",
      all(not c.is_known for c in obs_p1.opponent_state.hand_cards))
check("P1 sees P0 hand count",
      obs_p1.opponent_state.hand_count == 5)

# Both players see mana zone
check("P0 sees own mana zone",
      len(obs_p0.self_state.mana_zone) == 2)
check("P1 sees P0 mana zone",
      len(obs_p1.opponent_state.mana_zone) == 2)
check("P0 mana civs visible to P1",
      len(obs_p1.opponent_state.mana_zone[0].civilizations) > 0)

# Both players see battle zone
check("P0 sees own creature",
      len(obs_p0.self_state.battle_zone) == 1)
check("P1 sees P0 creature",
      len(obs_p1.opponent_state.battle_zone) == 1)
check("Creature name visible to opponent",
      obs_p1.opponent_state.battle_zone[0].name == "Bolshack Dragon")
check("Creature power visible to opponent",
      obs_p1.opponent_state.battle_zone[0].current_power == 6000)

# Shield counts visible but not contents
check("P0 sees own shield count",
      obs_p0.self_state.shield_count == 5)
check("P1 sees P0 shield count",
      obs_p1.opponent_state.shield_count == 5)

# P0 knows own deck composition
check("P0 sees own deck composition",
      obs_p0.self_state.own_deck_composition is not None)
check("P0 deck composition has Bolshack x4",
      obs_p0.self_state.own_deck_composition.get(BOLSHACK.id) == 4)

# P0 cannot see P1's deck composition
check("P0 cannot see opp deck composition",
      obs_p0.opponent_state.own_deck_composition is None)

# Turn/phase info visible to both
check("P0 sees turn number",
      obs_p0.turn_number == 1)
check("P1 sees turn number",
      obs_p1.turn_number == 1)
check("P0 sees current phase",
      obs_p0.current_phase == Phase.START_OF_TURN)
check("is_my_turn correct for P0 (active)",
      obs_p0.is_my_turn == True)
check("is_my_turn correct for P1 (inactive)",
      obs_p1.is_my_turn == False)


# ═══════════════════════════════════════════════════════════════════
print("\n── 5. PlayerState — Mana & Civilization Queries ────────────")
# ═══════════════════════════════════════════════════════════════════

# Build a more complex mana zone
s2 = state.copy()
fire_mana   = ManaCard(definition=BOLSHACK,    is_tapped=False)  # Fire
water_mana  = ManaCard(definition=AQUA_HULCUS, is_tapped=False)  # Water
nature_mana = ManaCard(definition=BRONZE_ARM,  is_tapped=True)   # Nature (tapped)
s2.players[0].mana_zone = [fire_mana, water_mana, nature_mana]

p0_2 = s2.players[0]

check("Mana count = 3",              p0_2.mana_count == 3)
check("Available mana = 2",          p0_2.available_mana == 2)
check("Tapped mana = 1",             p0_2.tapped_mana == 1)
check("Fire available",              Civilization.FIRE in p0_2.available_civilizations())
check("Water available",             Civilization.WATER in p0_2.available_civilizations())
check("Nature NOT available (tapped)", Civilization.NATURE not in p0_2.available_civilizations())
check("Darkness NOT available",      Civilization.DARKNESS not in p0_2.available_civilizations())
check("All mana civs includes Nature", Civilization.NATURE in p0_2.all_mana_civilizations())
check("Count Fire mana = 1",         p0_2.count_mana_of_civilization(Civilization.FIRE) == 1)
check("Count cards in mana zone = 3", p0_2.count_cards_in_zone(Zone.MANA_ZONE) == 3)
check("Count Fire in mana = 1",
      p0_2.count_cards_in_zone(Zone.MANA_ZONE, Civilization.FIRE) == 1)


# ═══════════════════════════════════════════════════════════════════
print("\n── 6. Creature State Tracking ──────────────────────────────")
# ═══════════════════════════════════════════════════════════════════

s3 = state.copy()

bolsh = Creature(definition=BOLSHACK, controller=0, owner=0,
                 entered_turn=1, has_summoning_sickness=True)
gaig  = Creature(definition=GAIGINGA, controller=0, owner=0,
                 entered_turn=1, has_summoning_sickness=False)
corl  = Creature(definition=CORILE, controller=1, owner=1,
                 entered_turn=1, has_summoning_sickness=False)

s3.players[0].battle_zone = [bolsh, gaig]
s3.players[1].battle_zone = [corl]

# Summoning sickness
check("Bolshack has summoning sickness", bolsh.has_summoning_sickness)
check("Bolshack CANNOT attack (sick, no speed atk)", not bolsh.can_attack())
check("Gaiginga has no sickness",        not gaig.has_summoning_sickness)
check("Gaiginga CAN attack (speed_attacker)", gaig.can_attack())
check("Corile CAN attack",               corl.can_attack())

# Keyword checks
check("Bolshack has Double Breaker",     bolsh.has_keyword(Keyword.DOUBLE_BREAKER))
check("Gaiginga has Speed Attacker",     gaig.has_keyword(Keyword.SPEED_ATTACKER))
check("Corile has Blocker",              corl.is_blocker())
check("Bolshack NOT blocker",            not bolsh.is_blocker())

# Shield breaks
check("Bolshack breaks 2 shields",       bolsh.shields_broken_on_attack() == 2)
check("Gaiginga breaks 2 shields",       gaig.shields_broken_on_attack() == 2)
check("Corile breaks 1 shield",          corl.shields_broken_on_attack() == 1)

# Power
check("Bolshack base power = 6000",      bolsh.base_power == 6000)
check("Gaiginga base power = 7000",      gaig.base_power == 7000)
check("compute_power matches base",      bolsh.compute_power() == 6000)

# Temp flags
bolsh.set_flag("cannot_attack", True)
check("cannot_attack flag set",          not bolsh.can_attack())
bolsh.clear_flag("cannot_attack")
check("cannot_attack flag cleared",      bolsh.can_attack() == False)  # still sick!

# Tapping
gaig.tap()
check("Gaiginga tapped",                 gaig.is_tapped)
check("Tapped Gaiginga cannot attack",   not gaig.can_attack())
gaig.untap()
check("Untapped Gaiginga can attack",    gaig.can_attack())

# Battle zone queries
check("P0 attackable creatures = 1",     len(s3.players[0].get_attackable_creatures()) == 1)
check("P1 blocker creatures = 1",        len(s3.players[1].get_blocker_creatures()) == 1)
check("find_creature by uid works",
      s3.players[0].find_creature(bolsh.uid) is bolsh)
check("find_creature_anywhere works",
      s3.find_creature_anywhere(corl.uid) == (1, corl))


# ═══════════════════════════════════════════════════════════════════
print("\n── 7. Global Effects (e.g. Spell Restrictions) ─────────────")
# ═══════════════════════════════════════════════════════════════════

# Simulate Alcadeias effect: "players can't cast non-Light spells"
s4 = state.copy()
alcadeias_creature = Creature(definition=ALCADEIAS, controller=1, owner=1,
                               entered_turn=1, has_summoning_sickness=False)
s4.players[1].battle_zone.append(alcadeias_creature)

# Add the global effect (would normally be done by EffectExecutor)
alc_effect = GlobalEffect(
    effect_type=GlobalEffectType.RESTRICT_SPELL_CIVILIZATION,
    applied_by_uid=alcadeias_creature.uid,
    applied_by_card=ALCADEIAS.id,
    controller=1,
    target_player=None,   # affects BOTH players
    duration="while_in_play",
    allowed_civilizations=frozenset([Civilization.LIGHT]),
)
s4.global_effects.add(alc_effect)

gr = s4.global_effects

# Terror Pit is Darkness — should be blocked
check("Darkness spell blocked by Alcadeias (P0)",
      not gr.can_cast_spell(0, TERROR_PIT.civilizations))
check("Darkness spell blocked by Alcadeias (P1)",
      not gr.can_cast_spell(1, TERROR_PIT.civilizations))

# Faerie Life is Nature — should also be blocked
check("Nature spell blocked by Alcadeias",
      not gr.can_cast_spell(0, FAERIE_LIFE.civilizations))

# A hypothetical Light spell would be fine
light_civs = frozenset([Civilization.LIGHT])
check("Light spell allowed through Alcadeias",
      gr.can_cast_spell(0, light_civs))

# Summons not affected (Alcadeias only restricts spells)
check("Summon not restricted by Alcadeias",
      gr.can_summon_creature(0, BOLSHACK.civilizations))

# Check restriction descriptions
restrictions = gr.active_restrictions_for_player(0)
check("Restriction description generated",
      len(restrictions) > 0)
check("Restriction mentions Light",
      any("Light" in r for r in restrictions))

# Test "lock all spells" effect
s5 = state.copy()
lock_effect = GlobalEffect(
    effect_type=GlobalEffectType.LOCK_ALL_SPELLS,
    applied_by_uid="some_uid",
    applied_by_card=99,
    controller=0,
    target_player=1,   # only affects P1
    duration="until_end_of_turn",
)
s5.global_effects.add(lock_effect)

check("P0 CAN still cast spells (lock targets P1)",
      s5.global_effects.can_cast_spell(0, TERROR_PIT.civilizations))
check("P1 CANNOT cast any spell (lock_all_spells)",
      not s5.global_effects.can_cast_spell(1, FAERIE_LIFE.civilizations))

# EOT expiry
s5.global_effects.expire_eot()
check("Lock removed after EOT expire",
      s5.global_effects.can_cast_spell(1, FAERIE_LIFE.civilizations))

# Remove by source
s4.global_effects.remove_by_source(alcadeias_creature.uid)
check("Alcadeias effect removed when creature leaves",
      s4.global_effects.can_cast_spell(0, TERROR_PIT.civilizations))


# ═══════════════════════════════════════════════════════════════════
print("\n── 8. Mana Restriction Global Effect ───────────────────────")
# ═══════════════════════════════════════════════════════════════════

s6 = state.copy()
no_charge = GlobalEffect(
    effect_type=GlobalEffectType.CANNOT_CHARGE_MANA,
    applied_by_uid="field_uid",
    applied_by_card=99,
    controller=0,
    target_player=0,   # only P0 can't charge
    duration="while_in_play",
)
s6.global_effects.add(no_charge)

check("P0 cannot charge mana (restriction active)",
      not s6.global_effects.can_charge_mana(0))
check("P1 CAN charge mana (restriction only on P0)",
      s6.global_effects.can_charge_mana(1))


# ═══════════════════════════════════════════════════════════════════
print("\n── 9. State Copy Immutability (MCTS Safety) ────────────────")
# ═══════════════════════════════════════════════════════════════════

original = state.copy()
original_hand_count = original.players[0].hand_count

# Make a copy and mutate it
mutated = original.copy()
new_hand_card = HandCard(definition=BOLSHACK)
mutated.players[0].hand.append(new_hand_card)

# Original must be unchanged
check("Original hand unchanged after copy mutation",
      original.players[0].hand_count == original_hand_count)
check("Mutated copy has extra card",
      mutated.players[0].hand_count == original_hand_count + 1)

# Mutate mana on copy
mutated.players[0].mana_zone.append(ManaCard(definition=BRONZE_ARM))
check("Original mana unchanged",
      original.players[0].mana_count == 0)
check("Mutated mana updated",
      mutated.players[0].mana_count == 1)

# Mutate global effects on copy
mutated.global_effects.add(GlobalEffect(
    effect_type=GlobalEffectType.LOCK_ALL_SPELLS,
    applied_by_uid="test", applied_by_card=1,
    controller=0, target_player=None
))
check("Original global effects unchanged",
      original.global_effects.is_empty())
check("Mutated global effects have new entry",
      not mutated.global_effects.is_empty())


# ═══════════════════════════════════════════════════════════════════
print("\n── 10. Turn Reset ───────────────────────────────────────────")
# ═══════════════════════════════════════════════════════════════════

s7 = state.copy()
p0_7 = s7.players[0]

# Set up some per-turn state
p0_7.has_charged_mana_this_turn = True
m = ManaCard(definition=BRONZE_ARM, is_tapped=True)
p0_7.mana_zone.append(m)
c = Creature(definition=BOLSHACK, controller=0, owner=0,
             entered_turn=1, has_summoning_sickness=True, is_tapped=True)
c.has_attacked_this_turn = True
p0_7.battle_zone.append(c)

# Untap all
p0_7.untap_all()
check("Mana untapped after untap_all",
      not p0_7.mana_zone[0].is_tapped)
check("Creature untapped after untap_all",
      not p0_7.battle_zone[0].is_tapped)

# Reset turn flags
p0_7.reset_turn_flags()
check("has_charged_mana reset",
      not p0_7.has_charged_mana_this_turn)
check("has_attacked_this_turn reset",
      not p0_7.battle_zone[0].has_attacked_this_turn)

# Clear summoning sickness (end of turn)
p0_7.clear_summoning_sickness()
check("Summoning sickness cleared EOT",
      not p0_7.battle_zone[0].has_summoning_sickness)


# ═══════════════════════════════════════════════════════════════════
print("\n── 11. Effect Stack ─────────────────────────────────────────")
# ═══════════════════════════════════════════════════════════════════

from core.state import EffectStack, AwaitedChoice, PendingTrigger

stack = EffectStack()
check("Stack empty initially",         not stack.has_pending())

# Add a shield trigger to the queue
shield_card = ShieldCard(definition=TERROR_PIT)
stack.add_shield_trigger(0, shield_card)
check("Shield trigger queued",         len(stack.shield_trigger_queue) == 1)

popped = stack.pop_shield_trigger()
check("Shield trigger popped",         popped is not None)
check("Popped correct player",         popped[0] == 0)
check("Popped correct card",           popped[1].id == TERROR_PIT.id)
check("Queue empty after pop",         len(stack.shield_trigger_queue) == 0)

# Awaited choice
choice = AwaitedChoice(
    choice_type="yes_no",
    player=0,
    effect=None,
    source_uid="uid_123",
    valid_options=[True, False],
    prompt="Use Shield Trigger?",
)
stack.set_choice(choice)
check("Stack waiting for choice",      stack.is_waiting_for_choice())
check("Correct choice type",           stack.awaited_choice.choice_type == "yes_no")
check("Correct player for choice",     stack.awaited_choice.player == 0)

stack.clear_choice()
check("Choice cleared",                not stack.is_waiting_for_choice())


# ═══════════════════════════════════════════════════════════════════
# RESULTS SUMMARY
# ═══════════════════════════════════════════════════════════════════

passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
total  = len(results)

print(f"\n{'═'*60}")
print(f"  RESULTS: {passed}/{total} passed, {failed} failed")
print(f"{'═'*60}")

if failed > 0:
    print("\n  FAILURES:")
    for name, ok, detail in results:
        if not ok:
            print(f"    {FAIL} {name}" + (f" — {detail}" if detail else ""))

print()
sys.exit(0 if failed == 0 else 1)
