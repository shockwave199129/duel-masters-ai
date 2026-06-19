"""
god_link_layout.py — Dynamic G-Link layout (2 / 3 / 4 / 6 positions).

Position axes used across Duel Masters god sets:
  2 — left / right           (e.g. Aku ↔ Zen)
  3 — left / center / right  (classic template gods)
  4 — left / right / top / bottom (King of the Gods)
  6 — planar + initial/center (Emperor, Original God, …)
"""

from __future__ import annotations

from typing import Optional

from scripts.card_link_parser import ParsedCardLinks, god_group_from_name, name_to_slug

PLANAR_SIDES = frozenset({"left", "right", "top", "bottom"})
LINEAR_SIDES = frozenset({"left", "right", "center"})
ALL_GOD_SIDES = PLANAR_SIDES | LINEAR_SIDES | frozenset({"initial"})

# Relative side when self is `self_pos` and partner occupies `partner_pos` in a linear 3-god line.
LINEAR_3_SLOT = {
    ("left", "center"): "right",
    ("left", "right"): "right",
    ("center", "left"): "left",
    ("center", "right"): "right",
    ("right", "center"): "left",
    ("right", "left"): "left",
}

LINEAR_2_SLOT = {
    ("left", "right"): "right",
    ("right", "left"): "left",
}


def positions_from_links(links: ParsedCardLinks) -> frozenset[str]:
    """All directional axes referenced by one card's parsed links."""
    sides = {side for side, _ in links.god_glink_slots}
    sides |= set(links.god_glink_open_sides)
    if links.god_link_position:
        sides.add(links.god_link_position)
    return frozenset(s for s in sides if s in ALL_GOD_SIDES)


def infer_layout_size(positions: frozenset[str]) -> int:
    """
    Classify a god set by how many link axes it uses.

    Returns 0 (unknown), 2, 3, 4, or 6.
    """
    if not positions:
        return 0

    has_lr = "left" in positions or "right" in positions
    has_tb = "top" in positions or "bottom" in positions
    has_center = "center" in positions
    has_initial = "initial" in positions
    planar_count = len(positions & PLANAR_SIDES)

    if has_lr and has_tb:
        if has_initial or has_center:
            return 6
        if planar_count >= 4:
            return 4
        return 6

    if has_center or len(positions & LINEAR_SIDES) >= 3:
        return 3

    if has_lr and not has_tb and not has_center:
        return 2

    if has_initial and planar_count >= 2:
        return 6

    if planar_count >= 4:
        return 4

    return min(6, max(2, len(positions & (PLANAR_SIDES | LINEAR_SIDES))))


def _member_position(
    card_name: str,
    links: ParsedCardLinks,
) -> Optional[str]:
    if links.god_link_position:
        return links.god_link_position
    _, pos = god_group_from_name(card_name)
    return pos


def synthesize_template_glinks(
    group: str,
    members: list[tuple[int, str, str, ParsedCardLinks]],
    layout_size: int,
) -> list[tuple[int, str, str]]:
    """
    Convert template Left/Center/Right gods (no inline engtext) into god_glink rows.

    members: (card_id, slug, card_name, parsed_links)
  Returns rows: (card_id, "side:partner_slug", "god_glink")
    """
    if layout_size not in (2, 3):
        return []

    rows: list[tuple[int, str, str]] = []
    positioned: list[tuple[int, str, str]] = []
    for card_id, slug, name, links in members:
        if links.god_glink_slots or links.god_glink_open_sides:
            return []
        pos = _member_position(name, links)
        if pos:
            positioned.append((card_id, slug, pos))

    if len(positioned) < 2:
        return []

    pos_to_slug = {pos: slug for _, slug, pos in positioned}
    slot_map = LINEAR_2_SLOT if layout_size == 2 else LINEAR_3_SLOT

    for card_id, _slug, self_pos in positioned:
        for partner_pos, partner_slug in pos_to_slug.items():
            if partner_pos == self_pos:
                continue
            side = slot_map.get((self_pos, partner_pos))
            if side:
                rows.append((card_id, f"{side}:{partner_slug}", "god_glink"))

    return rows


def layout_rows_for_group(
    group: str,
    member_card_ids: list[int],
    layout_size: int,
) -> list[tuple[int, str, str]]:
    """Emit god_link_layout relation for every card in a group."""
    if layout_size <= 0:
        return []
    group_slug = name_to_slug(group)
    value = f"{group_slug}:{layout_size}"
    return [(cid, value, "god_link_layout") for cid in member_card_ids]


def aggregate_group_layout(
    members: list[tuple[int, ParsedCardLinks]],
) -> int:
    """Union of all positions used in a god group → layout size."""
    all_positions: set[str] = set()
    for _cid, links in members:
        all_positions |= positions_from_links(links)
    return infer_layout_size(frozenset(all_positions))
