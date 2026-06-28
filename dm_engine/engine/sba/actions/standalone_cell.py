"""engine/sba/actions/standalone_cell.py — State-based action: Rule 806 — standalone cell."""
from __future__ import annotations
from core.enums import CardType, CardSubtype, GlobalEffectType
from core.state import GameState
from core.zones import Creature
from core.zones import GraveyardCard


def _sba_standalone_cell(state: GameState) -> bool:
    """
    Rule 703.4g: A standalone Cell in the Battle Zone is placed in the Graveyard.
    Cells are component cards of combined creatures. They cannot exist alone.
    """
    fired = False
    for player_idx in range(2):
        to_remove = [
            c for c in state.players[player_idx].battle_zone
            if c.definition.card_type == CardType.CELL
        ]
        for creature in to_remove:
            state.players[player_idx].battle_zone.remove(creature)
            state.players[player_idx].graveyard.insert(
                0, GraveyardCard(definition=creature.definition,
                                  died_from="sba_standalone_cell",
                                  died_on_turn=state.turn_number)
            )
            fired = True

    return fired


