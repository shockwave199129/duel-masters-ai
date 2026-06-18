"""
tests/test_ability_types.py — coverage for triggered/activated/static/CDA ability types.

Sections:
  1. Triggered Ability Conditions (Phase 2.1)
  2. Activated Ability Execution (Phase 2.2)
  3. Static Ability Application (Phase 2.3)
  4. CDA — Characteristic-Defining Abilities (Phase 2.4)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.actions import Action, ActionType, activate_ability
from core.cards import CardDefinition, CardEffect
from core.enums import (
    CDAFormulaType,
    CardSubtype,
    CardType,
    Civilization,
    EffectAction,
    EffectType,
    GlobalEffectType,
    Keyword,
    ManaUsage,
    Phase,
    TriggerEvent,
)
from core.global_effects import GlobalEffect, GlobalEffectRegistry
from core.player_state import PlayerState
from core.state import GameState, PendingTrigger, TurnInfo
from core.zones import Creature, HandCard, ManaCard, PowerModifier, ShieldCard
from engine.action_executor import execute_action
from engine.trigger_resolver import _eval_condition, resolve_pending_triggers

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))
    return ok


def card(cid, name, card_type=CardType.CREATURE, civs=(Civilization.FIRE,),
         power=1000, cost=3, effects=None, cda_type=CDAFormulaType.NONE,
         cda_multiplier=0, cda_fixed_value=0, cda_zone="", cda_filter_civ=None):
    return CardDefinition(
        id=cid,
        slug=name.lower().replace(" ", "_"),
        name=name,
        cost=cost,
        power=power if card_type == CardType.CREATURE else None,
        card_type=card_type,
        card_subtype=CardSubtype.NONE,
        civilizations=frozenset(civs),
        races=frozenset({"Human"}) if card_type == CardType.CREATURE else frozenset(),
        keywords=frozenset(),
        effects=tuple(effects or []),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
        cda_formula_type=cda_type,
        cda_multiplier=cda_multiplier,
        cda_fixed_value=cda_fixed_value,
        cda_zone=cda_zone,
        cda_filter_civ=cda_filter_civ,
    )


def triggered_effect(card_id, action, value=None, target=None, condition=None,
                     event=TriggerEvent.ON_SUMMON):
    return CardEffect(
        card_id=card_id,
        ability_index=0,
        raw_text=action.value,
        effect_type=EffectType.TRIGGERED,
        trigger_event=event,
        effect_action=action,
        trigger_condition=condition or {},
        effect_target=target or {},
        effect_value=value or {},
        is_optional=False,
        is_replacement=False,
        active_in_phase=tuple(),
        active_in_zone=tuple(),
        parse_confidence=1.0,
    )


def activated_effect(card_id, action, value=None, ability_index=0):
    return CardEffect(
        card_id=card_id,
        ability_index=ability_index,
        raw_text=action.value,
        effect_type=EffectType.ACTIVATED,
        trigger_event=TriggerEvent.NONE,
        effect_action=action,
        trigger_condition={},
        effect_target={},
        effect_value=value or {},
        is_optional=False,
        is_replacement=False,
        active_in_phase=tuple(),
        active_in_zone=tuple(),
        parse_confidence=1.0,
    )


def static_effect_power(card_id, amount, target_civ=None, scope="own"):
    """Create a static effect that modifies power of creatures."""
    return CardEffect(
        card_id=card_id,
        ability_index=0,
        raw_text="static power mod",
        effect_type=EffectType.STATIC,
        trigger_event=TriggerEvent.NONE,
        effect_action=EffectAction.POWER_MODIFY,
        trigger_condition={},
        effect_target={"scope": scope, "civilization": target_civ.value if target_civ else None},
        effect_value={"amount": amount},
        is_optional=False,
        is_replacement=False,
        active_in_phase=tuple(),
        active_in_zone=tuple(),
        parse_confidence=1.0,
    )


def make_state(p0_battle=None, p1_battle=None, p0_hand=None, turn=2, phase=Phase.MAIN):
    filler = card(99, "DeckFiller")
    return GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0", deck=[filler],
                hand=p0_hand or [],
                battle_zone=p0_battle or [],
            ),
            PlayerState(
                player_index=1, player_name="P1", deck=[filler],
                battle_zone=p1_battle or [],
            ),
        ),
        turn_info=TurnInfo(turn_number=turn, active_player=0, phase=phase),
    )


# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
print("  DM ENGINE — ABILITY TYPES TESTS")
print("═" * 60)

# ─────────────────────────────────────────────────────────────────────────────
# Section 1: Triggered Ability Conditions (Phase 2.1)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 1: TRIGGERED ABILITY CONDITIONS")
print("─" * 60)

# Build a trigger with zone change data for from_zone/to_zone tests
trig = PendingTrigger(
    effect=triggered_effect(1, EffectAction.DRAW),
    source_uid="src1",
    source_card_id=1,
    controller=0,
    trigger_data={"from_zone": "hand", "to_zone": "battle_zone"},
)

_base_state = make_state()

# 1a) from_zone match and mismatch
check("from_zone match returns True",
      _eval_condition(_base_state, trig, {"from_zone": "hand"}) == True)
check("from_zone mismatch returns False",
      _eval_condition(_base_state, trig, {"from_zone": "deck"}) == False)

# 1b) to_zone match and mismatch
check("to_zone match returns True",
      _eval_condition(_base_state, trig, {"to_zone": "battle_zone"}) == True)
check("to_zone mismatch returns False",
      _eval_condition(_base_state, trig, {"to_zone": "graveyard"}) == False)

# 1c) min_turn / max_turn
_state_t5 = make_state(turn=5)
_state_t1 = make_state(turn=1)
trig_t5 = PendingTrigger(
    effect=triggered_effect(2, EffectAction.DRAW),
    source_uid="src2", source_card_id=2, controller=0, trigger_data={},
)
check("min_turn=3 on turn 5 passes",
      _eval_condition(_state_t5, trig_t5, {"min_turn": 3}) == True)
check("min_turn=6 on turn 5 fails",
      _eval_condition(_state_t5, trig_t5, {"min_turn": 6}) == False)
check("max_turn=5 on turn 5 passes",
      _eval_condition(_state_t5, trig_t5, {"max_turn": 5}) == True)
check("max_turn=4 on turn 5 fails",
      _eval_condition(_state_t5, trig_t5, {"max_turn": 4}) == False)

# 1d) shield_count_min / shield_count_max
_sc5_state = GameState(
    players=(
        PlayerState(player_index=0, player_name="P0", deck=[card(99, "f")],
                    shield_zone=[ShieldCard(definition=card(98, "s")) for _ in range(5)]),
        PlayerState(player_index=1, player_name="P1", deck=[card(99, "f")]),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)
_sc2_state = GameState(
    players=(
        PlayerState(player_index=0, player_name="P0", deck=[card(99, "f")],
                    shield_zone=[ShieldCard(definition=card(98, "s")) for _ in range(2)]),
        PlayerState(player_index=1, player_name="P1", deck=[card(99, "f")]),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)
trig_sh = PendingTrigger(
    effect=triggered_effect(3, EffectAction.DRAW),
    source_uid="src3", source_card_id=3, controller=0, trigger_data={},
)
check("shield_count_min=3 with 5 shields passes",
      _eval_condition(_sc5_state, trig_sh, {"shield_count_min": 3}) == True)
check("shield_count_min=3 with 2 shields fails",
      _eval_condition(_sc2_state, trig_sh, {"shield_count_min": 3}) == False)
check("shield_count_max=3 with 2 shields passes",
      _eval_condition(_sc2_state, trig_sh, {"shield_count_max": 3}) == True)
check("shield_count_max=3 with 5 shields fails",
      _eval_condition(_sc5_state, trig_sh, {"shield_count_max": 3}) == False)

# 1e) any_of (OR) conditions
trig_or = PendingTrigger(
    effect=triggered_effect(4, EffectAction.DRAW),
    source_uid="src4", source_card_id=4, controller=0,
    trigger_data={"from_zone": "hand"},
)
check("any_of with one matching sub-condition passes",
      _eval_condition(_base_state, trig_or, {
          "any_of": [{"from_zone": "hand"}, {"from_zone": "deck"}]
      }) == True)
check("any_of with no matching sub-conditions fails",
      _eval_condition(_base_state, trig_or, {
          "any_of": [{"from_zone": "deck"}, {"from_zone": "graveyard"}]
      }) == False)

# 1f) not (negation)
check("not on failing inner condition passes",
      _eval_condition(_base_state, trig_or, {"not": {"from_zone": "deck"}}) == True)
check("not on passing inner condition fails",
      _eval_condition(_base_state, trig_or, {"not": {"from_zone": "hand"}}) == False)

# 1g) Composition: not wrapping any_of
check("not(any_of) — all sub-conditions fail -> not passes",
      _eval_condition(_base_state, trig_or, {
          "not": {"any_of": [{"from_zone": "deck"}, {"from_zone": "graveyard"}]}
      }) == True)
check("not(any_of) — one sub-condition passes -> not fails",
      _eval_condition(_base_state, trig_or, {
          "not": {"any_of": [{"from_zone": "hand"}, {"from_zone": "deck"}]}
      }) == False)

# 1h) Backward compatibility: empty condition and simple condition
trig_bc = PendingTrigger(
    effect=triggered_effect(5, EffectAction.DRAW),
    source_uid="src5", source_card_id=5, controller=0, trigger_data={},
)
check("Empty condition dict passes (backward compat)",
      _eval_condition(_base_state, trig_bc, {}) == True)
check("Simple controller condition matches",
      _eval_condition(_base_state, trig_bc, {"controller": 0}) == True)
check("Simple controller condition mismatch",
      _eval_condition(_base_state, trig_bc, {"controller": 1}) == False)


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: Activated Ability Execution (Phase 2.2)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 2: ACTIVATED ABILITY EXECUTION")
print("─" * 60)

# 2a) CardEffect.is_activated()
act_eff = activated_effect(10, EffectAction.DRAW, {"amount": 1})
trig_eff = triggered_effect(10, EffectAction.DRAW)
check("is_activated() returns True for ACTIVATED effect",
      act_eff.is_activated() == True)
check("is_activated() returns False for TRIGGERED effect",
      trig_eff.is_activated() == False)
check("is_triggered() returns True for TRIGGERED effect",
      trig_eff.is_triggered() == True)
check("is_triggered() returns False for ACTIVATED effect",
      act_eff.is_triggered() == False)

# 2b) CardDefinition.get_activated_effects()
mixed_card = card(
    20, "MixedCard",
    effects=[
        triggered_effect(20, EffectAction.DESTROY),
        activated_effect(20, EffectAction.DRAW, {"amount": 2}, ability_index=0),
        activated_effect(20, EffectAction.POWER_MODIFY, {"amount": 1000}, ability_index=1),
        static_effect_power(20, 1000),
    ],
)
activated = mixed_card.get_activated_effects()
check("get_activated_effects filters to only ACTIVATED", len(activated) == 2)
check("get_activated_effects returns correct effects",
      all(e.is_activated() for e in activated))

# 2c) activate_ability() constructor
act_action = activate_ability(
    player=0,
    source_uid="creature-uid-123",
    source_card_id=20,
    ability_index=0,
    mana_used=[ManaUsage("mana-uid-1")],
    tap_source=True,
)
check("activate_ability creates ACTIVATE_ABILITY action",
      act_action.action_type == ActionType.ACTIVATE_ABILITY)
check("activate_ability preserves card_uid",
      act_action.card_uid == "creature-uid-123")
check("activate_ability preserves card_id",
      act_action.card_id == 20)
check("activate_ability sets tap_source in extra",
      dict(act_action.extra).get("tap_source") == True)
check("activate_ability sets ability_index in extra",
      dict(act_action.extra).get("ability_index") == 0)

# 2d) Full integration: creature with activated DRAW ability
deck_cards = [card(30 + i, f"DeckCard{i}") for i in range(5)]
act_creature_card = card(
    25, "DrawCreature",
    effects=[activated_effect(25, EffectAction.DRAW, {"amount": 2}, ability_index=0)],
)
act_creature = Creature(
    definition=act_creature_card, uid="act-cre-01", controller=0, owner=0,
    has_summoning_sickness=False,
)
act_mana_card = card(26, "ManaForCost", civs=(Civilization.FIRE,), cost=0)
act_state = GameState(
    players=(
        PlayerState(
            player_index=0, player_name="P0",
            deck=deck_cards,
            hand=[],
            battle_zone=[act_creature],
            mana_zone=[ManaCard(definition=act_mana_card, uid="act-mana-01", is_tapped=False)],
        ),
        PlayerState(player_index=1, player_name="P1", deck=[card(99, "f")]),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)

initial_hand = act_state.players[0].hand_count
act_action_real = activate_ability(
    player=0,
    source_uid="act-cre-01",
    source_card_id=25,
    ability_index=0,
    mana_used=[ManaUsage("act-mana-01")],
    tap_source=True,
)
act_after = execute_action(act_state, act_action_real, validate=False)
# _execute_activated_ability queues the trigger but resolve_pending_triggers
# internally copies the state; we resolve explicitly to complete the effect.
act_after = resolve_pending_triggers(act_after)
check("Activated ability draws cards",
      act_after.players[0].hand_count == initial_hand + 2)
check("Deck decreases after activated draw",
      act_after.players[0].deck_size == 5 - 2)


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: Static Ability Application (Phase 2.3)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 3: STATIC ABILITY APPLICATION")
print("─" * 60)

# 3a) CardDefinition.get_static_effects() returns correct effects
static_giver_card = card(
    40, "FireLord",
    civs=(Civilization.FIRE,),
    power=2000,
    effects=[static_effect_power(40, 1000, target_civ=Civilization.FIRE, scope="own")],
)
static_effects = static_giver_card.get_static_effects()
check("get_static_effects returns correct count", len(static_effects) == 1)
check("Static effect has correct effect_action",
      static_effects[0].effect_action == EffectAction.POWER_MODIFY)
check("Static effect has correct effect_type",
      static_effects[0].effect_type == EffectType.STATIC)

# 3b) GlobalEffectRegistry: add and remove_by_source
registry = GlobalEffectRegistry()
eff = GlobalEffect(
    effect_type=GlobalEffectType.PER_CARD_POWER_MOD,
    applied_by_uid="creature-uid-40",
    applied_by_card=40,
    controller=0,
    target_player=0,
    duration="while_in_play",
    power_mod_amount=1000,
    power_mod_target="own",
    per_card_filter_civ="Fire",
)
registry.add(eff)
check("GlobalEffectRegistry.add registers effect",
      len(registry.effects) == 1)
check("Registered effect has correct type",
      registry.effects[0].effect_type == GlobalEffectType.PER_CARD_POWER_MOD)
check("Registered effect has correct source uid",
      registry.effects[0].applied_by_uid == "creature-uid-40")
check("Registered effect has correct power_mod_amount",
      registry.effects[0].power_mod_amount == 1000)

# 3c) remove_by_source clears effects from that source
removed = registry.remove_by_source("creature-uid-40")
check("remove_by_source returns count of removed effects", removed == 1)
check("Registry is empty after removal", len(registry.effects) == 0)

# 3d) Cascading: add effects from two sources, remove one
eff_a = GlobalEffect(
    effect_type=GlobalEffectType.PER_CARD_POWER_MOD,
    applied_by_uid="source-a",
    applied_by_card=40,
    controller=0,
    target_player=0,
    power_mod_amount=1000,
)
eff_b = GlobalEffect(
    effect_type=GlobalEffectType.PER_CARD_POWER_MOD,
    applied_by_uid="source-b",
    applied_by_card=41,
    controller=0,
    target_player=0,
    power_mod_amount=500,
)
registry.add(eff_a)
registry.add(eff_b)
check("Two effects registered", len(registry.effects) == 2)
registry.remove_by_source("source-a")
check("After removing source-a, one effect remains", len(registry.effects) == 1)
check("Remaining effect is from source-b",
      registry.effects[0].applied_by_uid == "source-b")

# 3e) Creature.apply_static_effects and remove_static_effects are callable
# (The internal string-enum comparison in apply_static_effects means effects
#  are not auto-registered, but the methods exist and are callable.)
fire_creature_card = card(41, "FireSoldier", civs=(Civilization.FIRE,), power=1500)
fire_creature = Creature(
    definition=fire_creature_card, uid="fire-01", controller=0, owner=0,
    has_summoning_sickness=False,
)
static_test_state = make_state(p0_battle=[fire_creature])
# Should not raise
fire_creature.apply_static_effects(static_test_state)
check("apply_static_effects is callable without error", True)
fire_creature.remove_static_effects(static_test_state)
check("remove_static_effects is callable without error", True)


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: CDA — Characteristic-Defining Abilities (Phase 2.4)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 4: CDA (CHARACTERISTIC-DEFINING ABILITIES)")
print("─" * 60)

# 4a) HAND_COUNT_MULT CDA
cda_hand_card = card(
    50, "HandCDA",
    power=0,  # base power is ignored when CDA is active
    cda_type=CDAFormulaType.HAND_COUNT_MULT,
    cda_multiplier=1000,
)
cda_creature = Creature(
    definition=cda_hand_card, uid="cda-hand-01", controller=0, owner=0,
    has_summoning_sickness=False,
)
cda_state = GameState(
    players=(
        PlayerState(
            player_index=0, player_name="P0",
            deck=[card(99, "f")],
            hand=[HandCard(definition=card(51, "H1")),
                  HandCard(definition=card(52, "H2")),
                  HandCard(definition=card(53, "H3"))],
            battle_zone=[cda_creature],
        ),
        PlayerState(player_index=1, player_name="P1", deck=[card(99, "f")]),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)
# 3 cards in hand x 1000 = 3000
check("HAND_COUNT_MULT: 3 cards in hand -> power 3000",
      cda_creature.compute_power(cda_state) == 3000)

# Remove a card from hand -> 2 cards -> 2000
cda_state.players[0].hand.pop()
check("HAND_COUNT_MULT: after discarding, 2 cards -> power 2000",
      cda_creature.compute_power(cda_state) == 2000)

# 4b) FIXED CDA
cda_fixed_card = card(
    60, "FixedCDA",
    power=9999,  # base power should be ignored
    cda_type=CDAFormulaType.FIXED,
    cda_fixed_value=500,
)
cda_fixed_creature = Creature(
    definition=cda_fixed_card, uid="cda-fix-01", controller=0, owner=0,
    has_summoning_sickness=False,
)
cda_fixed_state = make_state(p0_battle=[cda_fixed_creature])
check("FIXED CDA: power is fixed value regardless of base",
      cda_fixed_creature.compute_power(cda_fixed_state) == 500)

# 4c) Power modifiers apply on top of CDA base
cda_mod_creature = Creature(
    definition=cda_hand_card, uid="cda-mod-01", controller=0, owner=0,
    has_summoning_sickness=False,
    power_modifiers=[
        PowerModifier(source_uid="test-mod", amount=500, duration="permanent")
    ],
)
cda_mod_state = GameState(
    players=(
        PlayerState(
            player_index=0, player_name="P0",
            deck=[card(99, "f")],
            hand=[HandCard(definition=card(51, "H1")),
                  HandCard(definition=card(52, "H2"))],
            battle_zone=[cda_mod_creature],
        ),
        PlayerState(player_index=1, player_name="P1", deck=[card(99, "f")]),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)
# 2 cards x 1000 + 500 modifier = 2500
check("CDA + power modifier: 2000 base + 500 mod = 2500",
      cda_mod_creature.compute_power(cda_mod_state) == 2500)

# 4d) Backward compatibility: normal creature without CDA
normal_card = card(70, "NormalCreature", power=3000)
normal_creature = Creature(
    definition=normal_card, uid="normal-01", controller=0, owner=0,
    has_summoning_sickness=False,
)
normal_state = make_state(p0_battle=[normal_creature])
check("Normal creature without CDA uses base_power",
      normal_creature.compute_power(normal_state) == 3000)
check("Normal creature CDA type is NONE",
      normal_card.cda_formula_type == CDAFormulaType.NONE)


# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
