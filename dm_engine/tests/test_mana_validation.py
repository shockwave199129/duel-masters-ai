"""test_mana_validation.py — Mana civilization validation (Rule 112.2a)."""

import sys
sys.path.insert(0, "dm_engine")

from core.enums import Civilization, ManaUsage
from core.zones import ManaCard
from unittest.mock import MagicMock
from engine.action_generator import _get_mana_combinations


def check(name: str, condition: bool, detail: str = "") -> bool:
    """Print PASS/FAIL."""
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


def make_mana_card(uid: str, civs: frozenset[Civilization]) -> ManaCard:
    """Create a mock ManaCard for testing."""
    definition = MagicMock()
    definition.civilizations = civs
    card = ManaCard(uid=uid, definition=definition)
    card.is_tapped = False
    return card


# ──────────────────────────────────────────────────────────────────────────────

def test_single_civ_single_card():
    """Single Fire civ card covers Fire requirement."""
    mana = [make_mana_card("m1", frozenset([Civilization.FIRE]))]
    combos = _get_mana_combinations(mana, cost=1, card_civs=frozenset([Civilization.FIRE]))
    check("Single civ single card", len(combos) == 1)


def test_multi_civ_requirement():
    """Fire and Light requirement needs unique cards."""
    mana = [
        make_mana_card("m1", frozenset([Civilization.FIRE])),
        make_mana_card("m2", frozenset([Civilization.LIGHT])),
    ]
    combos = _get_mana_combinations(
        mana, 
        cost=2, 
        card_civs=frozenset([Civilization.FIRE, Civilization.LIGHT])
    )
    # Should allow m1 for Fire, m2 for Light
    check("Multi civ requirement", len(combos) > 0)


def test_multi_civ_card_covers_one():
    """Multi-civilization card provides ONE civ, not multiple."""
    mana = [make_mana_card("m1", frozenset([Civilization.FIRE, Civilization.LIGHT]))]
    combos = _get_mana_combinations(
        mana, 
        cost=1, 
        card_civs=frozenset([Civilization.FIRE])
    )
    check("Multi-civ card covers one", len(combos) == 1)


def test_multi_civ_card_cannot_cover_both_alone():
    """Single multi-civ card cannot satisfy two civ requirements."""
    mana = [make_mana_card("m1", frozenset([Civilization.FIRE, Civilization.LIGHT]))]
    combos = _get_mana_combinations(
        mana, 
        cost=2, 
        card_civs=frozenset([Civilization.FIRE, Civilization.LIGHT])
    )
    # m1 can only count as one civilization, not both
    check("Multi-civ card cannot cover both", len(combos) == 0)


def test_no_civ_requirement():
    """Colorless spell uses any mana."""
    mana = [
        make_mana_card("m1", frozenset([Civilization.FIRE])),
        make_mana_card("m2", frozenset([Civilization.LIGHT])),
    ]
    combos = _get_mana_combinations(mana, cost=2, card_civs=frozenset())
    check("Colorless spell", len(combos) > 0)


def test_insufficient_mana():
    """Cannot pay with insufficient mana."""
    mana = [make_mana_card("m1", frozenset([Civilization.FIRE]))]
    combos = _get_mana_combinations(
        mana, 
        cost=3, 
        card_civs=frozenset([Civilization.FIRE])
    )
    check("Insufficient mana", len(combos) == 0)


def test_unmatchable_civ():
    """Cannot cover a civilization not available."""
    mana = [
        make_mana_card("m1", frozenset([Civilization.FIRE])),
        make_mana_card("m2", frozenset([Civilization.FIRE])),
    ]
    combos = _get_mana_combinations(
        mana, 
        cost=2, 
        card_civs=frozenset([Civilization.LIGHT])
    )
    check("Unmatchable civilization", len(combos) == 0)


def test_three_civ_requirement():
    """Three civilization requirement validated correctly."""
    mana = [
        make_mana_card("m1", frozenset([Civilization.FIRE])),
        make_mana_card("m2", frozenset([Civilization.LIGHT])),
        make_mana_card("m3", frozenset([Civilization.WATER])),
    ]
    combos = _get_mana_combinations(
        mana, 
        cost=3, 
        card_civs=frozenset([Civilization.FIRE, Civilization.LIGHT, Civilization.WATER])
    )
    # Each mana card used for its respective civ
    check("Three civ requirement", len(combos) == 1)


def test_duplicate_card_prevention():
    """Same card cannot be used for two different civ requirements."""
    mana = [
        make_mana_card("m1", frozenset([Civilization.FIRE, Civilization.LIGHT])),
        make_mana_card("m2", frozenset([Civilization.FIRE])),
    ]
    combos = _get_mana_combinations(
        mana, 
        cost=2, 
        card_civs=frozenset([Civilization.FIRE, Civilization.LIGHT])
    )
    # m1 can be Fire or Light (but not both), so need m2 for the other
    # Valid: m1 as Fire + m2 (but m2 is single-civ Fire, so can't contribute to Light)
    # Valid: m1 as Light + m2 as Fire
    check("Duplicate card prevention", len(combos) > 0, f"got {len(combos)} combos")


if __name__ == "__main__":
    print("=" * 60)
    print("Mana Civilization Validation Tests (Rule 112.2a)")
    print("=" * 60)
    
    test_single_civ_single_card()
    test_multi_civ_requirement()
    test_multi_civ_card_covers_one()
    test_multi_civ_card_cannot_cover_both_alone()
    test_no_civ_requirement()
    test_insufficient_mana()
    test_unmatchable_civ()
    test_three_civ_requirement()
    test_duplicate_card_prevention()
    
    print("=" * 60)
    print("All mana validation tests completed")
    print("=" * 60)
