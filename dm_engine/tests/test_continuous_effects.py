"""
tests/test_continuous_effects.py — coverage for Phase 4 continuous effect layer system.

Sections:
  1. Layer Enum & LayeredEffect
  2. LayerEffectRegistry
  3. GlobalEffect Timestamp
  4. GameState.layer_effects
  5. compute_power() Integration
  6. get_granted_keywords() Integration
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.cards import CardDefinition
from core.enums import (
    CDAFormulaType,
    CardSubtype,
    CardType,
    Civilization,
    GlobalEffectType,
    Keyword,
    Phase,
)
from core.global_effects import GlobalEffect, GlobalEffectRegistry
from core.player_state import PlayerState
from core.state import GameState, TurnInfo
from core.zones import Creature, ShieldCard
from engine.layers import Layer, LayerEffectRegistry, LayeredEffect

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))
    return ok


def card_def(cid, name, power=1000, card_type=CardType.CREATURE,
             civs=(Civilization.FIRE,), races=frozenset({"Human"}),
             cda_type=CDAFormulaType.NONE, cda_multiplier=0,
             cda_fixed_value=0, cda_zone="", cda_filter_civ=None):
    return CardDefinition(
        id=cid,
        slug=name.lower().replace(" ", "_"),
        name=name,
        cost=3,
        power=power if card_type == CardType.CREATURE else None,
        card_type=card_type,
        card_subtype=CardSubtype.NONE,
        civilizations=frozenset(civs),
        races=races,
        keywords=frozenset(),
        effects=tuple(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
        cda_formula_type=cda_type,
        cda_multiplier=cda_multiplier,
        cda_fixed_value=cda_fixed_value,
        cda_zone=cda_zone,
        cda_filter_civ=cda_filter_civ,
    )


def make_creature(card_definition, controller=0):
    c = Creature(definition=card_definition, controller=controller, owner=controller)
    c.has_summoning_sickness = False
    return c


def make_test_state(p0_battle=None, p1_battle=None, turn=2, phase=Phase.MAIN):
    filler = card_def(99, "DeckFiller")
    return GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0", deck=[filler],
                battle_zone=p0_battle or [],
            ),
            PlayerState(
                player_index=1, player_name="P1", deck=[filler],
                battle_zone=p1_battle or [],
            ),
        ),
        turn_info=TurnInfo(turn_number=turn, active_player=0, phase=phase),
    )


def add_global_effect(state, effect_type, applied_by_uid="src_test",
                      applied_by_card=900, controller=0, target_player=None,
                      power_mod_amount=0, power_mod_target=None,
                      grant_keyword=None, grant_to_race=None,
                      grant_to_civ=None, grant_to_controller=None,
                      per_card_filter_civ=None, per_card_filter_race=None,
                      per_card_filter_self=True):
    eff = GlobalEffect(
        effect_type=effect_type,
        applied_by_uid=applied_by_uid,
        applied_by_card=applied_by_card,
        controller=controller,
        target_player=target_player,
        power_mod_amount=power_mod_amount,
        power_mod_target=power_mod_target,
        grant_keyword=grant_keyword,
        grant_to_race=grant_to_race,
        grant_to_civ=grant_to_civ,
        grant_to_controller=grant_to_controller,
        per_card_filter_civ=per_card_filter_civ,
        per_card_filter_race=per_card_filter_race,
        per_card_filter_self=per_card_filter_self,
    )
    state.global_effects.add(eff)
    return eff


# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
print("  DM ENGINE — CONTINUOUS EFFECTS TESTS (Phase 4)")
print("═" * 60)

# ─────────────────────────────────────────────────────────────────────────────
# Section 1: Layer Enum & LayeredEffect
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 1: LAYER ENUM & LAYEREDEFFECT")
print("─" * 60)

# 1a) Layer has 7 values
check("Layer has 7 values", len(Layer) == 7)
check("Layer.COPY = 1", Layer.COPY.value == 1)
check("Layer.CONTROL = 2", Layer.CONTROL.value == 2)
check("Layer.TEXT = 3", Layer.TEXT.value == 3)
check("Layer.TYPE = 4", Layer.TYPE.value == 4)
check("Layer.COLOR = 5", Layer.COLOR.value == 5)
check("Layer.ABILITY = 6", Layer.ABILITY.value == 6)
check("Layer.POWER_TOUGHNESS = 7", Layer.POWER_TOUGHNESS.value == 7)

# 1b) LayeredEffect wraps a GlobalEffect with layer + timestamp
sample_eff = GlobalEffect(
    effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
    applied_by_uid="uid1", applied_by_card=1, controller=0,
    target_player=None,
    power_mod_amount=2000,
)
le = LayeredEffect(layer=Layer.POWER_TOUGHNESS, timestamp=1, effect=sample_eff)
check("LayeredEffect.layer is POWER_TOUGHNESS", le.layer == Layer.POWER_TOUGHNESS)
check("LayeredEffect.timestamp == 1", le.timestamp == 1)
check("LayeredEffect.effect is GlobalEffect", isinstance(le.effect, GlobalEffect))
check("LayeredEffect.effect.power_mod_amount == 2000", le.effect.power_mod_amount == 2000)

# 1c) LayeredEffect stores depends_on as Optional[str]
le_dep = LayeredEffect(layer=Layer.ABILITY, timestamp=2, effect=sample_eff, depends_on="some_source")
check("LayeredEffect with depends_on='some_source'", le_dep.depends_on == "some_source")
check("LayeredEffect without depends_on defaults to None", le.depends_on is None)


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: LayerEffectRegistry
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 2: LAYEREFFECTREGISTRY")
print("─" * 60)

# 2a) add() creates LayeredEffect with correct layer and auto-incremented timestamp
reg = LayerEffectRegistry()
e1 = GlobalEffect(
    effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
    applied_by_uid="s1", applied_by_card=1, controller=0,
    target_player=None,
    power_mod_amount=1000,
)
le1 = reg.add(e1, Layer.POWER_TOUGHNESS)
check("add() returns LayeredEffect", isinstance(le1, LayeredEffect))
check("add() sets correct layer", le1.layer == Layer.POWER_TOUGHNESS)
check("add() auto-increments timestamp (first = 1)", le1.timestamp == 1)

# 2b) add() with depends_on stores the dependency key
e2 = GlobalEffect(
    effect_type=GlobalEffectType.GRANT_KEYWORD_ALL,
    applied_by_uid="s2", applied_by_card=2, controller=0,
    target_player=None,
    grant_keyword="blocker",
)
le2 = reg.add(e2, Layer.ABILITY, depends_on="source_card_uid")
check("add() with depends_on stores key", le2.depends_on == "source_card_uid")

# 2c) get_effects_in_order() returns effects sorted by layer ASC, then timestamp DESC within layer
e3 = GlobalEffect(
    effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
    applied_by_uid="s3", applied_by_card=3, controller=0,
    target_player=None,
    power_mod_amount=3000,
)
le3 = reg.add(e3, Layer.POWER_TOUGHNESS)  # same layer, later timestamp
ordered = reg.get_effects_in_order()
check("get_effects_in_order returns 3 effects", len(ordered) == 3)
# ABILITY (layer 6) should come before POWER_TOUGHNESS (layer 7)
check("ABILITY layer before POWER_TOUGHNESS",
      ordered[0].layer == Layer.ABILITY)
# Within POWER_TOUGHNESS, higher timestamp (most recent) first
check("POWER_TOUGHNESS: most recent first",
      ordered[1].timestamp == 3 and ordered[2].timestamp == 1)

# 2d) get_effects_for_layer(Layer.POWER_TOUGHNESS) returns only PTS effects
pts_effects = reg.get_effects_for_layer(Layer.POWER_TOUGHNESS)
check("get_effects_for_layer(PTS) returns 2 effects", len(pts_effects) == 2)
check("All PTS effects are POWER_TOUGHNESS",
      all(e.layer == Layer.POWER_TOUGHNESS for e in pts_effects))

# 2e) get_layer_power_modifiers(player, controller) returns matching power mod effects
reg2 = LayerEffectRegistry()
pwr_eff = GlobalEffect(
    effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
    applied_by_uid="s_pwr", applied_by_card=10, controller=0,
    target_player=None,
    power_mod_amount=2000, power_mod_target="own",
)
reg2.add(pwr_eff, Layer.POWER_TOUGHNESS)
modifiers = reg2.get_layer_power_modifiers(player=0, controller=0)
check("get_layer_power_modifiers matches controller=0", len(modifiers) == 1)
modifiers_none = reg2.get_layer_power_modifiers(player=0, controller=1)
check("get_layer_power_modifiers excludes wrong controller", len(modifiers_none) == 0)

# 2f) get_layer_keywords(player) returns keyword-grant effects from ABILITY layer
reg3 = LayerEffectRegistry()
kw_eff = GlobalEffect(
    effect_type=GlobalEffectType.GRANT_KEYWORD_ALL,
    applied_by_uid="s_kw", applied_by_card=20, controller=0,
    target_player=None,
    grant_keyword="blocker",
)
reg3.add(kw_eff, Layer.ABILITY)
keywords = reg3.get_layer_keywords(player=0)
check("get_layer_keywords returns 1 ABILITY keyword effect", len(keywords) == 1)
check("get_layer_keywords returns correct effect",
      keywords[0].effect.grant_keyword == "blocker")

# 2g) remove_by_source(uid) removes all effects from that source, returns count
reg4 = LayerEffectRegistry()
src_eff1 = GlobalEffect(
    effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
    applied_by_uid="rm_src", applied_by_card=30, controller=0,
    target_player=None,
    power_mod_amount=1000,
)
src_eff2 = GlobalEffect(
    effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
    applied_by_uid="rm_src", applied_by_card=30, controller=0,
    target_player=None,
    power_mod_amount=2000,
)
other_eff = GlobalEffect(
    effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
    applied_by_uid="other_src", applied_by_card=31, controller=0,
    target_player=None,
    power_mod_amount=500,
)
reg4.add(src_eff1, Layer.POWER_TOUGHNESS)
reg4.add(src_eff2, Layer.POWER_TOUGHNESS)
reg4.add(other_eff, Layer.POWER_TOUGHNESS)
removed = reg4.remove_by_source("rm_src")
check("remove_by_source removes 2 effects", removed == 2)
check("remove_by_source leaves 1 effect", len(reg4.get_effects_in_order()) == 1)
check("Remaining effect is from other_src",
      reg4.get_effects_in_order()[0].effect.applied_by_uid == "other_src")

# 2h) reset() clears all effects
reg4.reset()
check("reset() clears all effects", len(reg4.get_effects_in_order()) == 0)
check("reset() resets timestamp counter", reg4._timestamp_counter == 0)


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: GlobalEffect Timestamp
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 3: GLOBALEFFECT TIMESTAMP")
print("─" * 60)

# 3a) GlobalEffect.timestamp defaults to 0
ge = GlobalEffect(
    effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
    applied_by_uid="ts1", applied_by_card=1, controller=0,
    target_player=None,
)
check("GlobalEffect.timestamp defaults to 0", ge.timestamp == 0)

# 3b) GlobalEffectRegistry.add() auto-increments timestamp
greg = GlobalEffectRegistry()
ge1 = GlobalEffect(
    effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
    applied_by_uid="ts2", applied_by_card=2, controller=0,
    target_player=None,
)
greg.add(ge1)
check("First add() sets timestamp = 1", ge1.timestamp == 1)

# 3c) Multiple adds get sequential timestamps
ge2 = GlobalEffect(
    effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
    applied_by_uid="ts3", applied_by_card=3, controller=0,
    target_player=None,
)
ge3 = GlobalEffect(
    effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
    applied_by_uid="ts4", applied_by_card=4, controller=0,
    target_player=None,
)
greg.add(ge2)
greg.add(ge3)
check("Second add() sets timestamp = 2", ge2.timestamp == 2)
check("Third add() sets timestamp = 3", ge3.timestamp == 3)


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: GameState.layer_effects
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 4: GAMESTATE.LAYER_EFFECTS")
print("─" * 60)

# 4a) GameState has layer_effects attribute of type LayerEffectRegistry
state = make_test_state()
check("GameState has layer_effects attribute",
      hasattr(state, "layer_effects"))
check("layer_effects is LayerEffectRegistry",
      isinstance(state.layer_effects, LayerEffectRegistry))

# 4b) layer_effects is independent from global_effects and replacement_effects
check("layer_effects is not same object as global_effects",
      state.layer_effects is not state.global_effects)
check("layer_effects is not same object as replacement_effects",
      state.layer_effects is not state.replacement_effects)
# Verify they work independently
state.global_effects.add(GlobalEffect(
    effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
    applied_by_uid="indep1", applied_by_card=1, controller=0,
    target_player=None,
    power_mod_amount=1000,
))
state.layer_effects.add(GlobalEffect(
    effect_type=GlobalEffectType.ALL_CREATURES_POWER_MOD,
    applied_by_uid="indep2", applied_by_card=2, controller=0,
    target_player=None,
    power_mod_amount=2000,
), Layer.POWER_TOUGHNESS)
check("global_effects has 1 effect", len(state.global_effects.effects) == 1)
check("layer_effects has 1 effect", len(state.layer_effects.get_effects_in_order()) == 1)
check("global_effects effect has correct uid",
      state.global_effects.effects[0].applied_by_uid == "indep1")
check("layer_effects effect has correct uid",
      state.layer_effects.get_effects_in_order()[0].effect.applied_by_uid == "indep2")


# ─────────────────────────────────────────────────────────────────────────────
# Section 5: compute_power() Integration
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 5: COMPUTE_POWER() INTEGRATION")
print("─" * 60)

# 5a) Standard creature power = base_power + global power bonus
state5a = make_test_state()
card_5a = card_def(501, "Standard", power=3000)
cre_5a = make_creature(card_5a, controller=0)
state5a.players[0].battle_zone.append(cre_5a)
add_global_effect(state5a, GlobalEffectType.ALL_CREATURES_POWER_MOD,
                  applied_by_uid="bonus_a", applied_by_card=550,
                  controller=0, power_mod_amount=2000,
                  power_mod_target="own")
power_5a = cre_5a.compute_power(state5a)
check("Standard creature: base 3000 + global 2000 = 5000",
      power_5a == 5000, f"got {power_5a}")

# 5b) CDA creature power = CDA base + global power bonus
state5b = make_test_state()
card_5b = card_def(502, "CDA_Creature", power=0,
                    cda_type=CDAFormulaType.FIXED, cda_fixed_value=5000)
cre_5b = make_creature(card_5b, controller=0)
state5b.players[0].battle_zone.append(cre_5b)
add_global_effect(state5b, GlobalEffectType.ALL_CREATURES_POWER_MOD,
                  applied_by_uid="bonus_b", applied_by_card=551,
                  controller=0, power_mod_amount=1000,
                  power_mod_target="own")
power_5b = cre_5b.compute_power(state5b)
check("CDA creature: CDA base 5000 + global 1000 = 6000",
      power_5b == 6000, f"got {power_5b}")

# 5c) ALL_CREATURES_POWER_FIX overrides CDA and standard power
state5c = make_test_state()
card_5c = card_def(503, "FixedCreature", power=3000)
cre_5c = make_creature(card_5c, controller=0)
state5c.players[0].battle_zone.append(cre_5c)
add_global_effect(state5c, GlobalEffectType.ALL_CREATURES_POWER_FIX,
                  applied_by_uid="fix_c", applied_by_card=552,
                  controller=0, target_player=0,
                  power_mod_amount=1000)
power_5c = cre_5c.compute_power(state5c)
check("ALL_CREATURES_POWER_FIX overrides to 1000",
      power_5c == 1000, f"got {power_5c}")

# 5d) Per-card power bonus stacks with global power bonus
state5d = make_test_state()
card_5d = card_def(504, "FireCreature", power=2000,
                    civs=(Civilization.FIRE,), races=frozenset({"Dragon"}))
cre_5d = make_creature(card_5d, controller=0)
state5d.players[0].battle_zone.append(cre_5d)
# Global bonus: all creatures +1000
add_global_effect(state5d, GlobalEffectType.ALL_CREATURES_POWER_MOD,
                  applied_by_uid="global_d", applied_by_card=553,
                  controller=0, power_mod_amount=1000,
                  power_mod_target="own")
# Per-card bonus: Fire creatures +500
add_global_effect(state5d, GlobalEffectType.PER_CARD_POWER_MOD,
                  applied_by_uid="percard_d", applied_by_card=554,
                  controller=0, power_mod_amount=500,
                  per_card_filter_civ="Fire", per_card_filter_self=True)
power_5d = cre_5d.compute_power(state5d)
check("Per-card + global: 2000 + 1000 + 500 = 3500",
      power_5d == 3500, f"got {power_5d}")

# 5e) No global effects = base_power unchanged (no regression)
state5e = make_test_state()
card_5e = card_def(505, "PlainCreature", power=4000)
cre_5e = make_creature(card_5e, controller=0)
state5e.players[0].battle_zone.append(cre_5e)
power_5e = cre_5e.compute_power(state5e)
check("No global effects: base_power unchanged at 4000",
      power_5e == 4000, f"got {power_5e}")


# ─────────────────────────────────────────────────────────────────────────────
# Section 6: get_granted_keywords() Integration
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 6: GET_GRANTED_KEYWORDS() INTEGRATION")
print("─" * 60)

# 6a) Returns keywords from GRANT_KEYWORD_ALL effects
reg6 = GlobalEffectRegistry()
reg6.add(GlobalEffect(
    effect_type=GlobalEffectType.GRANT_KEYWORD_ALL,
    applied_by_uid="kw_a", applied_by_card=600, controller=0,
    target_player=None,
    grant_keyword="blocker", grant_to_controller=0,
))
kw_blocker = reg6.get_granted_keywords(controller=0)
check("GRANT_KEYWORD_ALL grants 'blocker' to controller 0",
      "blocker" in kw_blocker)
kw_none = reg6.get_granted_keywords(controller=1)
check("GRANT_KEYWORD_ALL does not grant to controller 1",
      "blocker" not in kw_none)

# 6b) Also returns keywords from PER_CARD_KEYWORD_GRANT effects
reg6b = GlobalEffectRegistry()
reg6b.add(GlobalEffect(
    effect_type=GlobalEffectType.PER_CARD_KEYWORD_GRANT,
    applied_by_uid="kw_b", applied_by_card=601, controller=0,
    target_player=None,
    grant_keyword="speed_attacker", grant_to_controller=0,
))
kw_speed = reg6b.get_granted_keywords(controller=0)
check("PER_CARD_KEYWORD_GRANT grants 'speed_attacker'",
      "speed_attacker" in kw_speed)

# 6c) PER_CARD_KEYWORD_GRANT respects grant_to_controller filter
reg6c = GlobalEffectRegistry()
reg6c.add(GlobalEffect(
    effect_type=GlobalEffectType.PER_CARD_KEYWORD_GRANT,
    applied_by_uid="kw_c", applied_by_card=602, controller=0,
    target_player=None,
    grant_keyword="double_breaker", grant_to_controller=0,
))
kw_c0 = reg6c.get_granted_keywords(controller=0)
kw_c1 = reg6c.get_granted_keywords(controller=1)
check("grant_to_controller=0 grants to controller 0",
      "double_breaker" in kw_c0)
check("grant_to_controller=0 does NOT grant to controller 1",
      "double_breaker" not in kw_c1)

# 6d) PER_CARD_KEYWORD_GRANT respects grant_to_race filter
reg6d = GlobalEffectRegistry()
reg6d.add(GlobalEffect(
    effect_type=GlobalEffectType.PER_CARD_KEYWORD_GRANT,
    applied_by_uid="kw_d", applied_by_card=603, controller=0,
    target_player=None,
    grant_keyword="blocker", grant_to_controller=0,
    grant_to_race="Dragon",
))
kw_dragon = reg6d.get_granted_keywords(controller=0, race="Dragon")
kw_human = reg6d.get_granted_keywords(controller=0, race="Human")
check("grant_to_race='Dragon' matches Dragon race",
      "blocker" in kw_dragon)
check("grant_to_race='Dragon' does NOT match Human race",
      "blocker" not in kw_human)

# 6e) PER_CARD_KEYWORD_GRANT respects grant_to_civ filter
reg6e = GlobalEffectRegistry()
reg6e.add(GlobalEffect(
    effect_type=GlobalEffectType.PER_CARD_KEYWORD_GRANT,
    applied_by_uid="kw_e", applied_by_card=604, controller=0,
    target_player=None,
    grant_keyword="cannot_be_blocked", grant_to_controller=0,
    grant_to_civ="Fire",
))
kw_fire = reg6e.get_granted_keywords(controller=0, civ="Fire")
kw_water = reg6e.get_granted_keywords(controller=0, civ="Water")
check("grant_to_civ='Fire' matches Fire civ",
      "cannot_be_blocked" in kw_fire)
check("grant_to_civ='Fire' does NOT match Water civ",
      "cannot_be_blocked" not in kw_water)


# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
total = len(results)
passed = sum(1 for _, ok, _ in results if ok)
failed = total - passed
print("\n" + "═" * 60)
print(f"  RESULTS: {passed}/{total} passed, {failed} failed")
print("═" * 60)
if failed:
    print("\n  FAILURES:")
    for name, ok, detail in results:
        if not ok:
            print(f"    ❌ {name}" + (f" — {detail}" if detail else ""))
    sys.exit(1)
else:
    print("\n  All tests passed! ✅")
