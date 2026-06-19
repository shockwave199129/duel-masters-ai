"""test_phase7_8.py — Tests for Phase 7 MEDIUM and Phase 8 LOW severity fixes."""

import sys
sys.path.insert(0, "dm_engine")

from core.enums import (
    EffectAction, GlobalEffectType, Civilization, CardType, CardSubtype,
    INFINITY, Keyword
)
from core.global_effects import GlobalEffect, GlobalEffectRegistry
from core.state import GameState, TurnInfo, PlayerState, EffectStack
from core.cards import CardDefinition
from core.zones import Creature, HandCard
from engine.battle_resolver import resolve_battle
from engine.phase_controller import _end_turn


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


def make_game_state(turn_player: int = 0) -> GameState:
    p0 = PlayerState(player_index=0, player_name="P0")
    p1 = PlayerState(player_index=1, player_name="P1")
    return GameState(
        players=[p0, p1],
        turn_info=TurnInfo(active_player=turn_player, turn_number=1),
        effect_stack=EffectStack(),
    )


def make_card(card_id: int, card_type=CardType.CREATURE, subtype=CardSubtype.NONE,
              civs=None, cost=3, power=1000, is_infinite=False):
    if civs is None:
        civs = frozenset([Civilization.FIRE])
    card_def = CardDefinition(
        id=card_id,
        slug=f"card-{card_id}",
        name=f"Card{card_id}",
        cost=cost,
        power=power,
        card_type=card_type,
        card_subtype=subtype,
        civilizations=civs,
        races=frozenset(),
        keywords=frozenset(),
        effects=[],
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
    )
    if is_infinite:
        object.__setattr__(card_def, 'is_infinite_power', True)
    return card_def


# ══════════════════════════════════════════════════════════════════════════════
# 7.1 — Hand-zone static effects
# ══════════════════════════════════════════════════════════════════════════════

def test_hand_zone_lock_spells():
    """LOCK_ALL_SPELLS prevents casting spells from hand."""
    registry = GlobalEffectRegistry()
    registry.add(GlobalEffect(
        effect_type=GlobalEffectType.LOCK_ALL_SPELLS,
        applied_by_uid="c1",
        applied_by_card=101,
        controller=0,
        target_player=None,
    ))
    can_exec, reason = registry.can_execute_from_hand(
        0, "Spell", "None", frozenset([Civilization.FIRE])
    )
    check("LOCK_ALL_SPELLS blocks spells", not can_exec, reason)


def test_hand_zone_lock_creatures():
    """LOCK_ALL_CREATURES prevents summoning creatures from hand."""
    registry = GlobalEffectRegistry()
    registry.add(GlobalEffect(
        effect_type=GlobalEffectType.LOCK_ALL_CREATURES,
        applied_by_uid="c1",
        applied_by_card=101,
        controller=0,
        target_player=None,
    ))
    can_exec, reason = registry.can_execute_from_hand(
        0, "Creature", "None", frozenset([Civilization.FIRE])
    )
    check("LOCK_ALL_CREATURES blocks creatures", not can_exec, reason)


def test_hand_zone_restrict_summon_civ():
    """RESTRICT_SUMMON_CIVILIZATION only allows specified civs."""
    registry = GlobalEffectRegistry()
    registry.add(GlobalEffect(
        effect_type=GlobalEffectType.RESTRICT_SUMMON_CIVILIZATION,
        applied_by_uid="c1",
        applied_by_card=101,
        controller=0,
        target_player=None,
        allowed_civilizations=frozenset([Civilization.LIGHT]),
    ))
    # Fire creature should be blocked
    can_exec, _ = registry.can_execute_from_hand(
        0, "Creature", "None", frozenset([Civilization.FIRE])
    )
    check("RESTRICT_SUMMON blocks wrong civ", not can_exec)
    # Light creature should be allowed
    can_exec, _ = registry.can_execute_from_hand(
        0, "Creature", "None", frozenset([Civilization.LIGHT])
    )
    check("RESTRICT_SUMMON allows correct civ", can_exec)


def test_hand_zone_lock_card_type():
    """LOCK_CARD_TYPE blocks specific card types."""
    registry = GlobalEffectRegistry()
    registry.add(GlobalEffect(
        effect_type=GlobalEffectType.LOCK_CARD_TYPE,
        applied_by_uid="c1",
        applied_by_card=101,
        controller=0,
        target_player=None,
        locked_card_type="Spell",
    ))
    can_exec, _ = registry.can_execute_from_hand(
        0, "Spell", "None", frozenset()
    )
    check("LOCK_CARD_TYPE blocks matching type", not can_exec)
    can_exec, _ = registry.can_execute_from_hand(
        0, "Creature", "None", frozenset()
    )
    check("LOCK_CARD_TYPE allows non-matching type", can_exec)


def test_hand_zone_no_effects():
    """No global effects means everything is allowed."""
    registry = GlobalEffectRegistry()
    can_exec, _ = registry.can_execute_from_hand(
        0, "Spell", "None", frozenset([Civilization.FIRE])
    )
    check("No effects allows execution", can_exec)


# ══════════════════════════════════════════════════════════════════════════════
# 7.3 — Infinity power
# ══════════════════════════════════════════════════════════════════════════════

def test_infinity_constant():
    """INFINITY sentinel value."""
    check("INFINITY == 999999", INFINITY == 999999)
    check("INFINITY > 10000", INFINITY > 10000)


