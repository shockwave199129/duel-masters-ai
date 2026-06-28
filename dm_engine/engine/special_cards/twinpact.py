"""engine/special_cards/twinpact.py — Twinpact card mechanics."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature, EvolutionStackEntry, HandCard
from core.cards import CardDefinition
from core.enums import CardSubtype
from core.cards import is_twinpact
from core.cards import get_other_face


def flip_twinpact(creature: Creature, card_db=None) -> Creature:
    """
    Flip a Twinpact card to its other face when it enters the battle zone.

    If the creature is a multi-face card and has a valid other_face_id,
    this swaps to a new CardDefinition with the other_face_id as the card_id
    (simplified flip — full card_db resolution is a stub for later).
    Toggles the _twinpact_flipped temp flag.
    """
    if not is_twinpact(creature.definition):
        return creature
    if creature.definition.other_face_id is None:
        return creature

    # Simplified flip: create a new CardDefinition with the other_face_id
    # Full card_db resolution is deferred (see get_other_face stub)
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
    creature.definition = new_def
    creature.temp_flags["_twinpact_flipped"] = not creature.temp_flags.get("_twinpact_flipped", False)
    return creature



