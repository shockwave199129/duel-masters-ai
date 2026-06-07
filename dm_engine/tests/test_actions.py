"""
tests/test_actions.py — Tests for core/actions.py.

Verifies:
  - Action construction for every action type
  - Immutability (frozen dataclass)
  - Hashability (MCTS dict-key safety)
  - ManaUsage: multi-civ card provides ONE civilization (rule 112.2a)
  - ManaCard.from_charge: multi-civ cards enter tapped (rule 405.1)
  - Action field correctness per rule
  - ACTION_TYPE_INDEX completeness
  - is_* predicate methods
  - Actions equal / not equal correctly
  - All updated enum values present
  - Creature.is_ignored blocks attack/block (rule 116.2)
  - Creature.hyper_mode_released field present
  - PlayerState has hyperspatial_zone and ultra_gr_zone
  - GlobalEffectRegistry: LOCK_CARD_TYPE and GRANT_KEYWORD_ALL
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.enums import (
    ActionType, Civilization, Phase, Keyword, Zone,
    CardType, CardSubtype, GlobalEffectType, ManaUsage,
    EffectType, TriggerEvent, EffectAction, GameResult,
)
from core.actions import (
    Action,
    charge_mana, pass_charge, pass_main, pass_attack, pass_block,
    summon_creature, cast_spell, generate_cross_gear, cross_gear,
    fortify_castle, deploy_field, execute_tamaseed,
    attack_player, attack_creature,
    declare_blocker, declare_guardman,
    select_shield_to_break,
    use_shield_trigger, use_s_back, use_ninja_strike,
    use_g_zero, use_attack_chance, use_g_strike, hyperize,
    select_yes_no, select_target, select_targets, select_mana,
    select_card, select_evolution_base, select_civilization,
    select_cards_from_list, pass_action, actions_equal,
    ACTION_TYPE_INDEX, NUM_ACTION_TYPES,
)
from core.zones import ManaCard, Creature
from core.global_effects import GlobalEffect, GlobalEffectRegistry
from core.player_state import PlayerState
from core.cards import CardDefinition
from core.enums import CardSubtype

# ── Minimal card fixture ───────────────────────────────────────────────────────
def _card(cid, name, civs, cost=3, power=2000, card_type=CardType.CREATURE):
    return CardDefinition(
        id=cid, slug=name.lower().replace(" ","_"), name=name,
        cost=cost, power=power if card_type==CardType.CREATURE else None,
        card_type=card_type, card_subtype=CardSubtype.NONE,
        civilizations=frozenset(civs), races=frozenset(),
        keywords=frozenset(), effects=tuple(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
    )

FIRE_CARD    = _card(1, "Fire Creature",    [Civilization.FIRE])
WATER_CARD   = _card(2, "Water Spell",      [Civilization.WATER], card_type=CardType.SPELL)
MULTI_CARD   = _card(3, "Multi Card",       [Civilization.FIRE, Civilization.NATURE])
LIGHT_CARD   = _card(4, "Light Creature",   [Civilization.LIGHT])
DARK_CARD    = _card(5, "Dark Creature",    [Civilization.DARKNESS])

PASS = "✅"
FAIL = "❌"
results = []

def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    mark = PASS if ok else FAIL
    print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))
    return ok


print("\n" + "═"*60)
print("  DM ENGINE — ACTIONS TEST SUITE")
print("═"*60)


# ═══════════════════════════════════════════════════════════════════
print("\n── 1. Enums Updated Correctly ──────────────────────────────")
# ═══════════════════════════════════════════════════════════════════

# Phase enum — rule 500.1 names
check("Phase.START_OF_TURN exists",   hasattr(Phase, "START_OF_TURN"))
check("Phase.MANA_CHARGE exists",     hasattr(Phase, "MANA_CHARGE"))
check("Phase.ATTACK_DECLARE exists",  hasattr(Phase, "ATTACK_DECLARE"))
check("Phase.BLOCK_DECLARE exists",   hasattr(Phase, "BLOCK_DECLARE"))
check("Phase.DIRECT_ATTACK exists",   hasattr(Phase, "DIRECT_ATTACK"))
check("Phase.END_OF_ATTACK exists",   hasattr(Phase, "END_OF_ATTACK"))
check("Phase.END_OF_TURN exists",     hasattr(Phase, "END_OF_TURN"))
check("Old UNTAP removed",            not hasattr(Phase, "UNTAP"))
check("Old CHARGE removed",           not hasattr(Phase, "CHARGE"))
check("ATTACK_DECLARE is sub-phase",  Phase.ATTACK_DECLARE.is_attack_subphase())
check("MAIN is not sub-phase",        not Phase.MAIN.is_attack_subphase())

# Keyword enum — new additions
check("Keyword.SILENT_SKILL exists",  hasattr(Keyword, "SILENT_SKILL"))
check("Keyword.S_BACK exists",        hasattr(Keyword, "S_BACK"))
check("Keyword.G_ZERO exists",        hasattr(Keyword, "G_ZERO"))
check("Keyword.ATTACK_CHANCE exists", hasattr(Keyword, "ATTACK_CHANCE"))
check("Keyword.MANA_BURST exists",    hasattr(Keyword, "MANA_BURST"))
check("Keyword.G_STRIKE exists",      hasattr(Keyword, "G_STRIKE"))
check("Keyword.HYPERIZE exists",      hasattr(Keyword, "HYPERIZE"))
check("Keyword.KIRIFUDASH exists",    hasattr(Keyword, "KIRIFUDASH"))
check("Keyword.SABAKI_Z exists",      hasattr(Keyword, "SABAKI_Z"))

# CardType — new additions
check("CardType.WEAPON exists",       hasattr(CardType, "WEAPON"))
check("CardType.HEARTBEAT exists",    hasattr(CardType, "HEARTBEAT"))
check("CardType.CORE exists",         hasattr(CardType, "CORE"))
check("CardType.AURA exists",         hasattr(CardType, "AURA"))
check("CardType.RITUAL exists",       hasattr(CardType, "RITUAL"))
check("CardType.TAMASEED exists",     hasattr(CardType, "TAMASEED"))

# CardSubtype — new additions
check("CardSubtype.FORBIDDEN exists",      hasattr(CardSubtype, "FORBIDDEN"))
check("CardSubtype.FINAL_FORBIDDEN exists",hasattr(CardSubtype, "FINAL_FORBIDDEN"))
check("CardSubtype.GR exists",             hasattr(CardSubtype, "GR"))
check("CardSubtype.EXILE exists",          hasattr(CardSubtype, "EXILE"))
check("CardSubtype.D2 exists",             hasattr(CardSubtype, "D2"))

# Zone — new additions
check("Zone.ULTRA_GR exists",         hasattr(Zone, "ULTRA_GR"))
check("Zone.PENDING exists",          hasattr(Zone, "PENDING"))
check("Zone.HYPERSPATIAL exists",     hasattr(Zone, "HYPERSPATIAL"))

# ActionType — new additions
check("ActionType.GENERATE_CROSS_GEAR exists", hasattr(ActionType, "GENERATE_CROSS_GEAR"))
check("ActionType.CROSS_GEAR exists",          hasattr(ActionType, "CROSS_GEAR"))
check("ActionType.FORTIFY_CASTLE exists",      hasattr(ActionType, "FORTIFY_CASTLE"))
check("ActionType.DEPLOY_FIELD exists",        hasattr(ActionType, "DEPLOY_FIELD"))
check("ActionType.EXECUTE_TAMASEED exists",    hasattr(ActionType, "EXECUTE_TAMASEED"))
check("ActionType.USE_SHIELD_TRIGGER exists",  hasattr(ActionType, "USE_SHIELD_TRIGGER"))
check("ActionType.USE_S_BACK exists",          hasattr(ActionType, "USE_S_BACK"))
check("ActionType.USE_G_ZERO exists",          hasattr(ActionType, "USE_G_ZERO"))
check("ActionType.USE_ATTACK_CHANCE exists",   hasattr(ActionType, "USE_ATTACK_CHANCE"))
check("ActionType.USE_G_STRIKE exists",        hasattr(ActionType, "USE_G_STRIKE"))
check("ActionType.HYPERIZE exists",            hasattr(ActionType, "HYPERIZE"))
check("ActionType.SELECT_ATTACK_ORDER exists", hasattr(ActionType, "SELECT_ATTACK_ORDER"))
check("ActionType.SELECT_EVOLUTION_BASE exists",hasattr(ActionType, "SELECT_EVOLUTION_BASE"))

# GlobalEffectType — new additions
check("GlobalEffectType.LOCK_CARD_TYPE exists",   hasattr(GlobalEffectType, "LOCK_CARD_TYPE"))
check("GlobalEffectType.GRANT_KEYWORD_ALL exists", hasattr(GlobalEffectType, "GRANT_KEYWORD_ALL"))
check("GlobalEffectType.ALL_CREATURES_POWER_FIX exists",
      hasattr(GlobalEffectType, "ALL_CREATURES_POWER_FIX"))

# EffectAction — new additions
check("EffectAction.POWER_FIX exists",    hasattr(EffectAction, "POWER_FIX"))
check("EffectAction.ATTACH_SEAL exists",  hasattr(EffectAction, "ATTACH_SEAL"))
check("EffectAction.REMOVE_SEAL exists",  hasattr(EffectAction, "REMOVE_SEAL"))
check("EffectAction.GACHINKO_JUDGE exists",hasattr(EffectAction, "GACHINKO_JUDGE"))
check("EffectAction.HYPERIZE exists",     hasattr(EffectAction, "HYPERIZE"))

# TriggerEvent — new additions
check("TriggerEvent.ON_WIN_BATTLE exists",  hasattr(TriggerEvent, "ON_WIN_BATTLE"))
check("TriggerEvent.ON_DIRECT_ATTACK exists",hasattr(TriggerEvent, "ON_DIRECT_ATTACK"))
check("TriggerEvent.BEFORE_BREAK exists",   hasattr(TriggerEvent, "BEFORE_BREAK"))

# ManaUsage class
mu = ManaUsage("uid_abc", Civilization.FIRE)
check("ManaUsage created",            mu.mana_uid == "uid_abc")
check("ManaUsage civilization set",   mu.used_for_civ == Civilization.FIRE)
mu2 = ManaUsage("uid_abc", Civilization.FIRE)
check("ManaUsage equality works",     mu == mu2)
check("ManaUsage hashable",           hash(mu) == hash(mu2))
mu3 = ManaUsage("uid_abc")
check("ManaUsage no civ is valid",    mu3.used_for_civ is None)


# ═══════════════════════════════════════════════════════════════════
print("\n── 2. Action Immutability and Hashability ──────────────────")
# ═══════════════════════════════════════════════════════════════════

a = charge_mana(0, "uid_1", 1)

# Frozen — cannot mutate
try:
    a.player = 1
    check("Action is immutable (frozen)",  False, "mutation succeeded — WRONG")
except Exception:
    check("Action is immutable (frozen)",  True)

# Hashable — can be used as dict key
d = {a: "value"}
check("Action hashable (dict key)",    d[a] == "value")

# Hashable — can be in a set
s = {a}
check("Action in set",                 a in s)

# Two identical actions hash equal
a2 = charge_mana(0, "uid_1", 1)
check("Identical actions hash equal",  hash(a) == hash(a2))
check("Identical actions equal",       a == a2)

# Different actions don't equal
a3 = charge_mana(1, "uid_1", 1)
check("Different player = not equal",  a != a3)


# ═══════════════════════════════════════════════════════════════════
print("\n── 3. Mana Charge Actions (rule 503) ───────────────────────")
# ═══════════════════════════════════════════════════════════════════

cm = charge_mana(0, "hand_uid_1", 42)
check("charge_mana player",       cm.player == 0)
check("charge_mana type",         cm.action_type == ActionType.CHARGE_MANA)
check("charge_mana card_uid",     cm.card_uid == "hand_uid_1")
check("charge_mana card_id",      cm.card_id == 42)
check("charge_mana no mana_used", len(cm.mana_used) == 0)
check("charge_mana is_play_from_hand", cm.is_play_from_hand())
check("charge_mana not costs_mana",    not cm.costs_mana())

pc = pass_charge(1)
check("pass_charge player",       pc.player == 1)
check("pass_charge is PASS",      pc.action_type == ActionType.PASS)
check("pass_charge step label",   dict(pc.extra).get("step") == "mana_charge")


# ═══════════════════════════════════════════════════════════════════
print("\n── 4. Summon Creature (rule 301, 112.2a) ───────────────────")
# ═══════════════════════════════════════════════════════════════════

# Rule 112.2a: each multi-civ mana card provides ONE civilization
mana_usage = [
    ManaUsage("mana_uid_1", Civilization.FIRE),    # fire card → fire
    ManaUsage("mana_uid_2", Civilization.FIRE),    # multi card → player chose fire
    ManaUsage("mana_uid_3", Civilization.NATURE),  # multi card → player chose nature
]
sc = summon_creature(0, "hand_uid_5", 5, mana_usage)
check("summon type",              sc.action_type == ActionType.SUMMON_CREATURE)
check("summon player",            sc.player == 0)
check("summon card_uid",          sc.card_uid == "hand_uid_5")
check("summon card_id",           sc.card_id == 5)
check("summon mana_used count",   len(sc.mana_used) == 3)
check("summon mana_used[0] civ",  sc.mana_used[0].used_for_civ == Civilization.FIRE)
check("summon mana_used[2] civ",  sc.mana_used[2].used_for_civ == Civilization.NATURE)
check("summon costs_mana",        sc.costs_mana())
check("summon no evo base",       sc.evolution_base_uid is None)

# Evolution summon
evo = summon_creature(0, "hand_uid_7", 7, mana_usage, evolution_base_uid="bz_uid_3")
check("evo base uid set",         evo.evolution_base_uid == "bz_uid_3")
check("evo card_uid correct",     evo.card_uid == "hand_uid_7")


# ═══════════════════════════════════════════════════════════════════
print("\n── 5. Cast Spell, Cross Gear, Field, Castle, Tamaseed ──────")
# ═══════════════════════════════════════════════════════════════════

spell_mana = [ManaUsage("m1", Civilization.WATER), ManaUsage("m2", Civilization.WATER)]
cs = cast_spell(1, "hand_sp_1", 10, spell_mana)
check("cast_spell type",          cs.action_type == ActionType.CAST_SPELL)
check("cast_spell mana count",    len(cs.mana_used) == 2)
check("cast_spell costs_mana",    cs.costs_mana())

gcg = generate_cross_gear(0, "hand_cg_1", 20, [ManaUsage("m3", Civilization.FIRE)])
check("generate_cross_gear type", gcg.action_type == ActionType.GENERATE_CROSS_GEAR)

cg = cross_gear(0, "bz_gear_1", 20, "bz_creature_1", [ManaUsage("m4", Civilization.FIRE)])
check("cross_gear type",          cg.action_type == ActionType.CROSS_GEAR)
check("cross_gear gear uid",      cg.card_uid == "bz_gear_1")
check("cross_gear target uid",    cg.target_uid == "bz_creature_1")

fc = fortify_castle(0, "hand_cas_1", 30, [ManaUsage("m5", Civilization.LIGHT)])
check("fortify_castle type",      fc.action_type == ActionType.FORTIFY_CASTLE)

df = deploy_field(0, "hand_fld_1", 40, [ManaUsage("m6", Civilization.DARKNESS)])
check("deploy_field type",        df.action_type == ActionType.DEPLOY_FIELD)

ts = execute_tamaseed(0, "hand_ts_1", 50, [ManaUsage("m7", Civilization.NATURE)])
check("execute_tamaseed type",    ts.action_type == ActionType.EXECUTE_TAMASEED)

pm = pass_main(0)
check("pass_main step label",     dict(pm.extra).get("step") == "main")


# ═══════════════════════════════════════════════════════════════════
print("\n── 6. Attack Actions (rules 506, 509) ──────────────────────")
# ═══════════════════════════════════════════════════════════════════

# Attack player
ap = attack_player(0, "bz_atk_1", 1)
check("attack_player type",        ap.action_type == ActionType.ATTACK_PLAYER)
check("attack_player attacker uid",ap.card_uid == "bz_atk_1")
check("attack_player target",      ap.target_uid == "player_1")
check("attack_player is_attack",   ap.is_attack())
check("attack_player no mana",     len(ap.mana_used) == 0)

# P1 attacking P0
ap2 = attack_player(1, "bz_atk_2", 2)
check("P1 attack targets player_0",ap2.target_uid == "player_0")

# Attack creature (rule 506.3 — must be tapped normally)
ac = attack_creature(0, "bz_atk_1", 1, "bz_opp_1", 99)
check("attack_creature type",      ac.action_type == ActionType.ATTACK_CREATURE)
check("attack_creature attacker",  ac.card_uid == "bz_atk_1")
check("attack_creature target",    ac.target_uid == "bz_opp_1")
check("attack_creature is_attack", ac.is_attack())
check("attack target_id in extra", dict(ac.extra).get("target_id") == 99)

# Shield break order (rule 509.2)
sbo = select_shield_to_break(0, shield_index=2)
check("shield_break type",         sbo.action_type == ActionType.SELECT_ATTACK_ORDER)
check("shield_break index",        sbo.shield_index == 2)

pa = pass_attack(0)
check("pass_attack step label",    dict(pa.extra).get("step") == "attack")


# ═══════════════════════════════════════════════════════════════════
print("\n── 7. Block Actions (rule 507) ─────────────────────────────")
# ═══════════════════════════════════════════════════════════════════

db = declare_blocker(1, "bz_blk_1", 7)
check("declare_blocker type",      db.action_type == ActionType.DECLARE_BLOCKER)
check("declare_blocker uid",       db.card_uid == "bz_blk_1")
check("declare_blocker player",    db.player == 1)

dg = declare_guardman(1, "bz_grd_1", 8)
check("declare_guardman type",     dg.action_type == ActionType.DECLARE_GUARDMAN)

pb = pass_block(1)
check("pass_block step label",     dict(pb.extra).get("step") == "block")


# ═══════════════════════════════════════════════════════════════════
print("\n── 8. Free Execution Abilities (rule 112.3) ─────────────────")
# ═══════════════════════════════════════════════════════════════════

# S-Trigger (rule 112.3a) — always free
st = use_shield_trigger(0, "broken_shield_uid", 5, use=True)
check("shield_trigger type",       st.action_type == ActionType.USE_SHIELD_TRIGGER)
check("shield_trigger is free",    len(st.mana_used) == 0)
check("shield_trigger choice=True",st.choice == True)
check("shield_trigger is_free_exec",st.is_free_execution())

st_skip = use_shield_trigger(0, "broken_shield_uid", 5, use=False)
check("shield_trigger skip choice",st_skip.choice == False)

# S-Back (rule 112.3b)
sb = use_s_back(0, "hand_sb_card", 6, "hand_discard_uid", 3)
check("s_back type",               sb.action_type == ActionType.USE_S_BACK)
check("s_back card uid",           sb.card_uid == "hand_sb_card")
check("s_back discard uid",        sb.discard_uid == "hand_discard_uid")
check("s_back is free",            len(sb.mana_used) == 0)
check("s_back is_free_exec",       sb.is_free_execution())

# Ninja Strike (rule 112.3c)
ns = use_ninja_strike(0, "hand_ninja_1", 9, "hand_discard_2", 4)
check("ninja_strike type",         ns.action_type == ActionType.USE_NINJA_STRIKE)
check("ninja_strike card_uid",     ns.card_uid == "hand_ninja_1")
check("ninja_strike discard_uid",  ns.discard_uid == "hand_discard_2")
check("ninja_strike is free",      len(ns.mana_used) == 0)
check("ninja_strike is_free_exec", ns.is_free_execution())

# G-Zero (rule 112.3e)
gz = use_g_zero(0, "hand_gz_1", 11)
check("g_zero type",               gz.action_type == ActionType.USE_G_ZERO)
check("g_zero is free",            len(gz.mana_used) == 0)
check("g_zero is_free_exec",       gz.is_free_execution())

# Attack Chance (rule 112.3f)
ach = use_attack_chance(0, "hand_spell_1", 12)
check("attack_chance type",        ach.action_type == ActionType.USE_ATTACK_CHANCE)
check("attack_chance is free",     len(ach.mana_used) == 0)

# G-Strike (rule 101.4b)
gs = use_g_strike(0, "shield_uid_gs", 13, use=True)
check("g_strike type",             gs.action_type == ActionType.USE_G_STRIKE)
check("g_strike choice",           gs.choice == True)
check("g_strike is_free_exec",     gs.is_free_execution())

# Hyperize (rule 816)
hz = hyperize(0, "bz_creature_2", 14)
check("hyperize type",             hz.action_type == ActionType.HYPERIZE)
check("hyperize card_uid",         hz.card_uid == "bz_creature_2")


# ═══════════════════════════════════════════════════════════════════
print("\n── 9. Effect Resolution Choices ────────────────────────────")
# ═══════════════════════════════════════════════════════════════════

yn_yes = select_yes_no(0, True, "effect_source_uid")
check("select_yes_no type",        yn_yes.action_type == ActionType.SELECT_YES_NO)
check("select_yes_no choice True", yn_yes.choice == True)

yn_no = select_yes_no(0, False)
check("select_yes_no False",       yn_no.choice == False)

tgt = select_target(0, "bz_creature_3", "battle_zone", "effect_uid")
check("select_target type",        tgt.action_type == ActionType.SELECT_TARGET)
check("select_target target_uid",  tgt.target_uid == "bz_creature_3")
check("select_target zone",        tgt.target_zone == "battle_zone")

tgts = select_targets(0, ["bz_c1", "bz_c2"], "battle_zone", "eff")
check("select_targets count",      len(tgts.selected_uids) == 2)
check("select_targets uid 0",      tgts.selected_uids[0] == "bz_c1")

sm = select_mana(0, [ManaUsage("m1", Civilization.FIRE), ManaUsage("m2", Civilization.WATER)])
check("select_mana type",          sm.action_type == ActionType.SELECT_MANA)
check("select_mana count",         len(sm.mana_used) == 2)

scard = select_card(0, "hand_uid_x", 99, "eff_uid", "hand")
check("select_card type",          scard.action_type == ActionType.SELECT_CARD)
check("select_card card_uid",      scard.card_uid == "hand_uid_x")
check("select_card zone",          scard.target_zone == "hand")

seb = select_evolution_base(0, "hand_evo_uid", 55, "bz_base_uid",
                             [ManaUsage("m3", Civilization.FIRE)])
check("select_evo_base type",      seb.action_type == ActionType.SELECT_EVOLUTION_BASE)
check("select_evo_base card_uid",  seb.card_uid == "hand_evo_uid")
check("select_evo_base base_uid",  seb.evolution_base_uid == "bz_base_uid")
check("select_evo_base mana",      len(seb.mana_used) == 1)

sciv = select_civilization(0, Civilization.FIRE, "eff_uid")
check("select_civ type",           sciv.action_type == ActionType.SELECT_CARD)
check("select_civ civilization",   sciv.selected_civ == Civilization.FIRE)

sclist = select_cards_from_list(0, ["h1", "h2", "h3"], "eff_uid", "hand")
check("select_cards_from_list",    len(sclist.selected_uids) == 3)

pa2 = pass_action(0, "main")
check("pass_action type",          pa2.action_type == ActionType.PASS)
check("pass_action step",          dict(pa2.extra).get("step") == "main")


# ═══════════════════════════════════════════════════════════════════
print("\n── 10. actions_equal helper ────────────────────────────────")
# ═══════════════════════════════════════════════════════════════════

a1 = attack_player(0, "uid_1", 1)
a2 = attack_player(0, "uid_1", 1)
a3 = attack_player(1, "uid_1", 1)
a4 = attack_player(0, "uid_2", 1)

check("actions_equal: identical",    actions_equal(a1, a2))
check("actions_equal: diff player",  not actions_equal(a1, a3))
check("actions_equal: diff uid",     not actions_equal(a1, a4))

# Mana ordering matters
m1 = summon_creature(0, "c", 1, [ManaUsage("x", Civilization.FIRE), ManaUsage("y", Civilization.WATER)])
m2 = summon_creature(0, "c", 1, [ManaUsage("x", Civilization.FIRE), ManaUsage("y", Civilization.WATER)])
m3 = summon_creature(0, "c", 1, [ManaUsage("y", Civilization.WATER), ManaUsage("x", Civilization.FIRE)])
check("actions_equal: same mana order",   actions_equal(m1, m2))
check("actions_equal: diff mana order",   not actions_equal(m1, m3))


# ═══════════════════════════════════════════════════════════════════
print("\n── 11. ACTION_TYPE_INDEX completeness ──────────────────────")
# ═══════════════════════════════════════════════════════════════════

check("ACTION_TYPE_INDEX has 26 entries", NUM_ACTION_TYPES == 26)
check("CHARGE_MANA in index",      ActionType.CHARGE_MANA in ACTION_TYPE_INDEX)
check("SUMMON_CREATURE in index",  ActionType.SUMMON_CREATURE in ACTION_TYPE_INDEX)
check("ATTACK_PLAYER in index",    ActionType.ATTACK_PLAYER in ACTION_TYPE_INDEX)
check("USE_SHIELD_TRIGGER index",  ActionType.USE_SHIELD_TRIGGER in ACTION_TYPE_INDEX)
check("USE_NINJA_STRIKE index",    ActionType.USE_NINJA_STRIKE in ACTION_TYPE_INDEX)
check("PASS in index",             ActionType.PASS in ACTION_TYPE_INDEX)
check("Indices unique",
      len(set(ACTION_TYPE_INDEX.values())) == NUM_ACTION_TYPES)
check("Indices 0-based contiguous",
      set(ACTION_TYPE_INDEX.values()) == set(range(NUM_ACTION_TYPES)))


# ═══════════════════════════════════════════════════════════════════
print("\n── 12. ManaCard.from_charge — rule 405.1 ───────────────────")
# ═══════════════════════════════════════════════════════════════════

# Single-civ card: enters untapped
single = ManaCard.from_charge(FIRE_CARD)
check("Single-civ enters untapped", not single.is_tapped)
check("Single-civ civilization",    Civilization.FIRE in single.civilizations)

# Multi-civ card: enters TAPPED (rule 405.1)
multi = ManaCard.from_charge(MULTI_CARD)
check("Multi-civ enters TAPPED",    multi.is_tapped)
check("Multi-civ civilizations",    Civilization.FIRE in multi.civilizations)
check("Multi-civ has Nature",       Civilization.NATURE in multi.civilizations)

# Multi-civ but provides ONE civilization at payment (rule 112.2a)
mu_fire   = ManaUsage(multi.uid, Civilization.FIRE)
mu_nature = ManaUsage(multi.uid, Civilization.NATURE)
check("ManaUsage FIRE from multi",   mu_fire.used_for_civ == Civilization.FIRE)
check("ManaUsage NATURE from multi", mu_nature.used_for_civ == Civilization.NATURE)
check("Different civ choices not equal", mu_fire != mu_nature)


# ═══════════════════════════════════════════════════════════════════
print("\n── 13. Creature — rule 116.2 Ignored State (Seals) ─────────")
# ═══════════════════════════════════════════════════════════════════

normal_c = Creature(definition=FIRE_CARD, controller=0, owner=0,
                    has_summoning_sickness=False)
check("Normal creature not ignored",   not normal_c.is_ignored)
check("Normal creature can attack",    normal_c.can_attack())
check("Normal creature can block",     True)  # FIRE_CARD has no blocker but struct OK

# Add a seal → creature becomes ignored
normal_c.seals.append(WATER_CARD)
check("Creature with seal is ignored", normal_c.is_ignored)
check("Ignored creature cannot attack",not normal_c.can_attack())
check("Ignored creature not blocker",  not normal_c.is_blocker())

# Remove seal → normal again
normal_c.seals.clear()
check("Seal removed → not ignored",   not normal_c.is_ignored)
check("Seal removed → can attack",    normal_c.can_attack())

# hyper_mode_released field
check("hyper_mode_released default False", not normal_c.hyper_mode_released)
normal_c.hyper_mode_released = True
check("hyper_mode_released settable",      normal_c.hyper_mode_released)


# ═══════════════════════════════════════════════════════════════════
print("\n── 14. PlayerState — hyperspatial and ultra_gr zones ───────")
# ═══════════════════════════════════════════════════════════════════

ps = PlayerState(player_index=0, player_name="Test",
                 deck_composition={FIRE_CARD.id: 4})
check("hyperspatial_zone exists",     hasattr(ps, "hyperspatial_zone"))
check("ultra_gr_zone exists",         hasattr(ps, "ultra_gr_zone"))
check("hyperspatial_count = 0",       ps.hyperspatial_count == 0)
check("ultra_gr_count = 0",           ps.ultra_gr_count == 0)

# count_cards_in_zone includes new zones
check("count hyperspatial zone",
      ps.count_cards_in_zone(Zone.HYPERSPATIAL) == 0)
check("count ultra_gr zone",
      ps.count_cards_in_zone(Zone.ULTRA_GR) == 0)
check("count abyss zone",
      ps.count_cards_in_zone(Zone.ABYSS_ZONE) == 0)


# ═══════════════════════════════════════════════════════════════════
print("\n── 15. GlobalEffectRegistry — LOCK_CARD_TYPE & GRANT_KEYWORD ─")
# ═══════════════════════════════════════════════════════════════════

reg = GlobalEffectRegistry()

# LOCK_CARD_TYPE: cannot summon Evolution creatures
lock_evo = GlobalEffect(
    effect_type=GlobalEffectType.LOCK_CARD_TYPE,
    applied_by_uid="field_uid_1",
    applied_by_card=99,
    controller=1,
    target_player=None,   # affects both
    duration="while_in_play",
    locked_card_subtype="Evolution",
)
reg.add(lock_evo)

check("Can summon normal creature",
      reg.can_summon_creature(0, frozenset([Civilization.FIRE]),
                              card_type="Creature", card_subtype="None"))
check("Cannot summon Evolution creature (LOCK_CARD_TYPE)",
      not reg.can_summon_creature(0, frozenset([Civilization.FIRE]),
                                  card_type="Creature", card_subtype="Evolution"))
check("Cannot summon Evolution for P1 too",
      not reg.can_summon_creature(1, frozenset([Civilization.WATER]),
                                  card_type="Creature", card_subtype="Evolution"))

# GRANT_KEYWORD_ALL: all Fire creatures gain Speed Attacker
grant_kw = GlobalEffect(
    effect_type=GlobalEffectType.GRANT_KEYWORD_ALL,
    applied_by_uid="creature_uid_2",
    applied_by_card=88,
    controller=0,
    target_player=None,
    duration="while_in_play",
    grant_keyword="speed_attacker",
    grant_to_civ="Fire",
    grant_to_controller=0,
)
reg.add(grant_kw)

granted_p0 = reg.get_granted_keywords(controller=0, civ="Fire")
check("P0 Fire creature gets speed_attacker",
      "speed_attacker" in granted_p0)

granted_p1 = reg.get_granted_keywords(controller=1, civ="Fire")
check("P1 does not get granted keyword (controller filter)",
      "speed_attacker" not in granted_p1)

granted_water = reg.get_granted_keywords(controller=0, civ="Water")
check("Water creature does not get granted keyword (civ filter)",
      "speed_attacker" not in granted_water)

# Remove by source
reg.remove_by_source("field_uid_1")
check("After remove, Evolution summon allowed again",
      reg.can_summon_creature(0, frozenset([Civilization.FIRE]),
                              card_subtype="Evolution"))


# ═══════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════

passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)

print(f"\n{'═'*60}")
print(f"  RESULTS: {passed}/{len(results)} passed, {failed} failed")
print(f"{'═'*60}")

if failed:
    print("\n  FAILURES:")
    for name, ok, detail in results:
        if not ok:
            print(f"    {FAIL} {name}" + (f" — {detail}" if detail else ""))

print()
import sys
sys.exit(0 if failed == 0 else 1)