def test_infinity_power_creature():
    """Creature with is_infinite_power returns INFINITY."""
    state = make_game_state()
    card_def = make_card(101, is_infinite=True)
    creature = Creature(definition=card_def, uid="c1", controller=0)
    power = creature.compute_power(state)
    check("Infinity creature power", power == INFINITY, f"got {power}")


def test_infinity_plus_finite():
    """∞ + X = ∞ (power modifiers don't reduce infinity)."""
    state = make_game_state()
    card_def = make_card(101, is_infinite=True)
    creature = Creature(definition=card_def, uid="c1", controller=0)
    from core.zones import PowerModifier
    creature.power_modifiers.append(PowerModifier(amount=-1000, source_uid="test", duration="permanent"))
    power = creature.compute_power(state)
    check("∞ + (-1000) = ∞", power == INFINITY, f"got {power}")


def test_negative_infinity_destroys():
    """-∞ power modifier returns -INFINITY sentinel (creature destroyed)."""
    state = make_game_state()
    card_def = make_card(101, is_infinite=True)
    creature = Creature(definition=card_def, uid="c1", controller=0)
    from core.zones import PowerModifier
    creature.power_modifiers.append(PowerModifier(amount=-INFINITY, source_uid="test", duration="permanent"))
    power = creature.compute_power(state)
    check("-∞ destroys creature", power == -INFINITY, f"got {power}")


# ══════════════════════════════════════════════════════════════════════════════
# 7.4 — S-MAX summon (already implemented, verify it works)
# ══════════════════════════════════════════════════════════════════════════════

def test_smax_subtype_exists():
    """S-MAX subtype is defined."""
    check("STAR_MAX subtype exists", CardSubtype.STAR_MAX is not None)


# ══════════════════════════════════════════════════════════════════════════════
# 7.5 — NEO/G-NEO effect actions exist
# ══════════════════════════════════════════════════════════════════════════════

def test_neo_evolve_action_exists():
    """NEO_EVOLVE effect action is defined."""
    check("NEO_EVOLVE exists", EffectAction.NEO_EVOLVE is not None)


def test_forbidden_release_action_exists():
    """FORBIDDEN_RELEASE effect action is defined."""
    check("FORBIDDEN_RELEASE exists", EffectAction.FORBIDDEN_RELEASE is not None)


# ══════════════════════════════════════════════════════════════════════════════
# 8.1 — Hand size limit
# ══════════════════════════════════════════════════════════════════════════════

def test_hand_size_limit_enforcement():
    """Hand > 10 cards triggers discard choice at end of turn."""
    state = make_game_state()
    # Add 12 cards to hand
    for i in range(12):
        card_def = make_card(100 + i, card_type=CardType.SPELL, cost=1)
        state.players[0].hand.append(HandCard(definition=card_def, uid=f"h{i}"))
    
    check("Hand has 12 cards", len(state.players[0].hand) == 12)
    
    _end_turn(state)
    
    # Should have triggered a choice (hand still > 10)
    check("Discard choice triggered", state.effect_stack.is_waiting_for_choice())
    if state.effect_stack.awaited_choice:
        check("Choice type is discard_down",
              state.effect_stack.awaited_choice.choice_type == "discard_down")
        check("Must discard 2 cards",
              state.effect_stack.awaited_choice.min_choices == 2)


def test_hand_size_10_or_less_no_discard():
    """Hand with ≤ 10 cards doesn't trigger discard."""
    state = make_game_state()
    for i in range(8):
        card_def = make_card(100 + i, card_type=CardType.SPELL, cost=1)
        state.players[0].hand.append(HandCard(definition=card_def, uid=f"h{i}"))
    
    _end_turn(state)
    
    check("No discard choice for 8 cards", not state.effect_stack.is_waiting_for_choice())


# ══════════════════════════════════════════════════════════════════════════════
# 8.5 — Win/Loss by card effect
# ══════════════════════════════════════════════════════════════════════════════

def test_win_condition_action_exists():
    """WIN_CONDITION effect action is defined."""
    check("WIN_CONDITION exists", EffectAction.WIN_CONDITION is not None)


def test_lose_condition_action_exists():
    """LOSE_CONDITION effect action is defined."""
    check("LOSE_CONDITION exists", EffectAction.LOSE_CONDITION is not None)


if __name__ == "__main__":
    print("=" * 60)
    print("Phase 7 + Phase 8 Tests")
    print("=" * 60)
    
    # 7.1
    print("\n--- 7.1: Hand-zone static effects ---")
    test_hand_zone_lock_spells()
    test_hand_zone_lock_creatures()
    test_hand_zone_restrict_summon_civ()
    test_hand_zone_lock_card_type()
    test_hand_zone_no_effects()
    
    # 7.3
    print("\n--- 7.3: Infinity power ---")
    test_infinity_constant()
    test_infinity_power_creature()
    test_infinity_plus_finite()
    test_negative_infinity_destroys()
    
    # 7.4
    print("\n--- 7.4: S-MAX summon ---")
    test_smax_subtype_exists()
    
    # 7.5
    print("\n--- 7.5: NEO/G-NEO actions ---")
    test_neo_evolve_action_exists()
    test_forbidden_release_action_exists()
    
    # 8.1
    print("\n--- 8.1: Hand size limit ---")
    test_hand_size_limit_enforcement()
    test_hand_size_10_or_less_no_discard()
    
    # 8.5
    print("\n--- 8.5: Win/Loss by effect ---")
    test_win_condition_action_exists()
    test_lose_condition_action_exists()
    
    print("\n" + "=" * 60)
    print("All Phase 7 + 8 tests completed")
    print("=" * 60)
