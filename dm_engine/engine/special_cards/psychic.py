"""engine/special_cards/psychic.py — Psychic card mechanics."""
from __future__ import annotations
from typing import Optional
from core.state import GameState
from core.zones import Creature, EvolutionStackEntry, HandCard
from core.cards import CardDefinition
from core.enums import CardSubtype


def awaken_psychic_creature(
    state: GameState,
    player: int,
    creature_uid: str,
    awakened_face_defn: "CardDefinition",
) -> "Creature":
    """
    Flip a Psychic Creature to its awakened face in the Battle Zone (rule 805.1a).

    Rule 805.5: The creature is treated as the same creature — uid, tapped state,
    entered_turn, power_modifiers, and applied effects are all preserved.
    Rule 805.6: The awakened creature does not suffer from summoning sickness.
    """
    from core.cards import CardDefinition  # local import to avoid circular deps
    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Creature {creature_uid} not found for awaken")
    # Remove old static effects before flipping (rule 805.2: independent characteristics)
    creature.remove_static_effects(state)
    # Flip to the awakened definition (rule 805.2: each face has independent characteristics)
    creature.definition = awakened_face_defn
    creature.face = 1
    # Rule 805.6: no summoning sickness after awakening
    creature.has_summoning_sickness = False
    # uid, is_tapped, entered_turn, power_modifiers preserved (rule 805.5)
    # Re-apply static effects from the awakened face
    creature.apply_static_effects(state)
    # Fire ON_AWAKEN trigger
    from core.enums import TriggerEvent
    from engine.trigger_registry import fire_trigger
    fire_trigger(state, TriggerEvent.ON_AWAKEN, {
        "source_uid": creature.uid,
        "source_card_id": creature.id,
        "controller": player,
    }, creature.uid)
    return creature




def link_psychic_cells(
    state: GameState,
    player: int,
    cell_uids: list[str],
    super_creature_defn: "CardDefinition",
    *,
    primary_uid: Optional[str] = None,
) -> "Creature":
    """
    Perform Awakening Link — simultaneously flip and link multiple Psychic Creatures
    into a Psychic Super Creature (rule 805.1c).

    The combined creature uses the uid of the primary cell (or the first cell if
    primary_uid is None). All other cells are stored in linked_cells.

    Rule 806.1f: Each Psychic Cell possesses the civilizations of the Psychic Super Creature.
    Tapped state: the Super Creature is tapped if any constituent cell was tapped (rule 806.2).
    Power modifiers: the Super Creature inherits modifiers from the primary cell.
    """
    if not cell_uids:
        raise ValueError("link_psychic_cells requires at least one cell uid")

    p_state = state.players[player]

    # Collect all cell Creatures from the BZ
    cells: list[Creature] = []
    for uid in cell_uids:
        c = p_state.find_creature(uid)
        if c is None:
            raise ValueError(f"Psychic Cell {uid} not found in battle zone")
        cells.append(c)

    # Pick the primary cell (becomes the combined Creature object)
    if primary_uid is not None:
        primary = next((c for c in cells if c.uid == primary_uid), None)
        if primary is None:
            raise ValueError(f"primary_uid {primary_uid} not in cell_uids")
        others = [c for c in cells if c.uid != primary_uid]
    else:
        primary = cells[0]
        others = cells[1:]

    # Remove all non-primary cells from the battle zone
    for other in others:
        p_state.battle_zone.remove(other)
        state.global_effects.remove_by_source(other.uid)

    # Flip primary cell to the Super Creature definition
    primary.remove_static_effects(state)
    primary.definition = super_creature_defn
    primary.face = 1

    # Rule 808.1a / link: Super Creature has no summoning sickness
    primary.has_summoning_sickness = False

    # Tapped if ANY cell was tapped (rule 806.2)
    primary.is_tapped = any(c.is_tapped for c in cells)

    # Store the constituent cells (mark them as cells of this super creature)
    for cell in cells:
        cell.is_psychic_cell = True
    primary.linked_cells = list(cells)  # includes primary itself

    # Re-apply static effects from the Super Creature face
    primary.apply_static_effects(state)

    # Fire ON_AWAKEN trigger for the primary (combined) creature
    from core.enums import TriggerEvent
    from engine.trigger_registry import fire_trigger
    fire_trigger(state, TriggerEvent.ON_AWAKEN, {
        "source_uid": primary.uid,
        "source_card_id": primary.id,
        "controller": player,
    }, primary.uid)

    return primary




def should_apply_psychic_release(creature: "Creature") -> bool:
    """
    Check whether a Psychic Creature's Release replacement effect (rule 805.1b) should
    apply when it would leave the Battle Zone.

    Rule 805.1b: Some Psychic Creatures have a Release ability that flips them to the
    lower-cost face instead of leaving the Battle Zone. This is a replacement effect.

    The engine sets temp_flag "_has_psychic_release" (via effect parser or tests) to
    indicate the card has this ability. Only fires once per leave attempt (rule 805.1b:
    replacement effect — cannot stack with another replacement effect).
    """
    if creature.definition.card_subtype not in (CardSubtype.PSYCHIC, CardSubtype.PSYCHIC_SUPER):
        return False
    if not creature.temp_flags.get("_has_psychic_release", False):
        return False
    if creature.temp_flags.get("_replacement_already_applied", False):
        return False
    return True




def apply_psychic_release(
    state: GameState,
    player: int,
    creature_uid: str,
    lower_face_defn: "CardDefinition",
) -> "Creature":
    """
    Apply the Psychic Release replacement effect: flip to lower-cost face, stay in BZ.
    (Rule 805.1b)
    """
    from core.cards import CardDefinition
    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Creature {creature_uid} not found for psychic_release")
    # Remove old static effects before flipping
    creature.remove_static_effects(state)
    creature.definition = lower_face_defn
    creature.face = 0
    creature.temp_flags["_replacement_already_applied"] = True
    # Re-apply static effects from the lower face
    creature.apply_static_effects(state)
    return creature



