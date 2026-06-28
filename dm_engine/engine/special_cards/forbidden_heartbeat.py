"""engine/special_cards/forbidden_heartbeat.py — Forbidden Heartbeat card mechanics."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature, EvolutionStackEntry, HandCard
from core.enums import CardSubtype
from core.cards import is_forbidden


def flip_forbidden(creature: Creature) -> Creature:
    """
    Flip a Forbidden or Final Forbidden card when it leaves the battle zone.

    Toggles the _forbidden_flipped temp flag and flips the face field (0→1 or 1→0).
    """
    if not is_forbidden(creature.definition):
        return creature
    creature.temp_flags["_forbidden_flipped"] = not creature.temp_flags.get("_forbidden_flipped", False)
    creature.face = 1 - creature.face
    return creature


