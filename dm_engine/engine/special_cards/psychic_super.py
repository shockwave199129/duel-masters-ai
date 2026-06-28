"""engine/special_cards/psychic_super.py — Psychic Super card mechanics."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature, EvolutionStackEntry, HandCard


def link_release_psychic_super(
    state: GameState,
    player: int,
    super_uid: str,
    returning_cell_idx: int = 0,
) -> list["Creature"]:
    """
    Perform Link Release — a Psychic Super Creature separates back into its component
    Psychic Creatures (rule 806.1b).

    One Psychic Cell (chosen by returning_cell_idx into linked_cells) is returned to the
    owner's Hyperspatial Zone. The remaining cells are placed in the Battle Zone face=0
    (lower-cost face), inheriting tapped state and power modifiers from the Super Creature
    (rule 806.2).

    Rule 806.1b: This is NOT a replacement effect.
    Rule 806.1e: Cell leaving the BZ is not treated as a creature leaving.
    Rule 806.2: Tapped state and applied effects are inherited by each separated creature.
    Rule 806.2a: If 3+ cells, the active player chooses one to continue any ongoing attack.
    """
    p_state = state.players[player]
    super_creature = p_state.find_creature(super_uid)
    if super_creature is None:
        raise ValueError(f"Psychic Super Creature {super_uid} not found")

    cells = list(super_creature.linked_cells)
    if not cells:
        raise ValueError(f"Super Creature {super_uid} has no linked_cells")

    if returning_cell_idx < 0 or returning_cell_idx >= len(cells):
        raise ValueError(f"returning_cell_idx {returning_cell_idx} out of range for {len(cells)} cells")

    returning_cell = cells[returning_cell_idx]
    remaining_cells = [c for i, c in enumerate(cells) if i != returning_cell_idx]

    # Inherit tapped state and power modifiers from the Super Creature (rule 806.2)
    was_tapped = super_creature.is_tapped
    inherited_mods = list(super_creature.power_modifiers)

    # Remove the Super Creature from the battle zone
    p_state.battle_zone.remove(super_creature)
    state.global_effects.remove_by_source(super_creature.uid)

    # Return one cell to Hyperspatial (rule 806.1b)
    returning_cell.face = 0
    returning_cell.is_tapped = False
    returning_cell.has_summoning_sickness = True
    returning_cell.entered_turn = 0
    returning_cell.power_modifiers.clear()
    returning_cell.temp_flags.clear()
    returning_cell.is_psychic_cell = False
    returning_cell.linked_cells.clear()
    owner = returning_cell.owner
    state.players[owner].hyperspatial_zone.append(creature_to_hyperspatial_card(returning_cell))

    # Place remaining cells back in the Battle Zone (flipped to face=0)
    surviving: list[Creature] = []
    for cell in remaining_cells:
        # Flip to lower-cost face (face=0), inherit super's tapped/effect state (rule 806.2)
        cell.face = 0
        cell.is_tapped = was_tapped
        cell.has_summoning_sickness = False  # already was in BZ this turn
        cell.is_psychic_cell = False
        cell.linked_cells.clear()
        # Inherit power modifiers from the Super Creature
        cell.power_modifiers = [m for m in inherited_mods]
        p_state.battle_zone.append(cell)
        surviving.append(cell)

    return surviving



