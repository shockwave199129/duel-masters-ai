"""engine/sba/actions/battle_loser.py — State-based action: Rule 115.3b — battle loser."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature


def _sba_battle_loser(state: GameState) -> bool:
    """
    Rule 703.4d: A creature that lost a battle is destroyed.
    The 'lost_battle' flag is set by the battle resolver on the
    creature that had lower power (or was slain by Slayer).
    """
    fired = False
    for player_idx in range(2):
        to_destroy = [
            c for c in state.players[player_idx].battle_zone
            if c.temp_flags.get("lost_battle", False) and c.can_be_destroyed()
        ]
        for creature in to_destroy:
            creature.clear_flag("lost_battle")
            _destroy_creature(state, player_idx, creature, "battle")
            fired = True

    return fired


