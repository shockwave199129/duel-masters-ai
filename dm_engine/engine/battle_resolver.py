"""
engine/battle_resolver.py — resolve Duel Masters battles.
"""

from __future__ import annotations

from core.enums import Keyword, Phase
from core.state import GameState
from engine.sba_checker import check_state_based_actions


def resolve_battle(state: GameState) -> GameState:
    """Resolve the current battle, mark losers, then run SBAs."""
    s = state.copy()
    ctx = s.attack_context
    if ctx is None:
        return s

    attacker_result = s.find_creature_anywhere(ctx.attacker_uid)
    if attacker_result is None:
        s.turn_info.phase = Phase.END_OF_ATTACK
        return s
    _, attacker = attacker_result

    defender_uid = ctx.blocker_uid if ctx.blocker_uid else ctx.target_uid
    defender_result = s.find_creature_anywhere(defender_uid or "")
    if defender_result is None:
        s.turn_info.phase = Phase.END_OF_ATTACK
        return s
    _, defender = defender_result

    attacker_always_wins = _wins_battles(attacker)
    defender_always_wins = _wins_battles(defender)
    attacker_power = attacker.compute_power(s)
    defender_power = defender.compute_power(s)

    if attacker.has_keyword(Keyword.SLAYER):
        defender.set_flag("lost_battle", True)
    if defender.has_keyword(Keyword.SLAYER):
        attacker.set_flag("lost_battle", True)

    if attacker_always_wins and defender_always_wins:
        pass
    elif attacker_always_wins:
        defender.set_flag("lost_battle", True)
    elif defender_always_wins:
        attacker.set_flag("lost_battle", True)
    elif attacker_power == defender_power:
        attacker.set_flag("lost_battle", True)
        defender.set_flag("lost_battle", True)
    elif attacker_power > defender_power:
        defender.set_flag("lost_battle", True)
    else:
        attacker.set_flag("lost_battle", True)

    s.turn_info.phase = Phase.END_OF_ATTACK
    return check_state_based_actions(s)


def _wins_battles(creature) -> bool:
    """Temporary effect hook for abilities that say this creature wins battles."""
    return bool(
        creature.temp_flags.get("wins_battles", False)
        or creature.temp_flags.get("win_battle", False)
    )
