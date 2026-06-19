"""
card_link_parser.py — Extract multiface / God / Psychic Super links from wiki wikitext.

Wiki templates (above {{Cardtable}}):
  {{Dragheart|from|Other Face}}     lower face of this Dragheart page
  {{Dragheart|to|Other Face}}       higher face of this Dragheart page
  {{Awakened|Awakened Name}}        Psychic awakened face
  {{Awaken|Awakened Name}}          Psychic awakened face (alternate template)
  {{Psychic Super Link|A|B|C}}      cells that form a Psychic Super Creature
  {{Left God Link}} / {{Center God Link}} / {{Right God Link}}
  {{Original Gods}} / {{Emperor of the Gods}}  — god set nav (group key for center cards like Atom)

Inline G-Link (in Cardtable engtext), e.g. Emperor of the Gods:
  ■ [[God Link]] (Ana or Suva) Left Side (Moora) Top Side {{God Link}}
  ■ [[God Link]] (Lepton) Left Side or (Quark) Right Side or (Atom) Bottom Side
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

DRAGHEART_FROM_RE = re.compile(r"\{\{Dragheart\|from\|([^}|]+)", re.IGNORECASE)
DRAGHEART_TO_RE = re.compile(r"\{\{Dragheart\|to\|([^}|]+)", re.IGNORECASE)
AWAKENED_RE = re.compile(r"\{\{Awakened\|([^}|]+)", re.IGNORECASE)
AWAKEN_RE = re.compile(r"\{\{Awaken\|([^}|]+)", re.IGNORECASE)
PSYCHIC_SUPER_RE = re.compile(r"\{\{Psychic Super Link\|([^}]+)\}\}", re.IGNORECASE)
LEFT_GOD_RE = re.compile(r"\{\{Left God Link", re.IGNORECASE)
CENTER_GOD_RE = re.compile(r"\{\{Center God Link", re.IGNORECASE)
RIGHT_GOD_RE = re.compile(r"\{\{Right God Link", re.IGNORECASE)

GOD_NAME_RE = re.compile(r"^(.*)\s+(Left|Right|Center)\s+God\s*$", re.IGNORECASE)
GOD_CENTER_NAME_RE = re.compile(r"^(.*)\s+God\s*$", re.IGNORECASE)
GOD_NAME_CATEGORY_RE = re.compile(r",\s*([^,]+(?:\s+God[s]?)?)\s*$", re.IGNORECASE)

# Nav templates at the bottom of god set card pages (e.g. Atom, King Gods nav).
GOD_GROUP_NAV_RE = re.compile(
    r"\{\{([^}|#]+(?:\s+Gods?|\s+Godkind))\}\}",
    re.IGNORECASE,
)
GOD_GROUP_TEMPLATE_ALIASES = {
    "original gods": "the Original God",
    "original god": "the Original God",
    "emperor of the gods": "Emperor of the Gods",
    "king of the gods": "King of the Gods",
    "king gods": "King of the Gods",
    "super godkind": "Super Godkind",
}

INLINE_GOD_LINK_BLOCK_RE = re.compile(
    r"\[\[(?:God Link|Tri God Link)\]\]\s*(.+?)(?=\n\s*■|\Z)",
    re.IGNORECASE | re.DOTALL,
)
WIKI_CARD_NAME_RE = re.compile(
    r"\{\{tooltip\|([^}|]+)(?:\|[^}]*)?\}\}|\[\[([^\]|]+)(?:\|[^\]]+)?\]\]",
    re.IGNORECASE,
)

SIDE_ALIASES = {
    "left side": "left",
    "right side": "right",
    "top side": "top",
    "bottom side": "bottom",
    "center": "center",
    "middle": "center",
    "initial": "initial",
}


def name_to_slug(name: str) -> str:
    return name.strip().replace(" ", "_")


@dataclass
class ParsedCardLinks:
    """Link hints extracted from one card's wikitext."""

    other_face_slugs: list[str] = field(default_factory=list)
    psychic_super_slugs: list[str] = field(default_factory=list)
    god_link_position: Optional[str] = None   # left | center | right (template gods)
    god_link_group: Optional[str] = None      # name category / shared prefix
    god_glink_slots: list[tuple[str, str]] = field(default_factory=list)  # (side, slug)
    god_glink_open_sides: list[str] = field(default_factory=list)           # open sides
    is_multiface_hint: bool = False


