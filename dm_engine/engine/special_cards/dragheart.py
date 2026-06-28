"""engine/special_cards/dragheart.py — Dragheart card mechanics."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature, EvolutionStackEntry, HandCard
from core.cards import CardDefinition
from core.enums import CardSubtype
from engine.zone_mover import creature_to_hyperspatial_card


def dragsolve_dragheart(
    state: GameState,
    player: int,
    creature_uid: str,
    creature_face_defn: "CardDefinition",
) -> "Creature":
    """
    Flip a Dragheart Weapon or Fortress to its Creature face via Dragsolve (rule 807.1a).

    Rule 807.5a: It does not matter which face was up at the beginning of the turn.
    If the card existed in the Battle Zone as a Weapon at the start of the turn and
    then Dragsolves, the Dragheart Creature is treated as having been in the BZ since
    the start of the turn (entered_turn is preserved, so can_attack() naturally works).
    Rule 807.5: Dragheart Creatures DO suffer from summoning sickness if they entered
    the BZ this turn as a Weapon (entered_turn == current turn).
    """
    from core.cards import CardDefinition
    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Creature {creature_uid} not found for dragsolve")
    # Remove old static effects before flipping
    creature.remove_static_effects(state)
    # Flip to creature face (rule 807.2: each face has independent characteristics)
    creature.definition = creature_face_defn
    creature.face = 1
    # entered_turn is NOT changed — rule 807.5a: orientation at BZ entry time determines sickness
    # has_summoning_sickness is not changed here — it reflects whether the card entered this turn
    # Re-apply static effects from the creature face
    creature.apply_static_effects(state)
    # Fire ON_DRAGSOLVE trigger
    from core.enums import TriggerEvent
    from engine.trigger_registry import fire_trigger
    fire_trigger(state, TriggerEvent.ON_DRAGSOLVE, {
        "source_uid": creature.uid,
        "source_card_id": creature.id,
        "controller": player,
    }, creature.uid)
    return creature




def should_apply_dragon_evasion(creature: "Creature") -> bool:
    """
    Check whether a Dragheart Creature's Dragon Evasion replacement effect (rule 807.1b)
    should apply when it would leave the Battle Zone.

    Rule 807.1b: Some Dragheart Creatures have a Dragon Evasion ability that flips them
    to the lower-cost face instead of leaving the Battle Zone. This is a replacement effect.
    """
    if creature.definition.card_subtype not in (CardSubtype.DRAGHEART,):
        return False
    if not creature.temp_flags.get("_has_dragon_evasion", False):
        return False
    if creature.temp_flags.get("_replacement_already_applied", False):
        return False
    return True




def apply_dragon_evasion(
    state: GameState,
    player: int,
    creature_uid: str,
    lower_face_defn: "CardDefinition",
) -> "Creature":
    """
    Apply the Dragon Evasion replacement effect: flip to lower-cost face, stay in BZ.
    (Rule 807.1b)
    """
    from core.cards import CardDefinition
    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Creature {creature_uid} not found for dragon_evasion")
    creature.definition = lower_face_defn
    creature.face = 0
    creature.temp_flags["_replacement_already_applied"] = True
    return creature


# ─────────────────────────────────────────────────────────────────────────────
# Twinpact and Forbidden flip logic (Phase 5A)
# ─────────────────────────────────────────────────────────────────────────────



def dragon_soul_evasion(
    state: GameState,
    player: int,
    super_uid: str,
    returning_cell_idx: int = 0,
) -> list["Creature"]:
    """
    Perform Dragon Soul Evasion — a Dragheart Super Creature that would leave the
    Battle Zone instead returns one Dragheart Cell to Hyperspatial and flips the
    remaining cells to their lower-cost face (rule 808.1b).

    Rule 808.1b: This IS a replacement effect.
    Rule 808.1d: Dragheart Cells leaving the BZ are NOT treated as creatures leaving.
    Replacement effects applied to the creature also apply when a Dragheart Cell leaves.
    """
    p_state = state.players[player]
    super_creature = p_state.find_creature(super_uid)
    if super_creature is None:
        raise ValueError(f"Dragheart Super Creature {super_uid} not found")

    cells = list(super_creature.linked_cells)
    if not cells:
        raise ValueError(f"Dragheart Super Creature {super_uid} has no linked_cells")

    if returning_cell_idx < 0 or returning_cell_idx >= len(cells):
        raise ValueError(f"returning_cell_idx {returning_cell_idx} out of range for {len(cells)} cells")

    returning_cell = cells[returning_cell_idx]
    remaining_cells = [c for i, c in enumerate(cells) if i != returning_cell_idx]

    # Mark as replacement applied (rule 808.1b: this is a replacement effect)
    super_creature.temp_flags["_replacement_already_applied"] = True

    was_tapped = super_creature.is_tapped
    inherited_mods = list(super_creature.power_modifiers)

    # Remove the Super Creature from the battle zone
    p_state.battle_zone.remove(super_creature)
    state.global_effects.remove_by_source(super_creature.uid)

    # Return chosen cell to Hyperspatial (rule 808.1b)
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

    # Flip remaining cells back to lower-cost face, inheriting state (rule 808.1b)
    surviving: list[Creature] = []
    for cell in remaining_cells:
        cell.remove_static_effects(state)
        cell.face = 0
        cell.is_tapped = was_tapped
        # Dragheart Super Creatures have no sickness (rule 808.1a) so the surviving
        # cells were already able to act; preserve that (no sickness reset here)
        cell.has_summoning_sickness = False
        cell.is_psychic_cell = False
        cell.linked_cells.clear()
        cell.power_modifiers = [m for m in inherited_mods]
        # Re-apply static effects from the lower face
        cell.apply_static_effects(state)
        p_state.battle_zone.append(cell)
        surviving.append(cell)

    return surviving



