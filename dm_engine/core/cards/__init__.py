"""core/cards — Card data structures.

These are STATIC — they represent the card as printed.
They never change during a game. All game-state changes
(tapped, power modified, etc.) live in the zone objects
in state.py, NOT here.

CardDefinition  — immutable card data loaded from DB once at startup
CardEffect      — one parsed ability row from card_effects table
"""

from .definition import CardEffect, CardDefinition, DeckDefinition
from .helpers import (
    is_zerom,
    is_zerom_creature,
    is_star_evolution,
    is_dream_rare,
    is_duel_mate,
    is_g_castle,
    is_hyper_soul_x,
    is_wd_field,
    is_twinpact,
    is_forbidden,
    get_other_face,
    get_twinpact_characteristics,
    is_hyper_mode,
)

# Re-export constants and enums for backwards compatibility
from ..enums import MAX_DECK_SIZE, MAX_COPIES_PER_CARD
from ..enums import CardType, CardSubtype, EffectAction, TriggerEvent, EffectType, Keyword