def _clean_template_arg(raw: str) -> str:
    return raw.strip().strip("|").strip()


def _normalize_side(raw: Optional[str]) -> str:
    if not raw:
        return "initial"
    key = raw.strip().lower()
    return SIDE_ALIASES.get(key, key.replace(" side", ""))


def _extract_card_names(text: str) -> list[str]:
    names: list[str] = []
    for match in WIKI_CARD_NAME_RE.finditer(text):
        name = (match.group(1) or match.group(2) or "").strip()
        if name and name.lower() not in {"god link", "g-link"}:
            names.append(name)
    if not names:
        for part in re.split(r"\s+or\s+|\s+and\s+", text, flags=re.IGNORECASE):
            cleaned = part.strip().strip(",")
            if cleaned and not cleaned.lower().startswith("left"):
                names.append(cleaned)
    return names


def god_name_category(name: str) -> Optional[str]:
    """
  Return a God name category from the card name suffix.
  e.g. 'Adge, Emperor of the Gods' → 'Emperor of the Gods'
    """
    m = GOD_NAME_CATEGORY_RE.search(name.strip())
    if m:
        suffix = m.group(1).strip()
        if "god" in suffix.lower():
            return suffix
    return None


def god_group_from_wikitext(wikitext: str) -> Optional[str]:
    """Read god set from nav templates like {{Original Gods}} on the card page."""
    match = GOD_GROUP_NAV_RE.search(wikitext)
    if not match:
        return None
    key = match.group(1).strip().lower()
    return GOD_GROUP_TEMPLATE_ALIASES.get(key, match.group(1).strip())


def god_group_from_name(name: str) -> tuple[Optional[str], Optional[str]]:
    """
    Return (group_key, position) for Left/Center/Right God creatures.
    position is left|center|right or None.
    """
    m = GOD_NAME_RE.match(name.strip())
    if m:
        return m.group(1).strip(), m.group(2).lower()
    m2 = GOD_CENTER_NAME_RE.match(name.strip())
    if m2 and not re.search(r"\b(Left|Right|Center)\b", name, re.I):
        return m2.group(1).strip(), "center"
    return None, None


