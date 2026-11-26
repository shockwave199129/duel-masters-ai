"""engine/special_cards/neo.py — Neo card mechanics."""
from __future__ import annotations
from core.enums import CardSubtype
from core.zones import Creature


def should_apply_gneo_all_leave_replacement(creature: Creature) -> bool:
    """
    Check if a creature should apply the G-NEO all-leave replacement.
    
    Rule 803.2: For a G-NEO Creature, when it leaves the Battle Zone while it has
    a card placed underneath it and is treated as a G-NEO Evolution Creature, all
    the cards placed under the G-NEO Creature leave instead.
    
    Rule 803.2a: The "when leaving" effect is a replacement effect. If another
    replacement effect was applied first, this cannot be applied.
    """
    # Only G-NEO has this ability, and only with a card underneath
    if (creature.definition.card_subtype != CardSubtype.G_NEO
            or not creature.is_neo_evolution_creature()):
        return False
    
    # Check if another replacement effect already applied (rule 803.2a)
    if creature.temp_flags.get("_replacement_already_applied", False):
        return False
    
    return True




# ─────────────────────────────────────────────────────────────────────────────
# Psychic / Dragheart Flip, Link, and Release helpers (rules 805–808)
# ─────────────────────────────────────────────────────────────────────────────


