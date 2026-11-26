"""
engine/shield_resolver.py — shield breaking and direct attack handling.
"""

from __future__ import annotations

from core.card_scripts import BREAKS_TO_GRAVEYARD
from core.continuous_rules import has_abilities
from core.enums import Phase
from core.zones import GraveyardCard
from core.state import GameState
from engine.sba_checker import check_state_based_actions
from engine.zone_mover import move_shield_to_standby


def resolve_shield_break_choice(state: GameState, first_shield_index: int, action=None) -> GameState:
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

    # Rule 509.2c: a creature with two breaker abilities chooses one
    break_mode = ctx.break_mode
    if action is not None:
        break_mode = action.get_extra().get("break_mode") or break_mode
    if break_mode == "triple":
        break_count = min(3, d_state.shield_count)
    elif break_mode == "double":
        break_count = min(2, d_state.shield_count)
    else:
        break_count = min(attacker.shields_broken_on_attack(), d_state.shield_count)

    if not 0 <= first_shield_index < d_state.shield_count or first_shield_index in ctx.chosen_shields:
        raise ValueError(f"Invalid shield index {first_shield_index}")
    ctx.chosen_shields.append(first_shield_index)
    ctx.break_mode = break_mode
    if len(ctx.chosen_shields) < break_count < d_state.shield_count:
        return s  # still choosing; nothing breaks until all are picked
    # When every shield will break there is nothing more to choose
    indices = list(ctx.chosen_shields)
    indices += [i for i in range(d_state.shield_count) if i not in indices][:break_count - len(indices)]

    # "Your opponent puts that shield into their graveyard instead"
    if attacker.definition.slug in BREAKS_TO_GRAVEYARD and has_abilities(attacker):
        for idx in sorted(indices, reverse=True):
            shield = d_state.shield_zone.pop(idx)
            d_state.graveyard.insert(0, GraveyardCard(
                definition=shield.definition, uid=shield.uid,
                died_from="shield_to_graveyard", died_on_turn=s.turn_number))
    else:
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
    if s.find_creature_anywhere(ctx.attacker_uid) is None:
        return s  # no attacker, no attack (rule 505.6)
    if s.players[ctx.defending_player].shield_count == 0 and ctx.shields_broken == 0:
        ctx.received_direct_attack = True
        
        # Fire ON_DIRECT_ATTACK trigger
        from core.enums import TriggerEvent
        from engine.trigger_registry import fire_trigger
        attacker_result = s.find_creature_anywhere(ctx.attacker_uid)
        if attacker_result:
            _, attacker = attacker_result
            fire_trigger(s, TriggerEvent.ON_DIRECT_ATTACK, {
                "source_uid": attacker.uid,
                "source_card_id": attacker.id,
                "controller": ctx.attacker_player,
                "defender_player": ctx.defending_player,
            }, attacker.uid)
    return s