def _engtext_from_wikitext(wikitext: str) -> str:
    match = re.search(
        r"\| engtext\s*=\s*(.*?)(?=\n\||\n\}\})",
        wikitext,
        re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _parse_inline_god_link_block(block: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Parse one [[God Link]] clause body (text after the keyword)."""
    slots: list[tuple[str, str]] = []
    open_sides: list[str] = []

    pair_re = re.compile(
        r"\(([^)]+)\)\s*(Left Side|Right Side|Top Side|Bottom Side|Center|Middle)\b",
        re.IGNORECASE,
    )
    pos_name_re = re.compile(
        r"\b(Left Side|Right Side|Top Side|Bottom Side|Center|Middle)\s*\(([^)]+)\)",
        re.IGNORECASE,
    )
    has_pos_name = bool(pos_name_re.search(block))

    if has_pos_name:
        first_side = re.search(
            r"\b(Left Side|Right Side|Top Side|Bottom Side|Center|Middle)\b",
            block,
            re.IGNORECASE,
        )
        if first_side:
            prefix = block[: first_side.start()]
            for match in re.finditer(r"\(([^)]+)\)", prefix):
                for name in _extract_card_names(match.group(1)):
                    slug = name_to_slug(name)
                    if slug:
                        slots.append(("initial", slug))
        for match in pos_name_re.finditer(block):
            side = _normalize_side(match.group(1))
            for name in _extract_card_names(match.group(2)):
                slots.append((side, name_to_slug(name)))
    else:
        for match in pair_re.finditer(block):
            side = _normalize_side(match.group(2))
            for name in _extract_card_names(match.group(1)):
                slots.append((side, name_to_slug(name)))

    side_kw = r"\b(Left Side|Right Side|Top Side|Bottom Side|Center|Middle)\b"
    side_matches = list(re.finditer(side_kw, block, re.IGNORECASE))
    for i, side_match in enumerate(side_matches):
        side = _normalize_side(side_match.group(1))
        rest_start = side_match.end()
        rest_end = (
            side_matches[i + 1].start() if i + 1 < len(side_matches) else len(block)
        )
        segment = block[rest_start:rest_end]
        if "{{God Link}}" in segment and not re.search(r"\([^)]+\)", segment):
            if side not in open_sides:
                open_sides.append(side)

    if re.search(r",\s*Middle\b", block, re.IGNORECASE) and "center" not in open_sides:
        open_sides.append("center")

    return slots, open_sides


def parse_inline_god_link(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """
    Parse ■ [[God Link]] (...) Left Side (...) Top Side style abilities.

    Handles both wiki phrasings:
      (Ana or Suva) Left Side (Moora) Top Side     — Emperor of the Gods
      (Lepton) Left Side or (Quark) Right Side     — Original Gods (6-card set)
      (Heavy) Right Side and (Metal) Left Side, Middle
    """
    slots: list[tuple[str, str]] = []
    open_sides: list[str] = []
    seen_slots: set[tuple[str, str]] = set()

    for block_match in INLINE_GOD_LINK_BLOCK_RE.finditer(text):
        block_slots, block_open = _parse_inline_god_link_block(block_match.group(1))
        for slot in block_slots:
            if slot not in seen_slots:
                seen_slots.add(slot)
                slots.append(slot)
        for side in block_open:
            if side not in open_sides:
                open_sides.append(side)

    return slots, open_sides


def parse_card_links(wikitext: str, card_name: str = "") -> ParsedCardLinks:
    """Parse all supported link templates from raw wikitext."""
    result = ParsedCardLinks()
    if not wikitext:
        return result

    for pattern in (DRAGHEART_FROM_RE, DRAGHEART_TO_RE, AWAKENED_RE, AWAKEN_RE):
        for match in pattern.finditer(wikitext):
            slug = name_to_slug(_clean_template_arg(match.group(1)))
            if slug and slug not in result.other_face_slugs:
                result.other_face_slugs.append(slug)
                result.is_multiface_hint = True

    super_match = PSYCHIC_SUPER_RE.search(wikitext)
    if super_match:
        parts = [_clean_template_arg(p) for p in super_match.group(1).split("|")]
        for part in parts:
            if not part:
                continue
            slug = name_to_slug(part)
            if slug not in result.psychic_super_slugs:
                result.psychic_super_slugs.append(slug)
        result.is_multiface_hint = True

    if LEFT_GOD_RE.search(wikitext):
        result.god_link_position = "left"
    elif RIGHT_GOD_RE.search(wikitext):
        result.god_link_position = "right"
    elif CENTER_GOD_RE.search(wikitext):
        result.god_link_position = "center"

    engtext = _engtext_from_wikitext(wikitext)
    if engtext:
        slots, open_sides = parse_inline_god_link(engtext)
        if slots or open_sides:
            result.god_glink_slots = slots
            result.god_glink_open_sides = open_sides

    if card_name:
        category = god_name_category(card_name)
        if category:
            result.god_link_group = category
        else:
            group, pos_from_name = god_group_from_name(card_name)
            if group:
                result.god_link_group = group
                if not result.god_link_position and pos_from_name:
                    result.god_link_position = pos_from_name
        if not result.god_link_group:
            nav_group = god_group_from_wikitext(wikitext)
            if nav_group:
                result.god_link_group = nav_group
        if (
            not result.god_link_position
            and "divine core" in card_name.lower()
            and result.god_glink_slots
        ):
            result.god_link_position = "center"

    if result.god_glink_slots or result.god_link_position or result.god_link_group:
        result.is_multiface_hint = True

    return result
