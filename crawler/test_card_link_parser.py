"""Tests for inline G-Link parsing (Emperor of the Gods, Original Gods)."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from scripts.card_link_parser import (  # noqa: E402
    god_name_category,
    parse_card_links,
    parse_inline_god_link,
)
from scripts.god_link_layout import (  # noqa: E402
    aggregate_group_layout,
    infer_layout_size,
    positions_from_links,
)

PASS = FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}: {detail}")


def test_emperor_adge():
    engtext = (
        "■ [[God Link]] (Ana or Suva) Left Side (Moora) Top Side {{God Link}}\n"
        "■ [[God Link]] (Moora) Right Side (Ana or Suva) Bottom Side {{God Link}}"
    )
    slots, open_sides = parse_inline_god_link(engtext)
    check(
        "Adge initial partners",
        ("initial", "Ana") in slots and ("initial", "Suva") in slots,
        str(slots),
    )
    check("Adge left Moora", ("left", "Moora") in slots, str(slots))
    check("Adge open top", "top" in open_sides, str(open_sides))


def test_original_neutron():
    engtext = (
        "■ [[God Link]] (Lepton) Left Side or (Quark) Right Side "
        "or (Atom) Bottom Side {{God Link}}"
    )
    slots, open_sides = parse_inline_god_link(engtext)
    check("Neutron left Lepton", ("left", "Lepton") in slots, str(slots))
    check("Neutron right Quark", ("right", "Quark") in slots, str(slots))
    check("Neutron bottom Atom", ("bottom", "Atom") in slots, str(slots))


def test_original_death():
    engtext = (
        "■ [[God Link]] (Heavy) Right Side and (Metal) Left Side, Middle"
    )
    slots, open_sides = parse_inline_god_link(engtext)
    check("Death right Heavy", ("right", "Heavy") in slots, str(slots))
    check("Death left Metal", ("left", "Metal") in slots, str(slots))
    check("Death open center", "center" in open_sides, str(open_sides))


def test_god_name_category():
    check(
        "Emperor category",
        god_name_category("Adge, Emperor of the Gods") == "Emperor of the Gods",
        god_name_category("Adge, Emperor of the Gods"),
    )


def test_parse_card_links_group():
    wikitext = (
        "{{Cardtable\n"
        "| engtext = ■ [[God Link]] (Ana or Suva) Left Side (Moora) Top Side {{God Link}}\n"
        "}}\n"
        "{{Emperor of the Gods}}\n"
    )
    links = parse_card_links(wikitext, "Adge, Emperor of the Gods")
    check("group from name", links.god_link_group == "Emperor of the Gods", links.god_link_group)
    check("has glink slots", len(links.god_glink_slots) >= 3, links.god_glink_slots)


def test_original_god_group_from_template():
    wikitext = (
        "{{Cardtable\n"
        "| engtext = ■ [[God Link]] ({{tooltip|Proton, the Original God}}) Left Side, "
        "({{tooltip|Electron, the Original God}}) Right Side, or "
        "({{tooltip|Neutron, the Original God}}) Top Side\n"
        "}}\n"
        "{{Original Gods}}\n"
    )
    links = parse_card_links(wikitext, "Atom, the Divine Core")
    check("Atom group from nav", links.god_link_group == "the Original God", links.god_link_group)
    check("Atom center position", links.god_link_position == "center", links.god_link_position)
    check("Atom left Proton", ("left", "Proton,_the_Original_God") in links.god_glink_slots, links.god_glink_slots)


def test_neutron_original_god_slots():
    engtext = (
        "■ [[God Link]] ({{tooltip|Lepton, the Original God}}) Left Side or "
        "({{tooltip|Quark, the Original God}}) Right Side or "
        "({{tooltip|Atom, the Divine Core}}) Bottom Side {{God Link}}"
    )
    slots, open_sides = parse_inline_god_link(engtext)
    check("Neutron 3 partners", len(slots) == 3, str(slots))
    check("Neutron group partners", ("bottom", "Atom,_the_Divine_Core") in slots, str(slots))


def test_layout_sizes():
    king_cards = [
        "■ [[God Link]] (Othello) Right Side or (Titus) Bottom Side {{God Link}}",
        "■ [[God Link]] (Titus) Left Side or (Othello) Top Side {{God Link}}",
        "■ [[God Link]] (Lear) Left Side or (Macbeth) Bottom Side {{God Link}}",
        "■ [[God Link]] (Macbeth) Right Side or (Lear) Top Side {{God Link}}",
    ]
    king_members = []
    for eng in king_cards:
        slots, open_sides = parse_inline_god_link(eng)
        king_members.append((
            len(king_members),
            type("L", (), {
                "god_glink_slots": slots,
                "god_glink_open_sides": open_sides,
                "god_link_position": None,
            })(),
        ))
    check("King Gods layout 4", aggregate_group_layout(king_members) == 4, str(king_members))

    two_eng = "■ [[God Link]] (Zen) Left Side"
    slots2, _ = parse_inline_god_link(two_eng)
    pos2 = positions_from_links(
        type("L", (), {
            "god_glink_slots": slots2,
            "god_glink_open_sides": [],
            "god_link_position": None,
        })()
    )
    check("2-god layout", infer_layout_size(pos2) == 2, str(pos2))

    emperor = parse_card_links(
        "{{Cardtable\n| engtext = ■ [[God Link]] (Ana or Suva) Left Side (Moora) Top Side {{God Link}}\n}}\n",
        "Adge, Emperor of the Gods",
    )
    group_members = [(1, emperor)]
    check("Emperor layout 6", aggregate_group_layout(group_members) == 6, emperor.god_glink_slots)


def test_king_gods_parse():
    engtext = (
        "■ [[God Link]] ({{tooltip|Othello, King of the Gods}}) Right Side or "
        "({{tooltip|Titus, King of the Gods}}) Bottom Side {{God Link}}"
    )
    slots, open_sides = parse_inline_god_link(engtext)
    check("Lear right Othello", ("right", "Othello,_King_of_the_Gods") in slots, str(slots))
    check("Lear bottom Titus", ("bottom", "Titus,_King_of_the_Gods") in slots, str(slots))
    check("Lear open bottom", "bottom" in open_sides, str(open_sides))
    links = parse_card_links(
        "{{Cardtable\n| engtext = " + engtext + "\n}}\n{{King of the Gods}}\n",
        "Lear, King of the Gods",
    )
    check("Lear group", links.god_link_group == "King of the Gods", links.god_link_group)


if __name__ == "__main__":
    print("card_link_parser tests")
    test_emperor_adge()
    test_original_neutron()
    test_original_death()
    test_god_name_category()
    test_parse_card_links_group()
    test_original_god_group_from_template()
    test_neutron_original_god_slots()
    test_layout_sizes()
    test_king_gods_parse()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
