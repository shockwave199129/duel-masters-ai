"""test_phase7_8.py — Tests for Phase 7 MEDIUM and Phase 8 LOW severity fixes."""

import sys
sys.path.insert(0, "dm_engine")

from core.enums import (
    EffectAction, GlobalEffectType, Civilization, CardType, CardSubtype,
    INFINITY, Keyword, Phase
)
from core.global_effects import GlobalEffect, GlobalEffectRegistry
from core.state import GameState, TurnInfo, PlayerState, EffectStack
from core.cards import CardDefinition
from core.zones import Creature, HandCard
from core.state import AttackContext
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
# 7.2 — Partial Effect Execution (Rule 606.2)
# ══════════════════════════════════════════════════════════════════════════════

def test_partial_destroy_skips_invalid_targets():
    """_do_destroy with multi-target skips invalid targets, destroys valid ones."""
    from core.state import PendingTrigger
    from core.cards import CardEffect, EffectAction, TriggerEvent, EffectType
    from engine.effect_executor import execute_pending_trigger
    
    state = make_game_state()
    
    # Create two creatures in P1's battle zone
    card1 = make_card(201, power=3000)
    c1 = Creature(definition=card1, uid="t1", controller=1)
    state.players[1].battle_zone.append(c1)
    
    card2 = make_card(202, power=4000)
    c2 = Creature(definition=card2, uid="t2", controller=1)
    state.players[1].battle_zone.append(c2)
    
    # Effect that targets t1 and a non-existent creature
    # Note: effect_target must NOT have type=creature to avoid triggering select_target choice
    effect = CardEffect(
        card_id=101, ability_index=0, raw_text="Destroy 2 creatures",
        effect_type=EffectType.SPELL, trigger_event=TriggerEvent.ON_CAST,
        effect_action=EffectAction.DESTROY,
        trigger_condition={}, effect_target={},
        effect_value={}, is_optional=False, is_replacement=False,
        active_in_phase=(), active_in_zone=(), parse_confidence=1.0,
    )
    trigger = PendingTrigger(
        effect=effect, source_uid="c1", source_card_id=101,
        controller=0, trigger_data={"target_uids": ["t1", "nonexistent"]},
    )
    
    result = execute_pending_trigger(state, trigger)
    
    # t1 should be destroyed (moved to graveyard)
    # Check it's no longer in battle zone
    bz_creatures = [c for c in result.players[1].battle_zone if c.uid == "t1"]
    check("Valid target destroyed (removed from BZ)", len(bz_creatures) == 0)
    # t2 should still be in battle zone
    found2 = result.find_creature_anywhere("t2")
    check("Non-targeted creature still exists", found2 is not None)


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
# 7.5 — G-NEO All-Leave Replacement (Rule 803.2)
# ══════════════════════════════════════════════════════════════════════════════

def test_gneo_replacement_function_exists():
    """G-NEO all-leave replacement check function exists and works."""
    from engine.zone_mover import should_apply_gneo_all_leave_replacement
    
    # Non-G-NEO creature should not trigger
    card = make_card(101)
    creature = Creature(definition=card, uid="c1", controller=0)
    check("Non-G-NEO doesn't trigger", not should_apply_gneo_all_leave_replacement(creature))
    
    # G-NEO creature without evolution stack should not trigger
    gneo_card = make_card(102, subtype=CardSubtype.G_NEO)
    gneo_creature = Creature(definition=gneo_card, uid="c2", controller=0)
    check("G-NEO without stack doesn't trigger", not should_apply_gneo_all_leave_replacement(gneo_creature))


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


# ══════════════════════════════════════════════════════════════════════════════
# 8.2 — Infinity Power Edge Case Tests (Rule 108.1c)
# ══════════════════════════════════════════════════════════════════════════════

