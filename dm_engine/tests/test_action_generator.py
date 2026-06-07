"""
tests/test_action_generator.py

Tests for engine/action_generator.py — every rule-grounded legal action check.
Runs without a database connection using mock GameStates.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from copy import deepcopy

from core.enums import (
    Phase, ActionType, Civilization, Keyword,
    CardType, CardSubtype, GlobalEffectType, ManaUsage,
)
from core.cards import CardDefinition, DeckDefinition
from core.zones import HandCard, ManaCard, ShieldCard, Creature
from core.player_state import PlayerState
from core.state import GameState, TurnInfo, EffectStack, AwaitedChoice
from core.global_effects import GlobalEffect, GlobalEffectRegistry
from core.initializer import initialize_game
from engine.action_generator import (
    get_legal_actions, _get_mana_combinations,
    _get_valid_evolution_bases, can_play_card, can_attack,
)

PASS = "✅"
FAIL = "❌"
results = []

def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    mark = PASS if ok else FAIL
    print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))
    return ok


# ── Helpers ────────────────────────────────────────────────────────────────────

def _card(cid, name, cost, civs, power=2000, card_type=CardType.CREATURE,
          keywords=None, subtype=CardSubtype.NONE, races=None, evo_races=None):
    return CardDefinition(
        id=cid, slug=name.lower().replace(" ","_"), name=name,
        cost=cost,
        power=power if card_type == CardType.CREATURE else None,
        card_type=card_type, card_subtype=subtype,
        civilizations=frozenset(civs),
        races=frozenset(races or []),
        keywords=frozenset(keywords or []),
        effects=tuple(),
        evolution_source_races=frozenset(evo_races or []),
        evolution_source_types=frozenset(),
        is_multiface=False,
    )

def _make_mana(defn, tapped=False):
    return ManaCard(definition=defn, is_tapped=tapped)

def _make_hand(defn):
    return HandCard(definition=defn)

def _make_creature(defn, controller=0, tapped=False, sick=True):
    return Creature(
        definition=defn, controller=controller, owner=controller,
        is_tapped=tapped, has_summoning_sickness=sick,
    )

def _bare_state(active=0, phase=Phase.MAIN) -> GameState:
    """Empty game state — no cards anywhere."""
    p0 = PlayerState(player_index=0, player_name="P0", deck_composition={})
    p1 = PlayerState(player_index=1, player_name="P1", deck_composition={})
    return GameState(
        players=(p0, p1),
        turn_info=TurnInfo(turn_number=2, active_player=active, phase=phase),
    )

# ── Card fixtures ──────────────────────────────────────────────────────────────
FIRE_3     = _card(1,  "Fire3",    3, [Civilization.FIRE])
WATER_4    = _card(2,  "Water4",   4, [Civilization.WATER])
NATURE_2   = _card(3,  "Nature2",  2, [Civilization.NATURE])
DARK_6     = _card(4,  "Dark6",    6, [Civilization.DARKNESS])
LIGHT_5    = _card(5,  "Light5",   5, [Civilization.LIGHT])
MULTI_FN   = _card(6,  "Multi_FN", 5, [Civilization.FIRE, Civilization.NATURE])
SPELL_W3   = _card(7,  "WSpell3",  3, [Civilization.WATER],  card_type=CardType.SPELL)
SPELL_D5   = _card(8,  "DSpell5",  5, [Civilization.DARKNESS], card_type=CardType.SPELL)
BLOCKER_C  = _card(9,  "Blocker",  3, [Civilization.WATER],  keywords=[Keyword.BLOCKER])
DBLBKR_C   = _card(10, "DblBkr",  5, [Civilization.FIRE],   keywords=[Keyword.DOUBLE_BREAKER])
SPD_ATK    = _card(11, "SpeedAtk", 3, [Civilization.FIRE],   keywords=[Keyword.SPEED_ATTACKER])
EVO_DRAG   = _card(12, "EvoDrag",  4, [Civilization.FIRE],
                   subtype=CardSubtype.EVOLUTION, evo_races=["Dragon"])
ST_SPELL   = _card(13, "STSpell",  3, [Civilization.WATER],
                   card_type=CardType.SPELL, keywords=[Keyword.SHIELD_TRIGGER])
CROSS_GEAR = _card(14, "CGear",    2, [Civilization.FIRE],   card_type=CardType.CROSS_GEAR)
MACH_C     = _card(15, "Mach",     3, [Civilization.FIRE],   keywords=[Keyword.MACH_FIGHTER])
DRAGON_C   = _card(16, "Dragon",   4, [Civilization.FIRE],   races=["Dragon"])
GNINJA     = _card(17, "Ninja",    3, [Civilization.WATER],  keywords=[Keyword.NINJA_STRIKE])
GZERO_C    = _card(18, "GZero",    4, [Civilization.FIRE],
                   keywords=[Keyword.G_ZERO], races=["Dragon"])


print("\n" + "═"*65)
print("  DM ENGINE — ACTION GENERATOR TEST SUITE")
print("═"*65)


# ═══════════════════════════════════════════════════════════════════
print("\n── 1. Mana Combination Algorithm (rule 112.2a) ─────────────")
# ═══════════════════════════════════════════════════════════════════

# Basic: 3 fire mana, card costs 3 fire
mana3 = [_make_mana(FIRE_3), _make_mana(FIRE_3), _make_mana(FIRE_3)]
combos = _get_mana_combinations(mana3, 3, frozenset([Civilization.FIRE]))
check("3 fire mana → can pay 3 fire",   len(combos) > 0)
check("combo has 3 entries",            all(len(c) == 3 for c in combos))
check("all chosen are fire civ",
      all(any(u.used_for_civ == Civilization.FIRE for u in c) for c in combos))

# Not enough mana
mana1 = [_make_mana(FIRE_3)]
combos_fail = _get_mana_combinations(mana1, 3, frozenset([Civilization.FIRE]))
check("1 fire mana can't pay 3",        len(combos_fail) == 0)

# Wrong civilization
water_mana = [_make_mana(WATER_4), _make_mana(WATER_4), _make_mana(WATER_4)]
combos_wrong = _get_mana_combinations(water_mana, 3, frozenset([Civilization.FIRE]))
check("Water mana can't pay Fire cost", len(combos_wrong) == 0)

# Multi-colored mana (rule 112.2a) — can provide ONE chosen civ
multi_m = _make_mana(MULTI_FN)   # Fire/Nature card
mana_mix = [multi_m, _make_mana(FIRE_3)]
# For a 2-cost Fire/Nature card, multi card can cover either civ
combos_multi = _get_mana_combinations(
    mana_mix, 2, frozenset([Civilization.FIRE, Civilization.NATURE])
)
check("Multi-civ mana enables F/N payment", len(combos_multi) > 0)
# Each combo must have exactly one mana card providing FIRE and one NATURE
for combo in combos_multi:
    fire_covered = any(u.used_for_civ == Civilization.FIRE for u in combo)
    nature_covered = any(u.used_for_civ == Civilization.NATURE for u in combo)
    check("Combo covers Fire civ",   fire_covered)
    check("Combo covers Nature civ", nature_covered)
    break

# Tapped mana cannot be used
tapped_mana = [_make_mana(FIRE_3, tapped=True), _make_mana(FIRE_3, tapped=True)]
combos_tapped = _get_mana_combinations(tapped_mana, 2, frozenset([Civilization.FIRE]))
check("Tapped mana cannot be used",     len(combos_tapped) == 0)

# Mixed tapped/untapped
mixed_mana = [_make_mana(FIRE_3, tapped=True), _make_mana(FIRE_3), _make_mana(NATURE_2)]
combos_mixed = _get_mana_combinations(
    mixed_mana, 2, frozenset([Civilization.FIRE])
)
check("Untapped fire mana usable when others tapped", len(combos_mixed) > 0)

# Free colorless card (cost 0, no civ requirement)
combos_free = _get_mana_combinations([], 0, frozenset())
check("Cost 0 colorless card is affordable", len(combos_free) > 0)

# Reduced-cost civilization card still pays civilizations (rule 112.2b)
combos_zero_fire = _get_mana_combinations([_make_mana(FIRE_3)], 0, frozenset([Civilization.FIRE]))
check("Cost 0 Fire card still taps Fire mana", len(combos_zero_fire) > 0 and len(combos_zero_fire[0]) == 1)
combos_zero_fire_fail = _get_mana_combinations([], 0, frozenset([Civilization.FIRE]))
check("Cost 0 Fire card still needs Fire source", len(combos_zero_fire_fail) == 0)


# ═══════════════════════════════════════════════════════════════════
print("\n── 2. Mana Charge Phase (rule 503) ─────────────────────────")
# ═══════════════════════════════════════════════════════════════════

s = _bare_state(phase=Phase.MANA_CHARGE)
# No hand → only pass
actions = get_legal_actions(s)
check("No hand → only PASS in charge", 
      len(actions) == 1 and actions[0].action_type == ActionType.PASS)

# Add cards to hand
s.players[0].hand = [_make_hand(FIRE_3), _make_hand(WATER_4)]
actions = get_legal_actions(s)
types = [a.action_type for a in actions]
check("2 hand cards → 2 charge + 1 pass",  len(actions) == 3)
check("CHARGE_MANA actions present",       ActionType.CHARGE_MANA in types)
check("PASS present in charge",            ActionType.PASS in types)

# Already charged this turn
s.players[0].has_charged_mana_this_turn = True
actions = get_legal_actions(s)
check("Already charged → only PASS",
      len(actions) == 1 and actions[0].action_type == ActionType.PASS)

# Global effect: cannot charge mana
s2 = _bare_state(phase=Phase.MANA_CHARGE)
s2.players[0].hand = [_make_hand(FIRE_3)]
s2.global_effects.add(GlobalEffect(
    effect_type=GlobalEffectType.CANNOT_CHARGE_MANA,
    applied_by_uid="uid1", applied_by_card=99,
    controller=1, target_player=0, duration="while_in_play",
))
actions = get_legal_actions(s2)
check("Cannot charge mana global effect → only PASS",
      all(a.action_type == ActionType.PASS for a in actions))


# ═══════════════════════════════════════════════════════════════════
print("\n── 3. Main Phase — Summon Creature (rules 301, 112.2a) ──────")
# ═══════════════════════════════════════════════════════════════════

s = _bare_state(phase=Phase.MAIN)
# 3 fire mana, fire creature costing 3
s.players[0].mana_zone = [_make_mana(FIRE_3) for _ in range(3)]
s.players[0].hand = [_make_hand(FIRE_3)]
actions = get_legal_actions(s)
types = [a.action_type for a in actions]
check("Can summon with exact mana",     ActionType.SUMMON_CREATURE in types)
check("PASS also available in main",    ActionType.PASS in types)

# Not enough mana
s2 = _bare_state(phase=Phase.MAIN)
s2.players[0].mana_zone = [_make_mana(FIRE_3) for _ in range(2)]  # 2 mana
s2.players[0].hand = [_make_hand(FIRE_3)]  # costs 3
actions = get_legal_actions(s2)
types = [a.action_type for a in actions]
check("Can't summon with insufficient mana", ActionType.SUMMON_CREATURE not in types)
check("PASS still available",               ActionType.PASS in types)

# Wrong civilization
s3 = _bare_state(phase=Phase.MAIN)
s3.players[0].mana_zone = [_make_mana(WATER_4) for _ in range(3)]  # water mana
s3.players[0].hand = [_make_hand(FIRE_3)]            # fire card
actions = get_legal_actions(s3)
types = [a.action_type for a in actions]
check("Can't summon without correct civ", ActionType.SUMMON_CREATURE not in types)

# Global restriction: cannot summon creatures
s4 = _bare_state(phase=Phase.MAIN)
s4.players[0].mana_zone = [_make_mana(FIRE_3) for _ in range(3)]
s4.players[0].hand = [_make_hand(FIRE_3)]
s4.global_effects.add(GlobalEffect(
    effect_type=GlobalEffectType.LOCK_ALL_CREATURES,
    applied_by_uid="u1", applied_by_card=99,
    controller=1, target_player=0, duration="while_in_play",
))
actions = get_legal_actions(s4)
types = [a.action_type for a in actions]
check("Cannot summon when LOCK_ALL_CREATURES",
      ActionType.SUMMON_CREATURE not in types)


# ═══════════════════════════════════════════════════════════════════
print("\n── 4. Main Phase — Cast Spell (rule 302, global restrictions) ")
# ═══════════════════════════════════════════════════════════════════

s = _bare_state(phase=Phase.MAIN)
s.players[0].mana_zone = [_make_mana(WATER_4) for _ in range(3)]
s.players[0].hand = [_make_hand(SPELL_W3)]
actions = get_legal_actions(s)
types = [a.action_type for a in actions]
check("Can cast spell with enough water mana", ActionType.CAST_SPELL in types)

# Alcadeias effect: only Light spells allowed
s2 = _bare_state(phase=Phase.MAIN)
s2.players[0].mana_zone = [_make_mana(DARK_6) for _ in range(6)]
s2.players[0].hand = [_make_hand(SPELL_D5)]
s2.global_effects.add(GlobalEffect(
    effect_type=GlobalEffectType.RESTRICT_SPELL_CIVILIZATION,
    applied_by_uid="alc_uid", applied_by_card=8,
    controller=1, target_player=None,
    duration="while_in_play",
    allowed_civilizations=frozenset([Civilization.LIGHT]),
))
actions = get_legal_actions(s2)
types = [a.action_type for a in actions]
check("Darkness spell blocked by Light-only restriction",
      ActionType.CAST_SPELL not in types)

# LOCK_ALL_SPELLS
s3 = _bare_state(phase=Phase.MAIN)
s3.players[0].mana_zone = [_make_mana(WATER_4) for _ in range(3)]
s3.players[0].hand = [_make_hand(SPELL_W3)]
s3.global_effects.add(GlobalEffect(
    effect_type=GlobalEffectType.LOCK_ALL_SPELLS,
    applied_by_uid="lock_uid", applied_by_card=99,
    controller=1, target_player=0, duration="while_in_play",
))
actions = get_legal_actions(s3)
types = [a.action_type for a in actions]
check("LOCK_ALL_SPELLS prevents casting", ActionType.CAST_SPELL not in types)


# ═══════════════════════════════════════════════════════════════════
print("\n── 5. Evolution Creatures (rule 801) ────────────────────────")
# ═══════════════════════════════════════════════════════════════════

# Need a Dragon base in battle zone
s = _bare_state(phase=Phase.MAIN)
s.players[0].mana_zone = [_make_mana(FIRE_3) for _ in range(4)]
s.players[0].hand = [_make_hand(EVO_DRAG)]   # costs 4, evolves from Dragon
dragon_base = _make_creature(DRAGON_C, sick=False)  # Dragon race
s.players[0].battle_zone = [dragon_base]

actions = get_legal_actions(s)
types = [a.action_type for a in actions]
check("Can summon evolution with Dragon base", ActionType.SUMMON_CREATURE in types)

# Check evolution_base_uid is set
evo_actions = [a for a in actions if a.action_type == ActionType.SUMMON_CREATURE]
check("Evolution action has base uid",
      all(a.evolution_base_uid == dragon_base.uid for a in evo_actions))

# No valid base
s2 = _bare_state(phase=Phase.MAIN)
s2.players[0].mana_zone = [_make_mana(FIRE_3) for _ in range(4)]
s2.players[0].hand = [_make_hand(EVO_DRAG)]
# No creatures in battle zone
actions2 = get_legal_actions(s2)
types2 = [a.action_type for a in actions2]
check("Cannot evolve without base", ActionType.SUMMON_CREATURE not in types2)

# Ignored base (rule 116.2 — cannot evolve onto ignored creature)
s3 = _bare_state(phase=Phase.MAIN)
s3.players[0].mana_zone = [_make_mana(FIRE_3) for _ in range(4)]
s3.players[0].hand = [_make_hand(EVO_DRAG)]
ignored_dragon = _make_creature(DRAGON_C, sick=False)
ignored_dragon.seals.append(WATER_4)  # has seal → is_ignored=True
s3.players[0].battle_zone = [ignored_dragon]
actions3 = get_legal_actions(s3)
types3 = [a.action_type for a in actions3]
check("Cannot evolve onto ignored (sealed) creature",
      ActionType.SUMMON_CREATURE not in types3)


# ═══════════════════════════════════════════════════════════════════
print("\n── 6. Attack Phase — Attack Declarations (rules 505, 506) ──")
# ═══════════════════════════════════════════════════════════════════

s = _bare_state(phase=Phase.ATTACK)
# Our creature: not sick, not tapped
attacker = _make_creature(FIRE_3, sick=False)
s.players[0].battle_zone = [attacker]
# Opponent: add a shield so no direct attack yet; tapped creature
opp_creature = _make_creature(WATER_4, controller=1, tapped=True)
s.players[1].battle_zone = [opp_creature]
s.players[1].shield_zone = [ShieldCard(definition=FIRE_3)]

actions = get_legal_actions(s)
types = [a.action_type for a in actions]
check("Can attack player",    ActionType.ATTACK_PLAYER in types)
check("Can attack tapped opp creature", ActionType.ATTACK_CREATURE in types)
check("PASS available",       ActionType.PASS in types)

# Summoning sickness prevents attack (rule 506.1a)
sick_attacker = _make_creature(FIRE_3, sick=True)
s2 = _bare_state(phase=Phase.ATTACK)
s2.players[0].battle_zone = [sick_attacker]
s2.players[1].shield_zone = [ShieldCard(definition=FIRE_3)]
actions2 = get_legal_actions(s2)
types2 = [a.action_type for a in actions2]
check("Summoning sickness prevents attack",
      ActionType.ATTACK_PLAYER not in types2)

# Speed Attacker ignores summoning sickness (rule 101.2: card beats rule)
speed = _make_creature(SPD_ATK, sick=True)
s3 = _bare_state(phase=Phase.ATTACK)
s3.players[0].battle_zone = [speed]
s3.players[1].shield_zone = [ShieldCard(definition=FIRE_3)]
actions3 = get_legal_actions(s3)
types3 = [a.action_type for a in actions3]
check("Speed Attacker can attack while sick",
      ActionType.ATTACK_PLAYER in types3)

# Tapped creature cannot attack (rule 506.1a)
tapped_att = _make_creature(FIRE_3, tapped=True, sick=False)
s4 = _bare_state(phase=Phase.ATTACK)
s4.players[0].battle_zone = [tapped_att]
s4.players[1].shield_zone = [ShieldCard(definition=FIRE_3)]
actions4 = get_legal_actions(s4)
types4 = [a.action_type for a in actions4]
check("Tapped creature cannot attack", ActionType.ATTACK_PLAYER not in types4)

# Ignored creature cannot attack (rule 116.2)
ignored_att = _make_creature(FIRE_3, sick=False)
ignored_att.seals.append(WATER_4)
s5 = _bare_state(phase=Phase.ATTACK)
s5.players[0].battle_zone = [ignored_att]
s5.players[1].shield_zone = [ShieldCard(definition=FIRE_3)]
actions5 = get_legal_actions(s5)
types5 = [a.action_type for a in actions5]
check("Ignored (sealed) creature cannot attack",
      ActionType.ATTACK_PLAYER not in types5)

# Cannot attack players (temp flag)
no_atk_player = _make_creature(FIRE_3, sick=False)
no_atk_player.set_flag("cannot_attack_players", True)
s6 = _bare_state(phase=Phase.ATTACK)
s6.players[0].battle_zone = [no_atk_player]
s6.players[1].shield_zone = [ShieldCard(definition=FIRE_3)]
s6.players[1].battle_zone = [_make_creature(WATER_4, controller=1, tapped=True)]
actions6 = get_legal_actions(s6)
types6 = [a.action_type for a in actions6]
check("cannot_attack_players flag works",
      ActionType.ATTACK_PLAYER not in types6)
check("Can still attack tapped creature",
      ActionType.ATTACK_CREATURE in types6)

# Mach Fighter can attack untapped creatures (rule: Mach Fighter ability)
mach = _make_creature(MACH_C, sick=False)
s7 = _bare_state(phase=Phase.ATTACK)
s7.players[0].battle_zone = [mach]
s7.players[1].shield_zone = [ShieldCard(definition=FIRE_3)]
untapped_opp = _make_creature(WATER_4, controller=1, tapped=False)
s7.players[1].battle_zone = [untapped_opp]
actions7 = get_legal_actions(s7)
types7 = [a.action_type for a in actions7]
check("Mach Fighter can attack untapped creature",
      ActionType.ATTACK_CREATURE in types7)

# Global cannot attack
s8 = _bare_state(phase=Phase.ATTACK)
s8.players[0].battle_zone = [_make_creature(FIRE_3, sick=False)]
s8.players[1].shield_zone = [ShieldCard(definition=FIRE_3)]
s8.global_effects.add(GlobalEffect(
    effect_type=GlobalEffectType.CANNOT_ATTACK,
    applied_by_uid="u1", applied_by_card=99,
    controller=1, target_player=0, duration="while_in_play",
))
actions8 = get_legal_actions(s8)
types8 = [a.action_type for a in actions8]
check("Global cannot_attack → only PASS",
      ActionType.ATTACK_PLAYER not in types8)


# ═══════════════════════════════════════════════════════════════════
print("\n── 7. Block Phase (rule 507) ────────────────────────────────")
# ═══════════════════════════════════════════════════════════════════

from core.state import AttackContext

def _state_with_attack(attacker_player=0, target_type="player"):
    s = _bare_state(phase=Phase.BLOCK_DECLARE, active=attacker_player)
    attacker = _make_creature(FIRE_3, sick=False)
    s.players[attacker_player].battle_zone = [attacker]
    ctx = AttackContext(
        attacker_uid=attacker.uid,
        attacker_player=attacker_player,
        target_type=target_type,
        target_uid=f"player_{1 - attacker_player}",
    )
    s.attack_context = ctx
    return s, attacker

# Untapped blocker can block
s, _ = _state_with_attack()
blocker = _make_creature(BLOCKER_C, controller=1, tapped=False, sick=False)
s.players[1].battle_zone = [blocker]
actions = get_legal_actions(s)
types = [a.action_type for a in actions]
check("Untapped Blocker can declare block", ActionType.DECLARE_BLOCKER in types)
check("PASS available (don't block)",       ActionType.PASS in types)

# Tapped blocker cannot block (rule 507.1a)
s2, _ = _state_with_attack()
tapped_blocker = _make_creature(BLOCKER_C, controller=1, tapped=True, sick=False)
s2.players[1].battle_zone = [tapped_blocker]
actions2 = get_legal_actions(s2)
types2 = [a.action_type for a in actions2]
check("Tapped Blocker cannot declare block",
      ActionType.DECLARE_BLOCKER not in types2)

# Ignored blocker cannot block (rule 116.2)
s3, _ = _state_with_attack()
ign_blocker = _make_creature(BLOCKER_C, controller=1, tapped=False, sick=False)
ign_blocker.seals.append(WATER_4)
s3.players[1].battle_zone = [ign_blocker]
actions3 = get_legal_actions(s3)
types3 = [a.action_type for a in actions3]
check("Ignored Blocker cannot declare block",
      ActionType.DECLARE_BLOCKER not in types3)

# Cannot-be-blocked attacker → no block options at all (rule 507.1a example)
s4 = _bare_state(phase=Phase.BLOCK_DECLARE)
unblockable = _make_creature(FIRE_3, sick=False)
unblockable.set_flag("cannot_be_blocked", True)
s4.players[0].battle_zone = [unblockable]
blocker2 = _make_creature(BLOCKER_C, controller=1, tapped=False, sick=False)
s4.players[1].battle_zone = [blocker2]
ctx2 = AttackContext(
    attacker_uid=unblockable.uid, attacker_player=0,
    target_type="player", target_uid="player_1",
)
s4.attack_context = ctx2
actions4 = get_legal_actions(s4)
types4 = [a.action_type for a in actions4]
check("Unblockable attacker → only PASS for defender",
      ActionType.DECLARE_BLOCKER not in types4 and ActionType.PASS in types4)

# Guardman cannot be used when the player is attacked (rule 507.1a)
s5 = _bare_state(phase=Phase.BLOCK_DECLARE)
attacker5 = _make_creature(FIRE_3, sick=False)
s5.players[0].battle_zone = [attacker5]
guardman = _make_creature(
    _card(20, "Guardman", 3, [Civilization.LIGHT], keywords=[Keyword.GUARDMAN]),
    controller=1, tapped=False, sick=False
)
s5.players[1].battle_zone = [guardman]
ctx5 = AttackContext(
    attacker_uid=attacker5.uid, attacker_player=0,
    target_type="player", target_uid="player_1",
)
s5.attack_context = ctx5
actions5 = get_legal_actions(s5)
types5 = [a.action_type for a in actions5]
check("Guardman cannot block when player attacked",
      ActionType.DECLARE_GUARDMAN not in types5)

# Guardman can change target when a creature is attacked (rule 507.1a)
s6 = _bare_state(phase=Phase.BLOCK_DECLARE)
attacker6 = _make_creature(FIRE_3, sick=False)
target6 = _make_creature(WATER_4, controller=1, tapped=True)
s6.players[0].battle_zone = [attacker6]
guardman2 = _make_creature(
    _card(21, "Guardman2", 3, [Civilization.LIGHT], keywords=[Keyword.GUARDMAN]),
    controller=1, tapped=False, sick=False
)
s6.players[1].battle_zone = [target6, guardman2]
ctx6 = AttackContext(
    attacker_uid=attacker6.uid, attacker_player=0,
    target_type="creature", target_uid=target6.uid,
)
s6.attack_context = ctx6
actions6 = get_legal_actions(s6)
types6 = [a.action_type for a in actions6]
check("Guardman can block when creature attacked",
      ActionType.DECLARE_GUARDMAN in types6)


# ═══════════════════════════════════════════════════════════════════
print("\n── 8. Shield Trigger Actions (rule 113.6, 509.5a-c) ─────────")
# ═══════════════════════════════════════════════════════════════════

s = _bare_state(phase=Phase.DIRECT_ATTACK)
# Manually push a shield trigger onto the queue
st_shield = ShieldCard(definition=ST_SPELL)  # has SHIELD_TRIGGER
s.effect_stack.shield_trigger_queue.append((1, st_shield))

actions = get_legal_actions(s)
types = [a.action_type for a in actions]
check("Shield trigger actions offered",
      ActionType.USE_SHIELD_TRIGGER in types)
check("Can also pass (add to hand)",  ActionType.PASS in types)

# Shield without trigger — only PASS
plain_shield = ShieldCard(definition=FIRE_3)  # no SHIELD_TRIGGER
s2 = _bare_state(phase=Phase.DIRECT_ATTACK)
s2.effect_stack.shield_trigger_queue.append((1, plain_shield))
actions2 = get_legal_actions(s2)
types2 = [a.action_type for a in actions2]
check("No shield trigger card → only PASS",
      ActionType.USE_SHIELD_TRIGGER not in types2 and ActionType.PASS in types2)


# ═══════════════════════════════════════════════════════════════════
print("\n── 9. Awaited Choice (effect stack paused) ──────────────────")
# ═══════════════════════════════════════════════════════════════════

s = _bare_state(phase=Phase.MAIN)
# Add a yes/no choice
choice = AwaitedChoice(
    choice_type="yes_no",
    player=0,
    effect=None,
    source_uid="eff_uid_1",
    valid_options=[True, False],
    prompt="Use optional ETB effect?",
)
s.effect_stack.set_choice(choice)

actions = get_legal_actions(s)
types = [a.action_type for a in actions]
check("Yes/No choice → only yes/no actions",
      all(a.action_type == ActionType.SELECT_YES_NO for a in actions))
check("Both yes and no offered",        len(actions) == 2)
choices = [a.choice for a in actions]
check("True option present",            True in choices)
check("False option present",           False in choices)

# Target selection choice
s2 = _bare_state(phase=Phase.MAIN)
c1 = _make_creature(FIRE_3, controller=1)
c2 = _make_creature(WATER_4, controller=1)
s2.players[1].battle_zone = [c1, c2]

choice2 = AwaitedChoice(
    choice_type="select_target",
    player=0,
    effect=None,
    source_uid="eff_uid_2",
    valid_options=[c1.uid, c2.uid],
    min_choices=1, max_choices=1,
)
s2.effect_stack.set_choice(choice2)
actions2 = get_legal_actions(s2)
types2 = [a.action_type for a in actions2]
check("Target choice → only SELECT_TARGET actions",
      all(a.action_type == ActionType.SELECT_TARGET for a in actions2))
check("Both targets offered",           len(actions2) == 2)


# ═══════════════════════════════════════════════════════════════════
print("\n── 10. Draw and End Phases ──────────────────────────────────")
# ═══════════════════════════════════════════════════════════════════

s_draw = _bare_state(phase=Phase.DRAW)
actions = get_legal_actions(s_draw)
check("Draw phase → only PASS",
      len(actions) == 1 and actions[0].action_type == ActionType.PASS)

s_eot = _bare_state(phase=Phase.END_OF_TURN)
actions = get_legal_actions(s_eot)
check("End of turn → only PASS",
      len(actions) == 1 and actions[0].action_type == ActionType.PASS)

s_eoa = _bare_state(phase=Phase.END_OF_ATTACK)
actions = get_legal_actions(s_eoa)
check("End of attack → only PASS",
      len(actions) == 1 and actions[0].action_type == ActionType.PASS)


# ═══════════════════════════════════════════════════════════════════
print("\n── 11. can_play_card and can_attack helpers ─────────────────")
# ═══════════════════════════════════════════════════════════════════

s = _bare_state(phase=Phase.MAIN)
s.players[0].mana_zone = [_make_mana(FIRE_3) for _ in range(3)]
hc = _make_hand(FIRE_3)
s.players[0].hand = [hc]

check("can_play_card: affordable card",   can_play_card(s, 0, hc.uid))

s2 = _bare_state(phase=Phase.MAIN)
s2.players[0].mana_zone = [_make_mana(FIRE_3)] * 2  # not enough
hc2 = _make_hand(FIRE_3)
s2.players[0].hand = [hc2]
check("can_play_card: not enough mana",   not can_play_card(s2, 0, hc2.uid))

s3 = _bare_state(phase=Phase.ATTACK)
attacker3 = _make_creature(FIRE_3, sick=False)
s3.players[0].battle_zone = [attacker3]
check("can_attack: valid attacker",       can_attack(s3, 0, attacker3.uid))

s4 = _bare_state(phase=Phase.ATTACK)
sick3 = _make_creature(FIRE_3, sick=True)
s4.players[0].battle_zone = [sick3]
check("can_attack: sick creature",        not can_attack(s4, 0, sick3.uid))


# ═══════════════════════════════════════════════════════════════════
print("\n── 12. Cross Gear in Main Phase (rule 504.2) ────────────────")
# ═══════════════════════════════════════════════════════════════════

s = _bare_state(phase=Phase.MAIN)
# Cross gear already in battle zone (already generated)
gear_creature = _make_creature(
    _card(14, "CGear", 2, [Civilization.FIRE], card_type=CardType.CROSS_GEAR),
    sick=False
)
normal_creature = _make_creature(FIRE_3, sick=False)
s.players[0].battle_zone = [gear_creature, normal_creature]
s.players[0].mana_zone = [_make_mana(FIRE_3) for _ in range(2)]

actions = get_legal_actions(s)
types = [a.action_type for a in actions]
check("Can cross existing gear in main phase",
      ActionType.CROSS_GEAR in types)
check("Cross targets the non-gear creature",
      any(a.action_type == ActionType.CROSS_GEAR
          and a.target_uid == normal_creature.uid for a in actions))


# ═══════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════

passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)

print(f"\n{'═'*65}")
print(f"  RESULTS: {passed}/{len(results)} passed, {failed} failed")
print(f"{'═'*65}")

if failed:
    print("\n  FAILURES:")
    for name, ok, detail in results:
        if not ok:
            print(f"    {FAIL} {name}" + (f" — {detail}" if detail else ""))

print()
import sys
sys.exit(0 if failed == 0 else 1)
