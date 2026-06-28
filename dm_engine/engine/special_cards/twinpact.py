"""engine/special_cards/twinpact.py — Twinpact card mechanics."""
from __future__ import annotations
from typing import Optional
from core.state import GameState
from core.zones import Creature, EvolutionStackEntry, HandCard
from core.cards import CardDefinition
from core.enums import CardSubtype
from core.cards import is_twinpact
from core.cards import get_other_face


def flip_twinpact(creature: Creature, card_db=None, state: Optional[GameState] = None) -> Creature:
    """
    Flip a Twinpact card to its other face when it enters the battle zone.

    Uses get_other_face() to resolve the other face from the card database.
    If card_db is None or resolution fails, falls back to the twinpact_other_face
    dict stored on the CardDefinition.
    Toggles the _twinpact_flipped temp flag.
    """
    if not is_twinpact(creature.definition):
        return creature
    if creature.definition.other_face_id is None:
        return creature

    # Try to resolve the other face from the card database
    other_face = get_other_face(creature.definition, card_db=card_db)
    if other_face is not None:
        if state is not None:
            creature.remove_static_effects(state)
        creature.definition = other_face
    else:
        # Fallback: manually clone with swapped face ID
        old_def = creature.definition
        from core.cards import CardDefinition as _CD
        new_def = _CD(
            id=old_def.other_face_id,
            slug=old_def.slug,
            name=old_def.name,
            cost=old_def.cost,
            power=old_def.power,
            card_type=old_def.card_type,
            card_subtype=old_def.card_subtype,
            civilizations=old_def.civilizations,
            races=old_def.races,
            keywords=old_def.keywords,
            effects=old_def.effects,
            evolution_source_races=old_def.evolution_source_races,
            evolution_source_types=old_def.evolution_source_types,
            is_multiface=old_def.is_multiface,
            other_face_id=old_def.id,  # point back to the original
        )
        if state is not None:
            creature.remove_static_effects(state)
        creature.definition = new_def
    creature.temp_flags["_twinpact_flipped"] = not creature.temp_flags.get("_twinpact_flipped", False)
    # Re-apply static effects from the new face
    if state is not None:
        creature.apply_static_effects(state)
    # Fire ON_TWINPACT_FLIP trigger
    if state is not None:
        from core.enums import TriggerEvent
        from engine.trigger_registry import fire_trigger
        fire_trigger(state, TriggerEvent.ON_TWINPACT_FLIP, {
            "source_uid": creature.uid,
            "source_card_id": creature.id,
            "controller": creature.controller,
        }, creature.uid)
    return creature



