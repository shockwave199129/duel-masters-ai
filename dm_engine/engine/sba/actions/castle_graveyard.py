"""engine/sba/actions/castle_graveyard.py — State-based action: Rule 304.3 — castle in graveyard."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature
from core.zones import GraveyardCard


def _sba_castle_graveyard(state: GameState) -> bool:
    """
    Rule 703.4k: When a fortified shield leaves the Shield Zone,
    the Castle is placed in the owner's Graveyard.

    Tracked by the "detached_castle" list on PlayerState.
    The action executor populates this when a shield is broken.
    """
    fired = False
    for player_idx in range(2):
        p = state.players[player_idx]
        if p.detached_castles:
            for castle_defn in p.detached_castles:
                p.graveyard.insert(
                    0, GraveyardCard(definition=castle_defn,
                                      died_from="sba_castle_detach",
                                      died_on_turn=state.turn_number)
                )
            p.detached_castles = []
            fired = True

    return fired


