"""core/cards/helpers.py — Card type predicate helpers.

Implements rules: 200.3c (card characteristics), 809 (Forbidden flip),
810 (Twinpact), 812 (Zerom), 813 (Star Evolution), 816 (Hyper Mode),
817 (Dream Rare), 818 (Hyper Soul X stub), 819 (WD Field stub),
820 (Duel Mate), 822 (G-Castle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..enums import CardSubtype, Keyword, EffectAction
from .definition import CardDefinition

if TYPE_CHECKING:
    from ..zones import Creature

# ── Zerom helpers (rule 812) ──────────────────────────────────────────────────

def is_zerom(card_def: CardDefinition) -> bool:
    """Check if a card is a Zerom (double-sided ritual/creature, rule 812)."""
    return card_def.card_subtype == CardSubtype.ZEROM


def is_zerom_creature(creature: "Creature") -> bool:
    """Check if a creature is a Zerom that has been flipped to its creature side."""
    return bool(creature.temp_flags.get("_zerom_flipped"))


# ── Star Evolution helpers (rule 813) ──────────────────────────────────────────

def is_star_evolution(creature: "Creature") -> bool:
    """Check if a creature is a Star Evolution (rule 813)."""
    return bool(creature.temp_flags.get("_is_star_evolution", False))


# ── Dream Rare helpers (rule 817) ─────────────────────────────────────────────

def is_dream_rare(card_def: CardDefinition) -> bool:
    """Check if a card is a Dream Rare (rule 817)."""
    return card_def.card_subtype == CardSubtype.DREAM


# ── Duel Mate helpers (rule 820) ─────────────────────────────────────────────

def is_duel_mate(card_def: CardDefinition) -> bool:
    """Check if a card is a Duel Mate (rule 820)."""
    return card_def.card_subtype == CardSubtype.DUEL_MATE


# ── G-Castle helpers (rule 822) ──────────────────────────────────────────────

def is_g_castle(card_def: CardDefinition) -> bool:
    """Check if a card is a G-Castle (rule 822)."""
    return card_def.card_subtype == CardSubtype.G_CASTLE


# ── Hyper Soul X helpers (rule 818) — STUB ────────────────────────────────────

def is_hyper_soul_x(card_def: CardDefinition) -> bool:
    """Check if a card is a Hyper Soul X (rule 818). STUB: not yet implemented."""
    return card_def.card_subtype == CardSubtype.HYPER_SOUL_X


# ── WD Field helpers (rule 819) — STUB ────────────────────────────────────────

def is_wd_field(card_def: CardDefinition) -> bool:
    """Check if a card is a WD Field (rule 819). STUB: not yet implemented."""
    return card_def.card_subtype == CardSubtype.WD_FIELD


# ── Twinpact / Forbidden flip helpers ──────────────────────────────────────────

def is_twinpact(card_def: CardDefinition) -> bool:
    """Return True if this card is a Twinpact (multi-face) card."""
    return card_def.is_multiface


def is_forbidden(card_def: CardDefinition) -> bool:
    """Return True if this card is a Forbidden or Final Forbidden card."""
    return card_def.card_subtype in (CardSubtype.FORBIDDEN, CardSubtype.FINAL_FORBIDDEN)


def get_other_face(card_def: CardDefinition, card_db=None) -> "CardDefinition | None":
    """
    Resolve the other-face CardDefinition for a multi-face card.

    If card_db is None or other_face_id is None, returns None.
    """
    if card_def.other_face_id is None:
        return None
    if card_db is not None:
        return card_db.get(card_def.other_face_id)
    return None


def get_twinpact_characteristics(card_def: CardDefinition, face: int) -> dict:
    """
    Rule 810.3: Return the characteristics for the chosen face of a Twinpact card.

    Returns dict with keys: cost, power, card_type, card_subtype, civilizations, races, keywords
    """
    if face == 0 or not card_def.twinpact_other_face:
        return {
            "cost": card_def.cost,
            "power": card_def.power,
            "card_type": card_def.card_type,
            "card_subtype": card_def.card_subtype,
            "civilizations": card_def.civilizations,
            "races": card_def.races,
            "keywords": card_def.keywords,
        }

    of = card_def.twinpact_other_face
    return {
        "cost": of.get("cost", card_def.cost),
        "power": of.get("power", card_def.power),
        "card_type": of.get("card_type", card_def.card_type),
        "card_subtype": of.get("card_subtype", card_def.card_subtype),
        "civilizations": of.get("civilizations", card_def.civilizations),
        "races": of.get("races", card_def.races),
        "keywords": of.get("keywords", card_def.keywords),
    }


def is_hyper_mode(card_def: CardDefinition) -> bool:
    """
    Check if a card has Hyper Mode capabilities (rule 816).
    """
    if card_def.other_face_id is None:
        return False
    if Keyword.HYPERIZE in card_def.keywords:
        return True
    return any(e.effect_action == EffectAction.HYPERIZE for e in card_def.effects)
