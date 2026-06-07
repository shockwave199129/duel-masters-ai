"""
engine/shield_resolver.py — shield breaking and direct attack handling.
"""

from __future__ import annotations

from core.enums import Phase
from core.state import GameState
from engine.sba_checker import check_state_based_actions
from engine.zone_mover import move_shield_to_standby


def resolve_shield_break_choice(state: GameState, first_shield_index: int) -> GameState:
    """
    Break the attacker's shield batch.

    The current action model chooses the first shield. Remaining shields in the
    same breaker batch are selected in table order until the break count is met.
    The batch enters standby together before declarations are offered.
    """
    s = state.copy()
    ctx = s.attack_context
    if ctx is None:
        return s
    if not ctx.is_attacking_player:
        s.turn_info.phase = Phase.END_OF_ATTACK
        return s

    defender = ctx.defending_player
    d_state = s.players[defender]
    if d_state.shield_count == 0:
        ctx.received_direct_attack = True
        s.turn_info.phase = Phase.END_OF_ATTACK
        return check_state_based_actions(s)

    attacker_result = s.find_creature_anywhere(ctx.attacker_uid)
    if attacker_result is None:
        s.turn_info.phase = Phase.END_OF_ATTACK
        return s
    _, attacker = attacker_result

    break_count = min(attacker.shields_broken_on_attack(), d_state.shield_count)
    indices = _shield_indices_for_batch(d_state.shield_count, first_shield_index, break_count)

    # Pop highest indices first so earlier positions stay stable.
    for idx in sorted(indices, reverse=True):
        move_shield_to_standby(s, defender, idx)

    ctx.shields_broken += len(indices)
    s.turn_info.phase = Phase.END_OF_ATTACK
    return check_state_based_actions(s)


def mark_direct_attack_if_applicable(state: GameState) -> GameState:
    """Set the direct-attack event only if the attack reaches a shieldless player."""
    s = state
    ctx = s.attack_context
    if ctx is None or not ctx.is_attacking_player:
        return s
    if s.players[ctx.defending_player].shield_count == 0 and ctx.shields_broken == 0:
        ctx.received_direct_attack = True
    return s


def _shield_indices_for_batch(total: int, first: int, count: int) -> list[int]:
    if first < 0 or first >= total:
        raise ValueError(f"Invalid shield index {first}")
    indices = [first]
    for idx in range(total):
        if len(indices) >= count:
            break
        if idx != first:
            indices.append(idx)
    return indices
