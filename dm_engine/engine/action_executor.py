"""
engine/action_executor.py — apply one Action to a copied GameState.
"""

from __future__ import annotations

from core.actions import Action, actions_equal
from core.enums import ActionType, Phase
from core.state import AttackContext, GameState
from engine.action_generator import get_legal_actions
from engine.battle_resolver import resolve_battle
from engine.phase_controller import advance_phase
from engine.sba_checker import check_state_based_actions
from engine.shield_resolver import mark_direct_attack_if_applicable, resolve_shield_break_choice
from engine.zone_mover import (
    cross_gear_to_creature,
    fortify_shield_with_castle,
    move_hand_to_battle,
    move_hand_to_graveyard,
    move_hand_to_mana,
    move_standby_shield_to_hand,
    tap_mana_for_payment,
)


def execute_action(state: GameState, action: Action, db=None, validate: bool = True) -> GameState:
    """
    Apply one action and return a new GameState.

    This is the main `(GameState, Action) -> GameState` entry point. The
    implementation is intentionally incremental: complex effects are delegated
    to resolver modules as they are added.
    """
    if state.is_terminal():
        return state.copy()

    if validate and not _is_legal_action(state, action, db):
        raise ValueError(f"Illegal action for current state: {action}")

    s = state.copy()
    action_type = action.action_type

    if action_type == ActionType.PASS:
        step = dict(action.extra).get("step")
        if step == "shield_to_hand" and s.effect_stack.shield_trigger_queue:
            shield_player, shield = s.effect_stack.shield_trigger_queue[0]
            move_standby_shield_to_hand(s, shield_player, shield.uid)
            return check_state_based_actions(s)
        if s.current_phase == Phase.BATTLE:
            return resolve_battle(s)
        if s.current_phase == Phase.DIRECT_ATTACK:
            mark_direct_attack_if_applicable(s)
        advance_phase(s, action)

    elif action_type == ActionType.CHARGE_MANA:
        _require_card_uid(action)
        move_hand_to_mana(s, action.player, action.card_uid)
        s.record_action(action_type, action.player, action.card_id)

    elif action_type == ActionType.SUMMON_CREATURE:
        _require_card_uid(action)
        tap_mana_for_payment(s, action.player, action.mana_used)
        creature = move_hand_to_battle(
            s,
            action.player,
            action.card_uid,
            evolution_base_uid=action.evolution_base_uid,
        )
        s.record_action(action_type, action.player, action.card_id, creature.uid)

    elif action_type == ActionType.CAST_SPELL:
        _require_card_uid(action)
        tap_mana_for_payment(s, action.player, action.mana_used)
        move_hand_to_graveyard(s, action.player, action.card_uid, reason="cast")
        s.record_action(action_type, action.player, action.card_id)

    elif action_type in (
        ActionType.GENERATE_CROSS_GEAR,
        ActionType.DEPLOY_FIELD,
        ActionType.EXECUTE_TAMASEED,
    ):
        _require_card_uid(action)
        tap_mana_for_payment(s, action.player, action.mana_used)
        battle_card = move_hand_to_battle(s, action.player, action.card_uid)
        battle_card.has_summoning_sickness = False
        s.record_action(action_type, action.player, action.card_id, battle_card.uid)

    elif action_type == ActionType.CROSS_GEAR:
        _require_card_uid(action)
        if not action.target_uid:
            raise ValueError("CROSS_GEAR requires target_uid")
        tap_mana_for_payment(s, action.player, action.mana_used)
        cross_gear_to_creature(s, action.player, action.card_uid, action.target_uid)
        s.record_action(action_type, action.player, action.card_id, action.target_uid)

    elif action_type == ActionType.FORTIFY_CASTLE:
        _require_card_uid(action)
        if not action.target_uid:
            raise ValueError("FORTIFY_CASTLE requires target_uid")
        tap_mana_for_payment(s, action.player, action.mana_used)
        fortify_shield_with_castle(s, action.player, action.card_uid, action.target_uid)
        s.record_action(action_type, action.player, action.card_id, action.target_uid)

    elif action_type == ActionType.USE_G_ZERO:
        _require_card_uid(action)
        creature = move_hand_to_battle(s, action.player, action.card_uid)
        s.record_action(action_type, action.player, action.card_id, creature.uid)

    elif action_type in (ActionType.ATTACK_PLAYER, ActionType.ATTACK_CREATURE):
        _declare_attack(s, action)

    elif action_type in (ActionType.DECLARE_BLOCKER, ActionType.DECLARE_GUARDMAN):
        _declare_block(s, action)

    elif action_type == ActionType.USE_SHIELD_TRIGGER:
        _resolve_shield_trigger_choice(s, action)

    elif action_type == ActionType.USE_G_STRIKE:
        _resolve_g_strike_choice(s, action)

    elif action_type == ActionType.USE_S_BACK:
        _resolve_s_back(s, action)

    elif action_type == ActionType.SELECT_ATTACK_ORDER:
        if action.shield_index is None:
            raise ValueError("SELECT_ATTACK_ORDER requires shield_index")
        return resolve_shield_break_choice(s, action.shield_index)

    elif action_type == ActionType.USE_NINJA_STRIKE:
        _require_card_uid(action)
        creature = move_hand_to_battle(s, action.player, action.card_uid)
        if s.attack_context:
            s.attack_context.ninja_strike_used = True
            s.attack_context.ninja_strike_card_uid = creature.uid
        s.record_action(action_type, action.player, action.card_id, creature.uid)

    elif action_type == ActionType.HYPERIZE:
        creature = s.players[action.player].find_creature(action.card_uid or "")
        if creature is None:
            raise ValueError("Hyperize source not found")
        creature.hyper_mode_released = True
        s.record_action(action_type, action.player, action.card_id, creature.uid)

    else:
        # Selection and advanced free-execution actions are handled by effect
        # and shield resolvers as those systems are filled in.
        s.record_action(action_type, action.player, action.card_id, action.target_uid)

    return check_state_based_actions(s)


