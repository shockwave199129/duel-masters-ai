"""engine/sba/actions/standalone_cross_gear.py — State-based action: Rule 703.4f.

A Cross Gear that is not attached to a creature is destroyed (placed in graveyard).
"""
from __future__ import annotations
from core.enums import CardType
from core.state import GameState
from core.zones import GraveyardCard


def _sba_cross_gear_detach(state: GameState) -> bool:
    """
    Rule 703.4f: A Cross Gear not attached to a creature is placed in the Graveyard.

    In this engine's model, a Cross Gear is either:
      - In the Battle Zone (unattached / standalone), or
      - Removed from the Battle Zone and stored in a host creature's
        attached_cards list (attached).

    So any Cross Gear found in the Battle Zone is by definition standalone
    and must be cleaned up.
    """
    fired = False
    for player_idx in range(2):
        to_remove = [
            c for c in state.players[player_idx].battle_zone
            if c.definition.card_type == CardType.CROSS_GEAR
        ]
        for gear in to_remove:
            state.players[player_idx].battle_zone.remove(gear)
            state.players[player_idx].graveyard.insert(
                0,
                GraveyardCard(
                    definition=gear.definition,
                    died_from="sba_cross_gear_standalone",
                    died_on_turn=state.turn_number,
                ),
            )
            fired = True
    return fired