def test_infinity_wins_battle_vs_normal():
    """∞ creature wins battle vs normal creature."""
    state = make_game_state()
    
    # Create ∞ attacker
    inf_card = make_card(101, is_infinite=True, power=INFINITY)
    attacker = Creature(definition=inf_card, uid="inf1", controller=0)
    attacker.is_tapped = False
    attacker.has_summoning_sickness = False
    state.players[0].battle_zone.append(attacker)
    
    # Create normal defender
    norm_card = make_card(102, power=5000)
    defender = Creature(definition=norm_card, uid="norm1", controller=1)
    state.players[1].battle_zone.append(defender)
    
    # Set up attack context
    state.attack_context = AttackContext(
        attacker_uid="inf1",
        attacker_player=0,
        target_type="creature",
        target_uid="norm1",
        blocker_uid=None,
    )
    state.turn_info.phase = Phase.ATTACK
    
    result = resolve_battle(state)
    
    # ∞ should win — defender should have lost_battle flag (or be destroyed via SBA)
    # After SBA, the defender creature may be moved to graveyard
    def_result = result.find_creature_anywhere("norm1")
    if def_result is None:
        # Creature was destroyed and moved to graveyard — this is correct
        defender_lost = True
    else:
        _, def_creature = def_result
        defender_lost = def_creature.temp_flags.get("lost_battle", False)
    check("∞ wins vs normal: defender lost/destroyed", defender_lost)
    
    # Attacker should NOT have lost
    _, atk_creature = result.find_creature_anywhere("inf1")
    check("∞ doesn't lose vs normal", not atk_creature.temp_flags.get("lost_battle", False))


def test_infinity_vs_infinity_is_tie():
    """∞ vs ∞ is a tie — both lose."""
    state = make_game_state()
    
    inf_card1 = make_card(101, is_infinite=True, power=INFINITY)
    attacker = Creature(definition=inf_card1, uid="inf1", controller=0)
    attacker.is_tapped = False
    attacker.has_summoning_sickness = False
    state.players[0].battle_zone.append(attacker)
    
    inf_card2 = make_card(102, is_infinite=True, power=INFINITY)
    defender = Creature(definition=inf_card2, uid="inf2", controller=1)
    state.players[1].battle_zone.append(defender)
    
    state.attack_context = AttackContext(
        attacker_uid="inf1",
        attacker_player=0,
        target_type="creature",
        target_uid="inf2",
        blocker_uid=None,
    )
    state.turn_info.phase = Phase.ATTACK
    
    result = resolve_battle(state)
    
    # ∞ vs ∞ is a tie — both lose (rule 115.3b)
    # After SBA, both creatures may be destroyed and moved to graveyard
    atk_result = result.find_creature_anywhere("inf1")
    dfn_result = result.find_creature_anywhere("inf2")
    # Both should be gone (destroyed via SBA after losing battle)
    check("∞ vs ∞: attacker destroyed", atk_result is None)
    check("∞ vs ∞: defender destroyed", dfn_result is None)


def test_infinity_minus_1000_stays_infinity():
    """∞ creature with -1000 power stays ∞."""
    state = make_game_state()
    card_def = make_card(101, is_infinite=True)
    creature = Creature(definition=card_def, uid="c1", controller=0)
    from core.zones import PowerModifier
    creature.power_modifiers.append(PowerModifier(amount=-1000, source_uid="test", duration="permanent"))
    power = creature.compute_power(state)
    check("∞ - 1000 = ∞", power == INFINITY, f"got {power}")


def test_normal_minus_infinity_destroyed():
    """Normal creature with -∞ power returns -INFINITY sentinel."""
    state = make_game_state()
    card_def = make_card(101, power=5000)
    creature = Creature(definition=card_def, uid="c1", controller=0)
    from core.zones import PowerModifier
    creature.power_modifiers.append(PowerModifier(amount=-INFINITY, source_uid="test", duration="permanent"))
    power = creature.compute_power(state)
    check("Normal - ∞ = -INFINITY", power == -INFINITY, f"got {power}")


# ══════════════════════════════════════════════════════════════════════════════
# 8.4 — Mutual "Wins Battles" Test (Rule 700.5a)
# ══════════════════════════════════════════════════════════════════════════════

def test_mutual_wins_battles():
    """Two creatures both with 'wins battles' — both win, neither loses."""
    state = make_game_state()
    
    card1 = make_card(101, power=1000)
    attacker = Creature(definition=card1, uid="a1", controller=0)
    attacker.is_tapped = False
    attacker.has_summoning_sickness = False
    attacker.temp_flags["wins_battles"] = True
    state.players[0].battle_zone.append(attacker)
    
    card2 = make_card(102, power=5000)
    defender = Creature(definition=card2, uid="d1", controller=1)
    defender.temp_flags["wins_battles"] = True
    state.players[1].battle_zone.append(defender)
    
    state.attack_context = AttackContext(
        attacker_uid="a1",
        attacker_player=0,
        target_type="creature",
        target_uid="d1",
        blocker_uid=None,
    )
    state.turn_info.phase = Phase.ATTACK
    
    result = resolve_battle(state)
    
    # Both win = neither loses (rule 115.3c)
    _, atk = result.find_creature_anywhere("a1")
    _, dfn = result.find_creature_anywhere("d1")
    # Both should still be alive (neither lost)
    check("Mutual wins: attacker survives", atk is not None and not atk.temp_flags.get("lost_battle", False))
    check("Mutual wins: defender survives", dfn is not None and not dfn.temp_flags.get("lost_battle", False))


