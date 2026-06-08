"""
engine/phase_controller.py — deterministic turn and phase advancement.
"""

from __future__ import annotations

from core.actions import Action
from core.enums import Phase
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
        s.attack_context = None
        s.turn_info.phase = Phase.ATTACK
    elif phase == Phase.END_OF_TURN:
        _end_turn(s)

    return s


def _start_turn(state: GameState) -> None:
    player = state.active_player
    p_state = state.players[player]
    p_state.reset_turn_flags()
    p_state.untap_all()
    # Rule 301.5 / 506.1a: creatures present since before this turn began lose
    # summoning sickness now. This correctly includes creatures that entered
    # during the opponent's turn (Shield Trigger summons, Ninja Strike, etc.).
    for creature in p_state.battle_zone:
        if creature.entered_turn < state.turn_number:
            creature.clear_summoning_sickness()


def _end_turn(state: GameState) -> None:
    player = state.active_player
    p_state = state.players[player]
    p_state.expire_eot_effects()
    state.global_effects.expire_eot()
    state.attack_context = None

    state.turn_info.active_player = 1 - player
    state.turn_info.turn_number += 1
    state.turn_info.phase = Phase.START_OF_TURN
