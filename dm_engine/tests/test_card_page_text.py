"""
tests/test_card_page_text.py - reading card pages, and holding back any text
update that would lose something. No network or database is used.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "crawler"))

from scripts.refresh_card_text import judge, merge
from scripts.scraper import parse_card_page

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


def row(label, value):
    return f"<tr><td>{label}</td><td>{value}</td></tr>"


PAGE = """<html><h1 id="firstHeading">Test Twin / Test Spell</h1><table class="wikitable">
<tr><th colspan="2">Test Twin</th></tr>
""" + row("Civilization<span>s</span>:", "Darkness / Nature") + row("Card Type:", "Creature") \
    + row("Mana Cost:", "5") + row("English Text:", """
<p><img alt="Blocker"> Blocker</p>
<p>■ Hyperify: Tap one of your other creatures.</p><hr>
<p>■ Double breaker</p>
<p><img alt="Fushigiverse"> Fushigiverse <img alt="DN8"> <i>(It can't cost less than
<img alt="DN2">.)</i></p>
<table><tr><td>Super Soul Cross</td></tr>
<tr><td>■ Each turn, draw a card.</td></tr></table>
""") + row("Power:", "6000") + row("Hyper Power:", "10000") + """
<tr><th colspan="2">Test Spell</th></tr>
""" + row("Card Type:", "Spell") + row("Mana Cost:", "2") \
    + row("Civilization:", "Darkness") + row("English Text:", "■ Draw a card.") \
    + "</table></html>"

card = parse_card_page(PAGE, "X", "https://example.test/wiki/Test_Twin")
check("two sides, not more", card is not None and len(card.faces) == 2,
      str(card and [f["name"] for f in card.faces]))
first, second = (" ".join(face["abilities"]) for face in card.faces)
check("cost icons keep their numbers, other icons are dropped",
      "{DN8}" in first and "{DN2}" in first and "{Blocker}" not in first, first)
check("the Hyper Mode divider is kept", "----" in first)
check("a small table inside the text stays part of that side",
      "Super Soul Cross" in first and "Each turn, draw a card." in first)
check("rows after it still belong to the side",
      card.faces[0]["power"] == "6000"
      and card.faces[0]["fields"].get("hyper power") == "10000")
check("a 'Civilizations' label is read",
      sorted(card.faces[0]["civilizations"]) == ["Darkness", "Nature"])
check("the spell side has its own text", second == "■ Draw a card.")
other_colour = parse_card_page(
    PAGE.replace("<td>Darkness</td>", "<td>Fire</td>"), "X",
    "https://example.test/wiki/Test_Twin")
check("a Twinpact card has only its creature side's civilizations",
      other_colour.faces[1]["civilizations"] == ["Fire"]
      and sorted(other_colour.civilizations) == ["Darkness", "Nature"],
      str(other_colour.civilizations))


def stored(*faces, **extra):
    return {"faces": [{"abilities": list(a)} for a in faces],
            "abilities": [x for a in faces for x in a], "fields": {}, **extra}


# The stored copy lacks lines and has cut-off text: safe to update
old = stored(["■ [[Blocker]]", "■ [[When you put]] this [[creature]], [[draw"],
             ["■ [[Blocker]]"])
page = """<html><table class="wikitable"><tr><th colspan="2">A</th></tr>""" \
    + row("Card Type:", "Creature") + row("Mana Cost:", "3") + row(
        "English Text:", "<p>■ Blocker</p><p>■ When you put this creature, draw a card.</p>"
        "<p>■ Double breaker</p>") \
    + """<tr><th colspan="2">B</th></tr>""" + row("Card Type:", "Spell") \
    + row("Mana Cost:", "2") + row("English Text:", "■ Blocker") + "</table></html>"
fresh = parse_card_page(page, "X", "https://example.test/wiki/A")
verdict, reasons = judge(old, fresh)
check("added and repaired text is an update", verdict == "update", str(reasons))
merged = merge({**old, "name": "A", "fields": {"image": "kept.jpg"}}, fresh)
check("only the text changes; other stored details stay",
      merged["fields"]["image"] == "kept.jpg" and merged["name"] == "A"
      and any("Double breaker" in a for a in merged["abilities"]))
json.dumps(merged)

verdict, reasons = judge(stored(["■ This creature wins every battle."], ["■ Blocker"]), fresh)
check("held back when a stored sentence would disappear",
      verdict == "review" and any("wins every battle" in r for r in reasons), str(reasons))
verdict, reasons = judge(stored(["■ Blocker"], ["■ Blocker"], ["■ Blocker"]), fresh)
check("held back when a side would disappear", verdict == "review", str(reasons))

print(f"\n{len(FAILURES)} failed")
sys.exit(1 if FAILURES else 0)
