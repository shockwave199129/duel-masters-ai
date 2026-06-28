"""engine/special_cards/king_cells.py — King Cells card mechanics."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature, EvolutionStackEntry, HandCard
from core.cards import CardDefinition


def combine_king_cells(
    state: GameState,
    player: int,
    king_creature_defn: "CardDefinition",
    cell_uids: list[str],
) -> "Creature":
    """
    Combine King Cells from hand and/or mana zone into a King Creature (rule 814.1c).

    Payment must already be applied via tap_mana_for_payment before calling this.
    """
    if not cell_uids:
        raise ValueError("combine_king_cells requires at least one cell uid")

    p_state = state.players[player]
    cell_creatures: list[Creature] = []

    for uid in cell_uids:
        hand_card = p_state.find_in_hand(uid)
        if hand_card is not None:
            p_state.hand.remove(hand_card)
            cell = Creature(
                definition=hand_card.definition,
                uid=uid,
                controller=player,
                owner=player,
            )
            cell.is_king_cell = True
            cell_creatures.append(cell)
            continue

        mana_card = p_state.find_mana(uid)
        if mana_card is not None:
            p_state.mana_zone.remove(mana_card)
            cell = Creature(
                definition=mana_card.definition,
                uid=uid,
                controller=player,
                owner=player,
                is_tapped=mana_card.is_tapped,
            )
            cell.is_king_cell = True
            cell_creatures.append(cell)
            continue

        raise ValueError(f"King Cell {uid} not found in hand or mana zone")

    primary = cell_creatures[0]
    primary.definition = king_creature_defn
    primary.has_summoning_sickness = True
    primary.entered_turn = state.turn_number
    primary.is_tapped = any(c.is_tapped for c in cell_creatures)
    primary.linked_cells = list(cell_creatures)
    p_state.battle_zone.append(primary)
    return primary



