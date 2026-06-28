"""engine/special_cards/star_evolution.py — Star Evolution card mechanics."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature, EvolutionStackEntry, HandCard


def is_star_evolution(definition) -> bool:
    """Check if a card definition is a Star Evolution creature (Rule 813)."""
    return False  # Placeholder — Star Evolution card detection requires DB subtype data


def should_apply_star_evo_replacement(creature: Creature) -> bool:
    """
    Check if a creature should apply the Star Evolution top-only leave replacement.
    
    Rule 813.1: A Star Evolution Creature is an Evolution Creature where, when
    leaving the Battle Zone, only the topmost card leaves instead.
    
    Rule 813.1a: The "when leaving" effect is a replacement effect. If another
    replacement effect was applied first, this cannot be applied.
    
    NOTE: This is a placeholder for the replacement effect check. The actual
    Star Evolution subtype detection depends on the card database having the
    "Star Evolution" designation. For now, we check for the _star_evo_replacement
    flag that would be set by the card parser or manually in tests.
    """
    # Must be an Evolution Creature
    if not creature.is_evolution_creature():
        return False
    
    # Check if this creature is flagged as Star Evolution (set by card parser or tests)
    # This will be replaced with proper subtype checking once the DB parser is updated
    if not creature.temp_flags.get("_is_star_evolution", False):
        return False
    
    # Check if another replacement effect already applied (rule 813.1a)
    if creature.temp_flags.get("_replacement_already_applied", False):
        return False
    
    return True



