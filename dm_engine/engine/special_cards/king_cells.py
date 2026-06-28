"""engine/special_cards/king_cells.py — King Cell card mechanics (rule 814)."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature, EvolutionStackEntry, HandCard
from core.cards import CardDefinition
from core.enums import Civilization


def validate_king_combine(
    state: GameState,
    player: int,
    king_defn: CardDefinition,
    cell_uids: list[str],
) -> tuple[bool, str]:
    """
    Validate that a King Cell combine is legal (rule 814.1a, 814.1c).

    Checks:
      1. All required slugs are present in the cell selection.
      2. The selected cells cover all civilizations required by the King Creature.
      3. All cells are controlled by the combining player.

    Returns (is_valid, reason). If is_valid is False, reason explains why.
    """
    p_state = state.players[player]

    # 1. Check required slugs
    required_slugs = sorted(king_defn.king_combine_required_slugs)
    if required_slugs:
        cell_slugs = []
        for uid in cell_uids:
            hand_card = p_state.find_in_hand(uid)
            if hand_card is not None:
                cell_slugs.append(hand_card.definition.slug)
                continue
            mana_card = p_state.find_mana(uid)
            if mana_card is not None:
                cell_slugs.append(mana_card.definition.slug)
                continue
            return False, f"Cell {uid} not found in hand or mana zone"

        for req_slug in required_slugs:
            if req_slug not in cell_slugs:
                return False, f"Missing required cell slug '{req_slug}'"

    # 2. Check civilization coverage (rule 814.1c)
    king_civs = king_defn.civilizations
    if king_civs and cell_uids:
        covered_civs: set[Civilization] = set()
        for uid in cell_uids:
            hand_card = p_state.find_in_hand(uid)
            if hand_card is not None:
                covered_civs.update(hand_card.definition.civilizations)
                continue
            mana_card = p_state.find_mana(uid)
            if mana_card is not None:
                covered_civs.update(mana_card.definition.civilizations)
                continue

        missing_civs = king_civs - covered_civs
        if missing_civs:
            civ_names = ", ".join(c.value for c in missing_civs)
            return False, f"Cells missing coverage for civilizations: {civ_names}"

    # 3. All cells must exist in hand or mana zone
    for uid in cell_uids:
        hand_card = p_state.find_in_hand(uid)
        mana_card = p_state.find_mana(uid)
        if hand_card is None and mana_card is None:
            return False, f"Cell {uid} not found"

    return True, ""


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



