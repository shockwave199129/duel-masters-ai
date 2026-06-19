"""
king_cell.py — Parse King Cell combine relations from wiki wikitext / ability text.

Rule 814: King Cells combine into a King Creature when specified cells are
in hand or mana zone.
"""

from __future__ import annotations

import re
from typing import Optional

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]")
TEXTWRAP_RE = re.compile(r"\{\{Textwrap\|([^}]+)\}\}", re.IGNORECASE | re.DOTALL)
CIV_NAMES = ["Fire", "Water", "Nature", "Light", "Darkness", "Zero", "Jokers"]


def title_to_slug(title: str) -> str:
    return title.strip().replace(" ", "_")


def collect_civilizations_from_params(params: dict[str, str]) -> list[str]:
    """Read civilization, civilization2, civilization3, … from Cardtable params."""
    found: list[str] = []
    for key, val in params.items():
        if key == "civilization" or key == "civilizations":
            pass
        elif key.startswith("civilization") and key[12:].isdigit():
            pass
        else:
            continue
        for civ in CIV_NAMES:
            if civ.lower() in val.lower() and civ not in found:
                found.append(civ)
    return found


def _wikilinks(text: str) -> list[str]:
    return [m.strip() for m in WIKILINK_RE.findall(text)]


def parse_textwrap_combine(wikitext: str) -> Optional[tuple[str, list[str]]]:
    """
    Parse {{Textwrap|…combine…}} on a King Cell page.

    Returns (combined_creature_slug, partner_cell_slugs) or None.
    """
    for match in TEXTWRAP_RE.finditer(wikitext):
        body = match.group(1)
        if "combine" not in body.lower():
            continue
        links = _wikilinks(body)
        if len(links) < 2:
            continue
        # Typical: "…with A and B into C" — target is last link after "into"
        lower = body.lower()
        into_idx = lower.rfind(" into ")
        if into_idx >= 0:
            after_into = body[into_idx + len(" into ") :]
            target_links = _wikilinks(after_into)
            if target_links:
                target_slug = title_to_slug(target_links[-1])
                partners = [title_to_slug(l) for l in links if title_to_slug(l) != target_slug]
                # Drop generic "King Cell" link
                partners = [s for s in partners if not s.lower().endswith("king_cell")]
                if target_slug:
                    return target_slug, partners
        if len(links) >= 2:
            return title_to_slug(links[-1]), [title_to_slug(l) for l in links[:-1]]
    return None


def parse_king_combine_from_abilities(abilities: list[str]) -> Optional[list[str]]:
    """
    Parse King Creature ability listing required King Cells by name.

    e.g. 'If you have the 3 King Cells of "Authority", "Thoughts" and "Fighting Spirit"'
    """
    for ab in abilities:
        lower = ab.lower()
        if "king cell" not in lower:
            continue
        if not any(tok in lower for tok in ("combine", "3 king", "three king", "have the 3")):
            continue
        links = _wikilinks(ab)
        if len(links) < 2:
            # Quoted names without wikilinks
            quoted = re.findall(r'"([^"]+)"', ab)
            if len(quoted) >= 2:
                return [title_to_slug(q) for q in quoted]
            continue
        slugs = [title_to_slug(l) for l in links]
        slugs = [s for s in slugs if not s.lower().endswith("king_cell")]
        into_idx = lower.rfind(" into ")
        if into_idx >= 0:
            after = ab[into_idx:]
            target_links = _wikilinks(after)
            if target_links:
                target_slug = title_to_slug(target_links[-1])
                slugs = [s for s in slugs if s != target_slug]
        if slugs:
            return slugs
    return None


def parse_combine_plain(text: str) -> Optional[tuple[str, list[str]]]:
    """Parse plain-text combine hints (HTML effect field or stripped wikitext)."""
    if "combine" not in text.lower():
        return None
    lower = text.lower()
    into_idx = lower.rfind(" into ")
    if into_idx < 0:
        return None
    before = text[:into_idx]
    after = text[into_idx + len(" into ") :]
    target_name = _clean(after.split(".")[0].split(",")[0])
    if not target_name:
        return None
    partners: list[str] = []
    with_idx = lower.find(" with ")
    if with_idx >= 0:
        partner_section = before[with_idx + len(" with ") :]
        for part in re.split(r"\band\b", partner_section, flags=re.IGNORECASE):
            name = _clean(re.sub(r"\[\[|\]\]", "", part))
            if name and "king cell" not in name.lower():
                partners.append(title_to_slug(name))
    return title_to_slug(target_name), partners


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def infer_king_relations(
    card_type: str,
    wikitext: str,
    abilities: list[str],
) -> tuple[Optional[str], list[str]]:
    """
    Return (king_combine_target_slug, king_combine_required_slugs) for a card.
    """
    ct = (card_type or "").lower()
    target_slug: Optional[str] = None
    required_slugs: list[str] = []

    if ct == "king cell":
        tw = parse_textwrap_combine(wikitext)
        if not tw:
            plain = parse_combine_plain(wikitext) or parse_combine_plain("\n".join(abilities))
            tw = plain
        if tw:
            target_slug = tw[0]

    if ct == "king creature" or parse_king_combine_from_abilities(abilities):
        cells = parse_king_combine_from_abilities(abilities)
        if cells:
            required_slugs = cells

    return target_slug, required_slugs
