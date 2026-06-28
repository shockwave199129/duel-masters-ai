"""engine/evolution_rules.py — Evolution base validation (rule 801)."""
from __future__ import annotations

from core.cards import CardDefinition
from core.enums import CardSubtype, CardType
from core.zones import Creature


def is_valid_evolution_base(evolution_def: CardDefinition, base: Creature) -> bool:
    """
    Rule 801.1: evolution requires a valid base creature in the battle zone.
    Rule 801.1a: if no valid evolution base exists, cannot evolve.

    The base must:
      - Be a creature (not ignored)
      - Match evolution_source_races OR evolution_source_types (if specified)
    """
    if base.is_ignored:
        return False
    if base.definition.card_type != CardType.CREATURE:
        return False

    has_race_match = (
        evolution_def.evolution_source_races
        and evolution_def.evolution_source_races.intersection(base.definition.races)
    )
    has_type_match = (
        evolution_def.evolution_source_types
        and base.definition.card_type in evolution_def.evolution_source_types
    )

    if evolution_def.evolution_source_races or evolution_def.evolution_source_types:
        return bool(has_race_match or has_type_match)

    # No specific requirements (Star Evolution, generic evolution)
    return True


def get_valid_evolution_bases(
    defn: CardDefinition,
    battle_zone: list[Creature],
) -> list[Creature]:
    """Return all valid evolution bases for *defn* in *battle_zone*."""
    if defn.card_subtype == CardSubtype.STAR_MAX:
        return []

    return [
        creature
        for creature in battle_zone
        if is_valid_evolution_base(defn, creature)
    ]
