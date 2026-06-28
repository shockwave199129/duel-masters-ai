"""engine/sba/actions/zero_power.py — State-based action: Rule 108.1b / 700.3 — zero power."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature


def _sba_zero_power(state: GameState) -> bool:
    """
    Rule 703.4c: A creature with power 0 or less is destroyed.
    Rule 700.3: if a creature "cannot be destroyed", it is not destroyed even
    at 0 power (the cannot_be_destroyed flag overrides this SBA).
    """
    fired = False
    for player_idx in range(2):
        to_destroy = []
        for creature in state.players[player_idx].battle_zone:
            if creature.is_ignored:
                continue  # sealed creatures are not evaluated for power
            power = creature.compute_power(state)
            if power <= 0 and creature.can_be_destroyed():
                to_destroy.append(creature)

        for creature in to_destroy:
            _destroy_creature(state, player_idx, creature, "sba_zero_power")
            fired = True

    return fired