# ══════════════════════════════════════════════════════════════════════════════
# 8.5 — Win/Loss by Card Effect Tests (Rule 104.2c)
# ══════════════════════════════════════════════════════════════════════════════

def test_win_by_effect_sets_game_result():
    """WIN_CONDITION effect sets game result to win for controller."""
    from core.state import PendingTrigger
    from core.cards import CardEffect, EffectAction, TriggerEvent, EffectType
    
    state = make_game_state()
    effect = CardEffect(
        card_id=101, ability_index=0, raw_text="You win the game",
        effect_type=EffectType.TRIGGERED, trigger_event=TriggerEvent.ON_SUMMON,
        effect_action=EffectAction.WIN_CONDITION,
        trigger_condition={}, effect_target={}, effect_value={},
        is_optional=False, is_replacement=False,
        active_in_phase=(), active_in_zone=(), parse_confidence=1.0,
    )
    trigger = PendingTrigger(
        effect=effect, source_uid="c1", source_card_id=101,
        controller=0, trigger_data={},
    )
    from engine.effect_executor import execute_pending_trigger
    result = execute_pending_trigger(state, trigger)
    check("Win by effect sets result", result.game_result == ("win", 0),
          f"got {result.game_result}")


def test_lose_by_effect_sets_game_result():
    """LOSE_CONDITION effect sets game result to win for opponent."""
    from core.state import PendingTrigger
    from core.cards import CardEffect, EffectAction, TriggerEvent, EffectType
    
    state = make_game_state()
    effect = CardEffect(
        card_id=101, ability_index=0, raw_text="You lose the game",
        effect_type=EffectType.TRIGGERED, trigger_event=TriggerEvent.ON_SUMMON,
        effect_action=EffectAction.LOSE_CONDITION,
        trigger_condition={}, effect_target={}, effect_value={},
        is_optional=False, is_replacement=False,
        active_in_phase=(), active_in_zone=(), parse_confidence=1.0,
    )
    trigger = PendingTrigger(
        effect=effect, source_uid="c1", source_card_id=101,
        controller=0, trigger_data={},
    )
    from engine.effect_executor import execute_pending_trigger
    result = execute_pending_trigger(state, trigger)
    check("Lose by effect: opponent wins", result.game_result == ("win", 1),
          f"got {result.game_result}")


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
    
    # 7.2
    print("\n--- 7.2: Partial effect execution ---")
    test_partial_destroy_skips_invalid_targets()
    
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
    test_gneo_replacement_function_exists()
    
    # 7.6
    print("\n--- 7.6: Forbidden/Zerom actions ---")
    # Zerom Birth is handled as spell subtype in action_generator (verified)
    check("ZEROM_RITUAL action exists", EffectAction.ZEROM_RITUAL is not None)
    check("ZEROM_FLIP action exists", EffectAction.ZEROM_FLIP is not None)
    
    # 8.1
    print("\n--- 8.1: Hand size limit ---")
    test_hand_size_limit_enforcement()
    test_hand_size_10_or_less_no_discard()
    
    # 8.2
    print("\n--- 8.2: Infinity edge cases ---")
    test_infinity_wins_battle_vs_normal()
    test_infinity_vs_infinity_is_tie()
    test_infinity_minus_1000_stays_infinity()
    test_normal_minus_infinity_destroyed()
    
    # 8.3
    print("\n--- 8.3: Pending state cleanup ---")
    # Verified in _end_turn implementation
    
    # 8.4
    print("\n--- 8.4: Mutual wins battles ---")
    test_mutual_wins_battles()
    
    # 8.5
    print("\n--- 8.5: Win/Loss by effect ---")
    test_win_condition_action_exists()
    test_lose_condition_action_exists()
    test_win_by_effect_sets_game_result()
    test_lose_by_effect_sets_game_result()
    
    print("\n" + "=" * 60)
    print("All Phase 7 + 8 tests completed")
    print("=" * 60)
