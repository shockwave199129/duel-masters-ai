"""
engine/phase_controller.py — deterministic turn and phase advancement.
"""

from __future__ import annotations

from core.actions import Action
from core.card_scripts import HYPER_MODE
from core.enums import Phase, TriggerEvent
from core.state import GameState
from engine.zone_mover import draw_card


def advance_phase(state: GameState, action: Action | None = None) -> GameState:
    """
    Advance the copied GameState through automatic phase transitions.

    This handles the base turn skeleton. Attack sub-step transitions are refined
    by action execution and the battle/shield resolvers.
    """
    s = state
    phase = s.current_phase

    if phase == Phase.START_OF_TURN:
        _start_turn(s)
        s.turn_info.phase = Phase.DRAW
    elif phase == Phase.DRAW:
        if not s.turn_info.should_skip_draw():
            draw_card(s, s.active_player)
        s.turn_info.phase = Phase.MANA_CHARGE
    elif phase == Phase.MANA_CHARGE:
        s.turn_info.phase = Phase.MAIN
    elif phase == Phase.MAIN:
        s.turn_info.phase = Phase.ATTACK
    elif phase == Phase.ATTACK:
        s.turn_info.phase = Phase.END_OF_TURN
    elif phase == Phase.ATTACK_DECLARE:
        s.turn_info.phase = Phase.BLOCK_DECLARE
    elif phase == Phase.BLOCK_DECLARE:
        if s.attack_context and (s.attack_context.blocker_uid or s.attack_context.is_attacking_creature):
            s.turn_info.phase = Phase.BATTLE
        else:
            s.turn_info.phase = Phase.DIRECT_ATTACK
    elif phase in (Phase.BATTLE, Phase.DIRECT_ATTACK):
        s.turn_info.phase = Phase.END_OF_ATTACK
    elif phase == Phase.END_OF_ATTACK:
        # "While attacking" bonuses last no longer than the attack (rule 510.2)
        for each in s.players:
            for creature in each.battle_zone:
                creature.temp_flags.pop("power_attacker_active", None)
        s.attack_context = None
        s.turn_info.phase = Phase.ATTACK
    elif phase == Phase.END_OF_TURN:
        if not s.turn_info.turn_end_triggered:
            _trigger_turn_end(s)
            if s.effect_stack.pending_triggers:
                return s  # they resolve now; the turn ends on the next step
        _end_turn(s)

    # Re-apply static effects for creatures with phase restrictions
    # (Rule 110.3d: static abilities function continuously; some are phase-gated)
    for player_idx in (0, 1):
        for creature in s.players[player_idx].battle_zone:
            creature.reapply_static_effects(s)

    return s


def _pay_mana_debts(state: GameState, player: int) -> None:
    """A cost put off until now (Counterattack): pay it or lose the game."""
    from engine.turn.action_gen import _get_mana_combinations

    p_state = state.players[player]
    debts, p_state.mana_debts = p_state.mana_debts, []
    for amount, civilization in debts:
        combos = _get_mana_combinations(p_state.mana_zone, amount, frozenset({civilization}))
        if not combos:
            from engine.leave_rules import lose_game
            lose_game(state, player)
            return
        for usage in combos[0]:
            p_state.find_mana(usage.mana_uid).tap()


def _start_turn(state: GameState) -> None:
    player = state.active_player
    p_state = state.players[player]
    p_state.reset_turn_flags()
    for each in state.players:
        each.shields_broken_this_turn = 0
        each.graveyard_left_this_turn = False
        each.spells_cast_this_turn = 0
        each.creatures_put_this_turn = 0
        each.played_free_this_turn = False
        for creature in each.battle_zone:
            for flag in ("locked_until_turn_of", "blocker_until_turn_of"):
                if creature.temp_flags.get(flag) == player:
                    del creature.temp_flags[flag]
    p_state.untap_all()
    _pay_mana_debts(state, player)
    # Hyper Mode lasts until the start of its owner's next turn
    for creature in p_state.battle_zone:
        if creature.definition.slug in HYPER_MODE:
            creature.hyper_mode_released = False
    # Rule 301.5 / 506.1a: creatures present since before this turn began lose
    # summoning sickness now. This correctly includes creatures that entered
    # during the opponent's turn (Shield Trigger summons, Ninja Strike, etc.).
    for creature in p_state.battle_zone:
        if creature.entered_turn < state.turn_number:
            creature.clear_summoning_sickness()

    # Fire START_OF_TURN triggers
    from engine.trigger_registry import fire_trigger
    fire_trigger(state, TriggerEvent.START_OF_TURN, {
        "controller": player,
        "turn_number": state.turn_number,
    }, None)


