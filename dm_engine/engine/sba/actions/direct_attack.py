"""engine/sba/actions/direct_attack.py — State-based action: Rule 104.2a / 509.1 — direct attack."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature


def _sba_direct_attack(state: GameState) -> bool:
    """
    Rule 703.4a: A player who received a Direct Attack loses the game.
    This is checked when the attack context indicates a direct attack
    was completed (shield_count == 0 and attack targeted the player).
    """
    if state.is_terminal():
        return False

    ctx = state.attack_context
    if ctx is None:
        return False

    # Direct attack = targeting player AND defender has 0 shields
    if not ctx.is_attacking_player:
        return False

    defender = ctx.defending_player
    if state.effective_shield_count(defender) == 0 and ctx.shields_broken >= 0:
        # Check if this attack actually reached the player
        if state.current_phase in (Phase.DIRECT_ATTACK, Phase.END_OF_ATTACK):
            if state.effective_shield_count(defender) == 0:
                winner = 1 - defender
                state.result = (
                    GameResult.PLAYER_0_WINS if winner == 0
                    else GameResult.PLAYER_1_WINS
                )
                return True

    return False


