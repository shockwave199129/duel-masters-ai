"""
card_validation.py — Decide whether a scraped wiki page is a real OCG card.

Non-card pages (characters, races, packs, decks, civ overview) often appear as
links on set pages. They must not be inserted into the cards table.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# Infobox themes used for wiki/meta articles, not printable cards.
_NON_CARD_INFOBOX_THEMES = (
    "pi-theme-wiki",
    "pi-theme-wikia",
)

# Title substrings that indicate a set/product/meta page, not a single card.
NON_CARD_TITLE_FRAGMENTS = (
    "Gallery",
    "gallery",
    "Talk:",
    "User:",
    "File:",
    "Category:",
    "Template:",
    "Help:",
    "Special:",
    "Wikipedia:",
    "disambiguation",
    "List of",
    "List Of",
    "Cycle/",
    "Block (sets)",
    "Booster pack",
    "Win Era",
    "God of Abyss",
    "Abyss Revolution",
    "OCG",
    "TCG",
    "(Battle Card)",
    "(Character)",
    "(character)",
    "(deck)",
    "(TCG)",
    "(OCG)",
    "(manga)",
    "(anime)",
    " Deck",
    "Deck ",
    "Starter Deck",
    "Thank You Pack",
    " Card Gummy",
    " Prize Pack",
    " Guaranteed Pack",
    " Special Pack",
    " Double Deck",
    " Card Deck",
    "-Card Deck",
    " Deck Builder",
    " Black Box",
    " Memorial Pack",
    " Revival Pack",
    " Climax Pack",
    " Revolution Pack",
    " Civilization",  # e.g. Light Civilization overview
)

META_TITLE_EXACT = frozenset(
    {
        "Block (sets)",
        "Booster pack",
        "OCG",
        "TCG",
        "Win Era",
        "God of Abyss",
        "Abyss Revolution",
        "Civilization",
        "Dragon",
        "Discard",
        "Monster",
        "Phoenix",
        "Oracle",
        "Master Card",
    }
)

SET_TITLE_RE = re.compile(
    r"^(DM\d{2,4}-|DM-|DMR-|DMRP-|DMD-|DMBD-|DMSD-|DMEX-|DMSP-|DMPS-|DMPD-|"
    r"DMPCD-|DMART|DMTG-|DMVS-|S\d+-|DMP-|DMC-|DMS-|DMX-|DMF-|DMT-)",
    re.IGNORECASE,
)


def is_card_wiki_title(title: str) -> bool:
    """Return False for set/product/meta pages before we even fetch them."""
    if not title or len(title) < 2:
        return False
    if title in META_TITLE_EXACT:
        return False
    if SET_TITLE_RE.match(title):
        return False
    if ":" in title:
        return False
    for frag in NON_CARD_TITLE_FRAGMENTS:
        if frag in title:
            return False
    if re.match(r"^\d+[/\d]*$", title):
        return False
    if re.match(r"^DM\d{2}-[A-Z]+\d*\s", title):
        return False
    if re.match(r"^(DM|DMR|DMRP|DMD|DMBD|DMSD|DMEX|DMSP)-\d", title):
        return False
    return True


def wikitable_looks_like_card(fields: dict[str, str]) -> bool:
    """True when a legacy wikitable row set looks like card stats."""
    keys = {k.lower().strip().rstrip(":") for k in fields}
    if "support card" in keys or "supported card" in keys:
        return False
    if "card type" in keys or "mana cost" in keys:
        return True
    if "cost" in keys and ("civilization" in keys or "power" in keys):
        return True
    return False


def infobox_looks_like_card(aside) -> bool:
    """True when a portable infobox is a card infobox, not a character/wiki box."""
    classes = " ".join(aside.get("class", []))
    if any(theme in classes for theme in _NON_CARD_INFOBOX_THEMES):
        # Character/wiki infoboxes need an explicit card type or cost/power.
        type_el = aside.find(attrs={"data-source": re.compile(r"^type$", re.I)})
        type_val = ""
        if type_el:
            val = type_el.find("div", class_="pi-data-value")
            type_val = val.get_text().strip() if val else ""
        if not type_val:
            has_cost = aside.find(attrs={"data-source": re.compile(r"cost|mana", re.I)})
            has_power = aside.find(attrs={"data-source": re.compile(r"power", re.I)})
            if not has_cost and not has_power:
                return False
    return True


def normalize_promo_card(card: Any, fields: Optional[dict] = None) -> Any:
    """
    Fix Cardtable rows that omit type/cost (e.g. Joecard promo).
    Mutates and returns card.
    """
    fields = fields or {}
    if card.card_type != "Unknown":
        return card

    eng = " ".join(card.abilities).lower()
    title_l = card.slug.replace("_", " ").lower()

    if "jokers" in eng or title_l == "joecard":
        card.card_type = "Creature"
        if not card.civilizations:
            card.civilizations = ["Jokers"]
        return card

    if card.power:
        card.card_type = "Creature"
        return card

    return card


def is_valid_raw_card(card: Any) -> bool:
    """Final gate before writing to PostgreSQL."""
    if card.card_type == "Unknown":
        return False
    # Real cards always have a type; cost may be null (King Cell, Zerom, etc.).
    if not card.card_type or card.card_type.strip() == "":
        return False
    return True