def _return_abyss_rush(state: GameState, player: int) -> None:
    """Creatures summoned with Abyss Rush go to the bottom of the deck as the turn ends."""
    from engine.zone_mover import move_battle_to_zone

    p_state = state.players[player]
    rushed = [
        c.uid for c in p_state.battle_zone
        if c.temp_flags.get("abyss_rush_turn") == state.turn_number
    ]
    for uid in rushed:
        move_battle_to_zone(
            state, player, uid, reason="abyss_rush", to_zone="deck",
            place=lambda definition, _uid: p_state.deck.append(definition),
        )


def _trigger_turn_end(state: GameState) -> None:
    """Rule 511.1: "at the end of the turn" abilities trigger."""
    player = state.active_player
    state.turn_info.turn_end_triggered = True

    from engine.trigger_registry import fire_trigger
    fire_trigger(state, TriggerEvent.END_OF_TURN, {
        "controller": player,
        "turn_number": state.turn_number,
    }, None)
    
    from engine.entry_rules import offer_from_hand_at_turn_end
    offer_from_hand_at_turn_end(state, player)

    _return_abyss_rush(state, player)


def _end_turn(state: GameState) -> None:
    """Finish the turn: expiry, then the next player (rules 511.2, 512)."""
    if not state.turn_info.turn_end_triggered:
        _trigger_turn_end(state)
    player = state.active_player
    p_state = state.players[player]
    state.turn_info.turn_end_triggered = False

    # ── Pending state cleanup (Rule 512.1) ──────────────────────────────────
    # Move any creatures in PENDING state to graveyard at end of turn
    pending_creatures = [c for c in p_state.battle_zone if c.temp_flags.get("_pending")]
    for creature in pending_creatures:
        p_state.battle_zone.remove(creature)
        state.global_effects.remove_by_source(creature.uid)
        p_state.graveyard.append(creature.definition)
    
    # ── "This turn" effects expire for both players (rule 511.2) ─────────────
    for each in state.players:
        each.expire_eot_effects()
    p_state.next_creature_discount = 0
    
    # ── Expire gained_control effects for BOTH players ──────────────────────
    # (Protection and mandatory actions affect both sides; gained_control can cross)
    for other_player_idx in (0, 1):
        other_p_state = state.players[other_player_idx]
        creatures_to_move = []
        
        for creature in list(other_p_state.battle_zone):
            revert_result = creature.revert_gained_control_if_eot()
            if revert_result:
                was_reverted, original_controller, _ = revert_result
                if was_reverted and original_controller != other_player_idx:
                    # Move back to original controller's BZ
                    creatures_to_move.append((creature, original_controller))
        
        # Execute moves after iteration to avoid mid-loop mutation
        for creature, target_player in creatures_to_move:
            other_p_state.battle_zone.remove(creature)
            state.players[target_player].battle_zone.append(creature)
            # Restore static effects for the original controller
            creature.apply_static_effects(state)
    
    # ── Global effect expiry ───────────────────────────────────────────────────
    state.global_effects.expire_eot()
    state.attack_context = None

    if p_state.extra_turns > 0:
        p_state.extra_turns -= 1          # this player goes again
        state.turn_info.active_player = player
    else:
        state.turn_info.active_player = 1 - player
    state.turn_info.turn_number += 1
    state.turn_info.phase = Phase.START_OF_TURN