def _is_legal_action(state: GameState, action: Action, db=None) -> bool:
    return any(actions_equal(action, legal) for legal in get_legal_actions(state, db))


def _require_card_uid(action: Action) -> None:
    if not action.card_uid:
        raise ValueError(f"{action.action_type.value} requires card_uid")


def _declare_attack(state: GameState, action: Action) -> None:
    attacker = state.players[action.player].find_creature(action.card_uid or "")
    if attacker is None:
        raise ValueError("Attacking creature not found")
    attacker.tap()
    attacker.has_attacked_this_turn = True

    target_type = "player" if action.action_type == ActionType.ATTACK_PLAYER else "creature"
    state.attack_context = AttackContext(
        attacker_uid=attacker.uid,
        attacker_player=action.player,
        target_type=target_type,
        target_uid=action.target_uid,
    )
    state.turn_info.phase = Phase.ATTACK_DECLARE
    state.record_action(action.action_type, action.player, action.card_id, action.target_uid)


def _declare_block(state: GameState, action: Action) -> None:
    if state.attack_context is None:
        raise ValueError("No attack is in progress")
    blocker = state.players[action.player].find_creature(action.card_uid or "")
    if blocker is None:
        raise ValueError("Blocking creature not found")
    blocker.tap()
    blocker.is_blocking = True
    blocker.blocking_uid = state.attack_context.attacker_uid
    state.attack_context.blocker_uid = blocker.uid
    state.attack_context.blocker_player = action.player
    state.attack_context.block_was_declared = True
    state.turn_info.phase = Phase.BATTLE
    state.record_action(action.action_type, action.player, action.card_id, blocker.uid)


def _resolve_shield_trigger_choice(state: GameState, action: Action) -> None:
    if not action.card_uid:
        raise ValueError("Shield trigger action requires card_uid")
    if not action.choice:
        move_standby_shield_to_hand(state, action.player, action.card_uid)
        state.record_action(action.action_type, action.player, action.card_id)
        return

    # Minimal free execution: spells go to graveyard after resolution placeholder;
    # creatures enter the battle zone. Full effects are handled later.
    for idx, (queued_player, shield) in enumerate(state.effect_stack.shield_trigger_queue):
        if queued_player == action.player and shield.uid == action.card_uid:
            state.effect_stack.shield_trigger_queue.pop(idx)
            if shield.definition.is_creature():
                from core.zones import Creature
                creature = Creature(
                    definition=shield.definition,
                    controller=action.player,
                    owner=action.player,
                    entered_turn=state.turn_number,
                    has_summoning_sickness=True,
                )
                state.players[action.player].battle_zone.append(creature)
            else:
                from core.zones import GraveyardCard
                state.players[action.player].graveyard.insert(
                    0,
                    GraveyardCard(
                        definition=shield.definition,
                        uid=shield.uid,
                        died_from="shield_trigger",
                        died_on_turn=state.turn_number,
                    )
                )
            state.record_action(action.action_type, action.player, action.card_id)
            return
    raise ValueError(f"Standby shield {action.card_uid} not found")


def _resolve_g_strike_choice(state: GameState, action: Action) -> None:
    if not action.card_uid:
        raise ValueError("G-Strike action requires card_uid")
    move_standby_shield_to_hand(state, action.player, action.card_uid)
    state.record_action(action.action_type, action.player, action.card_id)


def _resolve_s_back(state: GameState, action: Action) -> None:
    if not action.card_uid or not action.discard_uid:
        raise ValueError("S-Back action requires card_uid and discard_uid")

    shield = None
    for idx, (queued_player, queued_shield) in enumerate(state.effect_stack.shield_trigger_queue):
        if queued_player == action.player and queued_shield.uid == action.discard_uid:
            shield = queued_shield
            state.effect_stack.shield_trigger_queue.pop(idx)
            break
    if shield is None:
        raise ValueError(f"Standby shield {action.discard_uid} not found")

    from core.zones import GraveyardCard
    state.players[action.player].graveyard.insert(
        0,
        GraveyardCard(
            definition=shield.definition,
            uid=shield.uid,
            died_from="s_back_discard",
            died_on_turn=state.turn_number,
        ),
    )

    s_back_card = state.players[action.player].find_in_hand(action.card_uid)
    if s_back_card is None:
        raise ValueError(f"S-Back card {action.card_uid} not found in hand")
    if s_back_card.definition.is_creature():
        creature = move_hand_to_battle(state, action.player, action.card_uid)
        state.record_action(action.action_type, action.player, action.card_id, creature.uid)
    else:
        move_hand_to_graveyard(state, action.player, action.card_uid, reason="s_back")
        state.record_action(action.action_type, action.player, action.card_id)
