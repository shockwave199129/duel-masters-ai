"""
tests/test_missing_sbas.py — Tests for newly implemented SBAs (Rule 703.4e-4m).

Tests cover:
  - 703.4e: Cannot attack tap
  - 703.4f: Cross Gear standalone destroy
  - 703.4g: Aura/Fortress standalone destroy
  - 703.4m: Weapon standalone destroy
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.cards import CardDefinition
from core.enums import (
    CardType, CardSubtype, Civilization, Keyword, Phase,
)
from core.player_state import PlayerState
from core.state import GameState, TurnInfo
from core.zones import Creature, HandCard
from engine.sba.missing_sbas import (
    _sba_cannot_attack_tap,
    _sba_cross_gear_standalone,
    _sba_aura_fortress_standalone,
    _sba_weapon_standalone,
)
from engine.sba.checker import check_state_based_actions

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))
    return ok


def card(cid, name, card_type=CardType.CREATURE, power=1000, keywords=None):
    return CardDefinition(
        id=cid,
        slug=name.lower().replace(" ", "_"),
        name=name,
        cost=3,
        power=power if card_type == CardType.CREATURE else None,
        card_type=card_type,
        card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]),
        races=frozenset(["Human"]) if card_type == CardType.CREATURE else frozenset(),
        keywords=frozenset(keywords or []),
        effects=tuple(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
    )


def make_game_state():
    """Create a basic game state for testing."""
    players = (
        PlayerState(player_index=0),
        PlayerState(player_index=1),
    )
    return GameState(
        players=players,
        turn_info=TurnInfo(turn_number=1, active_player=0),
    )


print("\n" + "═" * 70)
print("  MISSING SBAs (Rule 703.4e-4m) — COMPREHENSIVE TESTS")
print("═" * 70)

ALL_PASSED = True

# ════════════════════════════════════════════════════════════════════════════════
# Test 1: Cannot Attack Tap (Rule 703.4e)
# ════════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("  TEST 1: Cannot Attack Tap (Rule 703.4e)")
print("─" * 70)

state = make_game_state()
creature_def = card(101, "Blocker", keywords=[Keyword.CANNOT_ATTACK])
creature = Creature(
    definition=creature_def,
    controller=0,
    uid="c1",
    is_tapped=False,
)
state.players[0].battle_zone.append(creature)

# Before SBA: creature is not tapped
ALL_PASSED &= check(
    "Before SBA: cannot_attack creature is untapped",
    not creature.is_tapped,
)

# Run SBA
fired = _sba_cannot_attack_tap(state)
ALL_PASSED &= check(
    "SBA fires for cannot_attack creature",
    fired,
)

# After SBA: creature should be tapped
ALL_PASSED &= check(
    "After SBA: creature is tapped",
    creature.is_tapped,
)

# ════════════════════════════════════════════════════════════════════════════════
# Test 2: Cross Gear Standalone (Rule 703.4f)
# ════════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("  TEST 2: Cross Gear Standalone Destroy (Rule 703.4f)")
print("─" * 70)

state = make_game_state()
gear_def = card(102, "Cross Gear", card_type=CardType.CROSS_GEAR, power=None)
gear = Creature(
    definition=gear_def,
    controller=0,
    uid="gear1",
    attached_to_uid=None,  # Standalone!
)
state.players[0].battle_zone.append(gear)

# Before SBA: gear is in battle zone
ALL_PASSED &= check(
    "Before SBA: cross gear in battle zone",
    gear in state.players[0].battle_zone,
)

# Run SBA
fired = _sba_cross_gear_standalone(state)
ALL_PASSED &= check(
    "SBA fires for standalone cross gear",
    fired,
)

# After SBA: gear should be in graveyard
ALL_PASSED &= check(
    "After SBA: cross gear moved to graveyard",
    gear not in state.players[0].battle_zone,
    f"Graveyard count: {len(state.players[0].graveyard)}",
)

ALL_PASSED &= check(
    "Cross gear is in graveyard",
    len(state.players[0].graveyard) == 1,
)

# ════════════════════════════════════════════════════════════════════════════════
# Test 3: Aura Standalone (Rule 703.4g)
# ════════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("  TEST 3: Aura Standalone Destroy (Rule 703.4g)")
print("─" * 70)

state = make_game_state()
aura_def = card(103, "Aura", card_type=CardType.AURA, power=None)
aura = Creature(
    definition=aura_def,
    controller=0,
    uid="aura1",
    attached_to_uid=None,  # Standalone!
)
state.players[0].battle_zone.append(aura)

# Before SBA: aura is in battle zone
ALL_PASSED &= check(
    "Before SBA: aura in battle zone",
    aura in state.players[0].battle_zone,
)

# Run SBA
fired = _sba_aura_fortress_standalone(state)
ALL_PASSED &= check(
    "SBA fires for standalone aura",
    fired,
)

# After SBA: aura should be in graveyard
ALL_PASSED &= check(
    "After SBA: aura moved to graveyard",
    aura not in state.players[0].battle_zone,
)

# ════════════════════════════════════════════════════════════════════════════════
# Test 4: Fortress Standalone (Rule 703.4g)
# ════════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("  TEST 4: Fortress Standalone Destroy (Rule 703.4g)")
print("─" * 70)

state = make_game_state()
fortress_def = card(104, "Fortress", card_type=CardType.FORTRESS, power=None)
fortress = Creature(
    definition=fortress_def,
    controller=0,
    uid="fortress1",
    attached_to_uid=None,  # Standalone!
)
state.players[0].battle_zone.append(fortress)

# Run SBA
fired = _sba_aura_fortress_standalone(state)
ALL_PASSED &= check(
    "SBA fires for standalone fortress",
    fired,
)

# After SBA: fortress should be in graveyard
ALL_PASSED &= check(
    "After SBA: fortress moved to graveyard",
    fortress not in state.players[0].battle_zone,
)

# ════════════════════════════════════════════════════════════════════════════════
# Test 5: Weapon Standalone (Rule 703.4m)
# ════════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("  TEST 5: Weapon Standalone Destroy (Rule 703.4m)")
print("─" * 70)

state = make_game_state()
weapon_def = card(105, "Weapon", card_type=CardType.WEAPON, power=None)
weapon = Creature(
    definition=weapon_def,
    controller=0,
    uid="weapon1",
    attached_to_uid=None,  # Standalone!
)
state.players[0].battle_zone.append(weapon)

# Run SBA
fired = _sba_weapon_standalone(state)
ALL_PASSED &= check(
    "SBA fires for standalone weapon",
    fired,
)

# After SBA: weapon should be in graveyard
ALL_PASSED &= check(
    "After SBA: weapon moved to graveyard",
    weapon not in state.players[0].battle_zone,
)

# ════════════════════════════════════════════════════════════════════════════════
# Test 6: Attached Equipment NOT Destroyed
# ════════════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("  TEST 6: Attached Equipment Survives (Negative Test)")
print("─" * 70)

state = make_game_state()
base_def = card(106, "Base Creature", power=3000)
base = Creature(
    definition=base_def,
    controller=0,
    uid="base1",
)

gear_def = card(107, "Cross Gear", card_type=CardType.CROSS_GEAR, power=None)
gear = Creature(
    definition=gear_def,
    controller=0,
    uid="gear2",
    attached_to_uid="base1",  # Attached!
)

state.players[0].battle_zone.append(base)
state.players[0].battle_zone.append(gear)

# Run SBA
fired = _sba_cross_gear_standalone(state)
ALL_PASSED &= check(
    "SBA does not fire for attached cross gear",
    not fired,
)

# Gear should still be in battle zone
ALL_PASSED &= check(
    "Attached cross gear remains in battle zone",
    gear in state.players[0].battle_zone,
)

# ════════════════════════════════════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════════════════════════════════════
print("\n" + "═" * 70)
if ALL_PASSED:
    print("  ✅ ALL TESTS PASSED")
else:
    print("  ❌ SOME TESTS FAILED")
print("═" * 70 + "\n")

for name, passed, detail in results:
    status = PASS if passed else FAIL
    print(f"{status} {name}" + (f" — {detail}" if detail else ""))

sys.exit(0 if ALL_PASSED else 1)
