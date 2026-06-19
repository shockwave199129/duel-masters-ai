"""
tests/test_special_cards.py — coverage for special card type mechanics.

Sections:
  1. Twinpact flip (Rule 810)
  2. Forbidden flip (Rule 809)
  3. GR Summon from Ultra GR (Rule 811)
  4. Zerom ritual (Rule 812)
  5. Hyper Mode swap (Rule 816)
  6. Star Evolution uniqueness SBA (Rule 813)
  7. Dream Rare uniqueness SBA (Rule 817)
  8. Duel Mate cleanup SBA (Rule 820)
  9. G-Castle shield behavior (Rule 822)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.cards import (
    CardDefinition,
    CardEffect,
    is_twinpact,
    is_forbidden,
    is_zerom,
    is_zerom_creature,
    is_hyper_mode,
    is_star_evolution,
    is_dream_rare,
    is_duel_mate,
    is_g_castle,
)
from core.enums import (
    CardSubtype,
    CardType,
    Civilization,
    EffectAction,
    Keyword,
    Phase,
)
from core.player_state import PlayerState
from core.state import GameState, TurnInfo
from core.zones import Creature, HandCard, ShieldCard
from engine.zone_mover import (
    flip_twinpact,
    flip_forbidden,
    move_ultra_gr_to_battle,
    move_zerom_to_battle,
    swap_hyper_mode,
    move_hand_to_battle,
    move_shield_to_standby,
    move_standby_shield_to_hand,
)
from engine.sba_checker import check_state_based_actions

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))
    return ok


def card(cid, name, card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE,
         civs=(Civilization.FIRE,), power=1000, cost=3, races=None,
         keywords=None, effects=None, is_multiface=False, other_face_id=None):
    return CardDefinition(
        id=cid,
        slug=name.lower().replace(" ", "_"),
        name=name,
        cost=cost,
        power=power if card_type == CardType.CREATURE else None,
        card_type=card_type,
        card_subtype=card_subtype,
        civilizations=frozenset(civs),
        races=frozenset(races or ["Human"]),
        keywords=frozenset(keywords or []),
        effects=tuple(effects or []),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=is_multiface,
        other_face_id=other_face_id,
    )


def make_creature(card_def, controller=0, zone=None, **kwargs):
    defaults = dict(
        definition=card_def,
        uid=f"creature-{card_def.id}-c{controller}",
        controller=controller,
        owner=controller,
        entered_turn=2,
        has_summoning_sickness=True,
    )
    defaults.update(kwargs)
    return Creature(**defaults)


def make_state(p0_battle=None, p1_battle=None, p0_hand=None, p0_shields=None,
               p0_ultra_gr=None, turn=2, phase=Phase.MAIN):
    filler = card(99, "DeckFiller")
    return GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0", deck=[filler],
                hand=p0_hand or [],
                battle_zone=p0_battle or [],
                shield_zone=p0_shields or [],
                ultra_gr_zone=p0_ultra_gr or [],
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
print("  DM ENGINE — SPECIAL CARDS TESTS")
print("═" * 60)

# ─────────────────────────────────────────────────────────────────────────────
# Section 1: Twinpact (Rule 810)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 1: TWINPACT (Rule 810)")
print("─" * 60)

# 1a) is_twinpact returns True for cards with is_multiface=True
twinpact_card = card(100, "Twinpact Beast", is_multiface=True, other_face_id=101, power=3000)
check("1a: is_twinpact() returns True for is_multiface card",
      is_twinpact(twinpact_card) == True)

# 1b) is_twinpact returns False for normal cards
normal_card = card(102, "Normal Creature", power=2000)
check("1b: is_twinpact() returns False for normal card",
      is_twinpact(normal_card) == False)

# 1c) flip_twinpact toggles _twinpact_flipped flag
twinpact_creature = make_creature(twinpact_card, controller=0)
check("1c: flip_twinpact toggles _twinpact_flipped flag",
      flip_twinpact(twinpact_creature).temp_flags.get("_twinpact_flipped") == True,
      f"got {twinpact_creature.temp_flags.get('_twinpact_flipped')}")

# 1d) flip_twinpact on non-multiface creature is a no-op
normal_creature = make_creature(normal_card, controller=0)
result = flip_twinpact(normal_creature)
check("1d: flip_twinpact on non-multiface is no-op (no flag set)",
      "_twinpact_flipped" not in result.temp_flags)

# 1e) Twinpact flip is triggered when creature enters battle zone via move_hand_to_battle
twinpact_hand = card(103, "Hand Twinpact", is_multiface=True, other_face_id=104, power=4000)
state_1e = make_state(p0_hand=[HandCard(definition=twinpact_hand)])
creature_1e = move_hand_to_battle(state_1e, 0, state_1e.players[0].hand[0].uid)
check("1e: Twinpact flip triggered on move_hand_to_battle",
      creature_1e.temp_flags.get("_twinpact_flipped") == True,
      f"flag={creature_1e.temp_flags.get('_twinpact_flipped')}")


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: Forbidden (Rule 809)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 2: FORBIDDEN (Rule 809)")
print("─" * 60)

# 2a) is_forbidden returns True for CardSubtype.FORBIDDEN
forbidden_card = card(200, "Forbidden One", card_subtype=CardSubtype.FORBIDDEN, power=5000)
check("2a: is_forbidden() returns True for FORBIDDEN subtype",
      is_forbidden(forbidden_card) == True)

# 2b) is_forbidden returns True for CardSubtype.FINAL_FORBIDDEN
final_forbidden = card(201, "Final Forbidden", card_subtype=CardSubtype.FINAL_FORBIDDEN, power=6000)
check("2b: is_forbidden() returns True for FINAL_FORBIDDEN subtype",
      is_forbidden(final_forbidden) == True)

# 2c) is_forbidden returns False for normal cards
check("2c: is_forbidden() returns False for normal card",
      is_forbidden(normal_card) == False)

# 2d) flip_forbidden toggles _forbidden_flipped flag
forbidden_creature = make_creature(forbidden_card, controller=0)
flip_forbidden(forbidden_creature)
check("2d: flip_forbidden toggles _forbidden_flipped flag",
      forbidden_creature.temp_flags.get("_forbidden_flipped") == True)

# 2e) flip_forbidden toggles face field (0→1)
forbidden_creature_2e = make_creature(forbidden_card, controller=0)
check("2e: face starts at 0", forbidden_creature_2e.face == 0)
flip_forbidden(forbidden_creature_2e)
check("2e: face toggles to 1 after flip", forbidden_creature_2e.face == 1)

# 2f) Forbidden flip triggered when creature leaves battle zone (via move_battle_to_graveyard)
from engine.zone_mover import move_battle_to_graveyard
state_2f = make_state(p0_battle=[forbidden_creature])
forbidden_creature.face = 0  # reset
forbidden_creature.temp_flags.clear()  # clear flags
gy = move_battle_to_graveyard(state_2f, 0, forbidden_creature.uid)
check("2f: Forbidden flip triggered on leave battle zone",
      forbidden_creature.temp_flags.get("_forbidden_flipped") == True)


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: GR Summon from Ultra GR (Rule 811)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 3: GR SUMMON FROM ULTRA GR (Rule 811)")
print("─" * 60)

# 3a) find_in_ultra_gr finds a card by card_id
gr_card = card(300, "GR Warrior", card_subtype=CardSubtype.GR, power=7000)
state_3a = make_state(p0_ultra_gr=[gr_card])
found = state_3a.find_in_ultra_gr(0, 300)
check("3a: find_in_ultra_gr() finds a card by card_id",
      found is not None and found.id == 300)

# 3b) find_in_ultra_gr returns None for missing cards
not_found = state_3a.find_in_ultra_gr(0, 999)
check("3b: find_in_ultra_gr() returns None for missing card",
      not_found is None)

# 3c) move_ultra_gr_to_battle removes card from ultra_gr_zone
gr_card_3c = card(301, "GR Mage", card_subtype=CardSubtype.GR, power=4000)
state_3c = make_state(p0_ultra_gr=[gr_card_3c])
gr_creature = move_ultra_gr_to_battle(state_3c, 0, gr_card_3c)
check("3c: move_ultra_gr_to_battle removes card from ultra_gr_zone",
      len(state_3c.players[0].ultra_gr_zone) == 0)

# 3d) move_ultra_gr_to_battle adds creature to battle_zone
check("3d: move_ultra_gr_to_battle adds creature to battle_zone",
      len(state_3c.players[0].battle_zone) == 1)

# 3e) move_ultra_gr_to_battle sets summoning sickness
check("3e: move_ultra_gr_to_battle sets summoning_sickness=True",
      gr_creature.has_summoning_sickness == True)

# 3f) GR creature in Ultra GR zone has correct card_id
check("3f: GR creature has correct card_id",
      gr_card_3c.card_subtype == CardSubtype.GR and gr_card_3c.id == 301)


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: Zerom Ritual (Rule 812)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 4: ZEROM RITUAL (Rule 812)")
print("─" * 60)

# 4a) CardSubtype.ZEROM exists in enum
check("4a: CardSubtype.ZEROM exists", CardSubtype.ZEROM is not None)

# 4b) is_zerom returns True for ZEROM subtype
zerom_card = card(400, "Zerom Ritual", card_subtype=CardSubtype.ZEROM, power=3000)
check("4b: is_zerom() returns True for ZEROM subtype",
      is_zerom(zerom_card) == True)

# 4c) is_zerom returns False for normal cards
check("4c: is_zerom() returns False for normal card",
      is_zerom(normal_card) == False)

# 4d) is_zerom_creature returns True when _zerom_flipped flag is set
zerom_creature = make_creature(zerom_card, controller=0)
zerom_creature.temp_flags["_zerom_flipped"] = True
check("4d: is_zerom_creature() returns True when _zerom_flipped is set",
      is_zerom_creature(zerom_creature) == True)

# 4e) move_zerom_to_battle creates creature with _zerom_flipped flag
state_4e = make_state()
zerom_creature_4e = move_zerom_to_battle(state_4e, 0, zerom_card)
check("4e: move_zerom_to_battle sets _zerom_flipped flag",
      zerom_creature_4e.temp_flags.get("_zerom_flipped") == True,
      f"flag={zerom_creature_4e.temp_flags.get('_zerom_flipped')}")

# 4f) EffectAction.ZEROM_RITUAL and ZEROM_FLIP exist in enum
check("4f: EffectAction.ZEROM_RITUAL exists", EffectAction.ZEROM_RITUAL is not None)
check("4f: EffectAction.ZEROM_FLIP exists", EffectAction.ZEROM_FLIP is not None)


# ─────────────────────────────────────────────────────────────────────────────
# Section 5: Hyper Mode Swap (Rule 816)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 5: HYPER MODE SWAP (Rule 816)")
print("─" * 60)

# 5a) is_hyper_mode returns True for cards with other_face_id and HYPERIZE keyword
hyper_card = card(500, "Hyper Dragon", power=8000,
                  other_face_id=501, keywords=[Keyword.HYPERIZE])
check("5a: is_hyper_mode() returns True for card with HYPERIZE keyword",
      is_hyper_mode(hyper_card) == True)

# 5b) is_hyper_mode returns False for normal cards
check("5b: is_hyper_mode() returns False for normal card",
      is_hyper_mode(normal_card) == False)

# 5c) swap_hyper_mode sets hyper_mode_released=True
hyper_creature = make_creature(hyper_card, controller=0)
swap_hyper_mode(hyper_creature)
check("5c: swap_hyper_mode sets hyper_mode_released=True",
      hyper_creature.hyper_mode_released == True)

# 5d) swap_hyper_mode is idempotent (calling twice doesn't change further)
hyper_creature_5d = make_creature(hyper_card, controller=0)
swap_hyper_mode(hyper_creature_5d)
released_after_first = hyper_creature_5d.hyper_mode_released
swap_hyper_mode(hyper_creature_5d)
check("5d: swap_hyper_mode is idempotent",
      hyper_creature_5d.hyper_mode_released == released_after_first,
      f"both calls result in hyper_mode_released={hyper_creature_5d.hyper_mode_released}")

# 5e) _do_hyperize sets hyper_mode_released flag on creature
from engine.effect_executor import _do_hyperize
from core.state import PendingTrigger
hyper_creature_5e = make_creature(hyper_card, controller=0)
hyper_creature_5e.hyper_mode_released = False
state_5e = make_state(p0_battle=[hyper_creature_5e])
trigger_5e = PendingTrigger(
    effect=CardEffect(
        card_id=500, ability_index=0, raw_text="hyperize",
        effect_type=None, trigger_event=None, effect_action=EffectAction.HYPERIZE,
        trigger_condition={}, effect_target={}, effect_value={},
        is_optional=False, is_replacement=False,
        active_in_phase=(), active_in_zone=(), parse_confidence=1.0,
    ),
    source_uid=hyper_creature_5e.uid,
    source_card_id=500,
    controller=0,
    trigger_data={"creature_uid": hyper_creature_5e.uid},
)
_do_hyperize(state_5e, 0, trigger_5e)
check("5e: _do_hyperize sets hyper_mode_released flag",
      hyper_creature_5e.hyper_mode_released == True,
      f"got {hyper_creature_5e.hyper_mode_released}")


# ─────────────────────────────────────────────────────────────────────────────
# Section 6: Star Evolution Uniqueness SBA (Rule 813)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 6: STAR EVOLUTION UNIQUENESS SBA (Rule 813)")
print("─" * 60)

# 6a) Two Star Evolution creatures with same card_id → one sent to graveyard
star_card = card(600, "Star Evolver", power=5000)
star_creature_a = make_creature(star_card, controller=0, entered_turn=2)
star_creature_a.temp_flags["_is_star_evolution"] = True
star_creature_b = make_creature(star_card, controller=0, entered_turn=3)
star_creature_b.temp_flags["_is_star_evolution"] = True
state_6a = make_state(p0_battle=[star_creature_a, star_creature_b])
check_state_based_actions(state_6a)
# After the first SBA check, verify by running directly
state_6a_direct = make_state(p0_battle=[star_creature_a, star_creature_b])
from engine.sba_checker import _sba_star_evolution_uniqueness
_sba_star_evolution_uniqueness(state_6a_direct)
remaining_ids = [c.definition.id for c in state_6a_direct.players[0].battle_zone]
gy_ids = [g.definition.id for g in state_6a_direct.players[0].graveyard]
check("6a: Two same-id Star Evos → one sent to graveyard",
      len(remaining_ids) == 1 and 600 in gy_ids,
      f"battle={remaining_ids}, gy={gy_ids}")

# 6b) Two Star Evolution creatures with different card_id → both stay
star_card_2 = card(601, "Star Evolver II", power=4000)
star_creature_c = make_creature(star_card_2, controller=0, entered_turn=3)
star_creature_c.temp_flags["_is_star_evolution"] = True
state_6b = make_state(p0_battle=[
    make_creature(star_card, controller=0, entered_turn=2, temp_flags={"_is_star_evolution": True}),
    star_creature_c,
])
_sba_star_evolution_uniqueness(state_6b)
count_6b = len(state_6b.players[0].battle_zone)
check("6b: Two different-id Star Evos → both stay",
      count_6b == 2, f"battle_zone has {count_6b}")

# 6c) Non-star-evolution creatures are unaffected
normal_creature_a = make_creature(card(602, "Normal A", power=2000), controller=0)
normal_creature_b = make_creature(card(602, "Normal A", power=2000), controller=0)
state_6c = make_state(p0_battle=[normal_creature_a, normal_creature_b])
_sba_star_evolution_uniqueness(state_6c)
count_6c = len(state_6c.players[0].battle_zone)
check("6c: Non-star-evolution creatures unaffected",
      count_6c == 2, f"battle_zone has {count_6c}")


# ─────────────────────────────────────────────────────────────────────────────
# Section 7: Dream Rare Uniqueness SBA (Rule 817)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 7: DREAM RARE UNIQUENESS SBA (Rule 817)")
print("─" * 60)

# 7a) Two Dream Rare creatures with same card_id → one sent to graveyard
dream_card = card(700, "Dream Phantom", card_subtype=CardSubtype.DREAM, power=6000)
dream_creature_a = make_creature(dream_card, controller=0, entered_turn=2)
dream_creature_b = make_creature(dream_card, controller=0, entered_turn=3)
state_7a = make_state(p0_battle=[dream_creature_a, dream_creature_b])
from engine.sba_checker import _sba_dream_rare_uniqueness
_sba_dream_rare_uniqueness(state_7a)
remaining_7a = [c.definition.id for c in state_7a.players[0].battle_zone]
gy_7a = [g.definition.id for g in state_7a.players[0].graveyard]
check("7a: Two same-id Dream Rares → one sent to graveyard",
      len(remaining_7a) == 1 and 700 in gy_7a,
      f"battle={remaining_7a}, gy={gy_7a}")

# 7b) Two Dream Rare creatures with different card_id → both stay
dream_card_2 = card(701, "Dream Phantom II", card_subtype=CardSubtype.DREAM, power=5000)
dream_creature_c = make_creature(dream_card_2, controller=0, entered_turn=3)
state_7b = make_state(p0_battle=[
    make_creature(dream_card, controller=0, entered_turn=2),
    dream_creature_c,
])
_sba_dream_rare_uniqueness(state_7b)
count_7b = len(state_7b.players[0].battle_zone)
check("7b: Two different-id Dream Rares → both stay",
      count_7b == 2, f"battle_zone has {count_7b}")

# 7c) Non-dream-rare creatures are unaffected
non_dream_a = make_creature(card(702, "Regular A", power=2000), controller=0)
non_dream_b = make_creature(card(702, "Regular A", power=2000), controller=0)
state_7c = make_state(p0_battle=[non_dream_a, non_dream_b])
_sba_dream_rare_uniqueness(state_7c)
count_7c = len(state_7c.players[0].battle_zone)
check("7c: Non-dream-rare creatures unaffected",
      count_7c == 2, f"battle_zone has {count_7c}")


# ─────────────────────────────────────────────────────────────────────────────
# Section 8: Duel Mate Cleanup SBA (Rule 820)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 8: DUEL MATE CLEANUP SBA (Rule 820)")
print("─" * 60)

# 8a) CardSubtype.DUEL_MATE exists in enum
check("8a: CardSubtype.DUEL_MATE exists", CardSubtype.DUEL_MATE is not None)

# 8b) is_duel_mate returns True for DUEL_MATE subtype
duel_mate_card = card(800, "Duel Mate", card_subtype=CardSubtype.DUEL_MATE, power=3000)
check("8b: is_duel_mate() returns True for DUEL_MATE subtype",
      is_duel_mate(duel_mate_card) == True)

# 8c) Duel Mate creature in battle zone is cleaned up by SBA
duel_mate_creature = make_creature(duel_mate_card, controller=0, entered_turn=2)
duel_mate_creature.temp_flags["_duel_mate"] = True
duel_mate_creature.has_summoning_sickness = False  # not properly summoned
state_8c = make_state(p0_battle=[duel_mate_creature])
from engine.sba_checker import _sba_duel_mate_cleanup
_sba_duel_mate_cleanup(state_8c)
battle_count = len(state_8c.players[0].battle_zone)
hyper_count = len(state_8c.players[0].hyperspatial_zone)
check("8c: Duel Mate in battle zone cleaned up by SBA",
      battle_count == 0 and hyper_count >= 1,
      f"battle={battle_count}, hyperspatial={hyper_count}")


# ─────────────────────────────────────────────────────────────────────────────
# Section 9: G-Castle Shield Behavior (Rule 822)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  SECTION 9: G-CASTLE SHIELD BEHAVIOR (Rule 822)")
print("─" * 60)

# 9a) CardSubtype.G_CASTLE exists in enum
check("9a: CardSubtype.G_CASTLE exists", CardSubtype.G_CASTLE is not None)

# 9b) is_g_castle returns True for G_CASTLE subtype
g_castle_card = card(900, "G-Castle Fortress", card_subtype=CardSubtype.G_CASTLE)
check("9b: is_g_castle() returns True for G_CASTLE subtype",
      is_g_castle(g_castle_card) == True)

# 9c) G-Castle broken from shield goes to graveyard (not hand)
g_castle_shield = ShieldCard(definition=g_castle_card)
normal_shield = ShieldCard(definition=card(901, "Normal Shield", card_type=CardType.CREATURE))
state_9c = make_state(p0_shields=[normal_shield, g_castle_shield])
# Move shield to standby (simulating shield break)
broken = move_shield_to_standby(state_9c, 0, 1)  # break index 1 (g_castle_shield)
# Move from standby to hand — G-Castle should go to graveyard, not hand
result_card = move_standby_shield_to_hand(state_9c, 0, g_castle_shield.uid)
gy_9c = [g.died_from for g in state_9c.players[0].graveyard]
check("9c: G-Castle broken from shield goes to graveyard",
      "g_castle" in str(gy_9c),
      f"graveyard died_from={gy_9c}")


# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
