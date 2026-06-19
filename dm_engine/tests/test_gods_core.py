"""test_gods_core.py — Gods (rule 804) core linking and validation."""

import sys
sys.path.insert(0, "dm_engine")

from unittest.mock import MagicMock
from engine.god_manager import GodManager
from core.zones import Creature
from core.enums import CardType, CardSubtype


def check(name: str, condition: bool, detail: str = "") -> bool:
    """Print PASS/FAIL."""
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


def make_god_card(card_id: int, god_group: str, has_slots: bool = True):
    """Create a mock God CardDefinition."""
    card = MagicMock()
    card.id = card_id
    card.god_link_group = god_group
    card.god_glink_slots = (("left", "other"),) if has_slots else ()
    return card


def make_creature(card_id: int, god_group: str = None):
    """Create a mock Creature."""
    definition = make_god_card(card_id, god_group) if god_group else MagicMock()
    creature = MagicMock()
    creature.definition = definition
    return creature


# ──────────────────────────────────────────────────────────────────────────────

def test_validate_god_link_matching_group():
    """Two cards in same God group can link."""
    card1 = make_god_card(1, "gods_group_a", has_slots=True)
    card2 = make_god_card(2, "gods_group_a", has_slots=True)
    
    result = GodManager.validate_god_link(card1, card2)
    check("Same group can link", result == True)


def test_validate_god_link_different_group():
    """Cards from different God groups cannot link."""
    card1 = make_god_card(1, "gods_group_a", has_slots=True)
    card2 = make_god_card(2, "gods_group_b", has_slots=True)
    
    result = GodManager.validate_god_link(card1, card2)
    check("Different groups cannot link", result == False)


def test_validate_god_link_no_slots():
    """Cards without G-Link slots cannot link."""
    card1 = make_god_card(1, "gods_group_a", has_slots=False)
    card2 = make_god_card(2, "gods_group_a", has_slots=True)
    
    result = GodManager.validate_god_link(card1, card2)
    check("Missing slots blocks link", result == False)


def test_validate_god_link_not_gods():
    """Non-God cards cannot link."""
    card1 = MagicMock()
    card1.god_link_group = None
    card2 = MagicMock()
    card2.god_link_group = None
    
    result = GodManager.validate_god_link(card1, card2)
    check("Non-Gods cannot link", result == False)


def test_is_valid_god_configuration_two_creatures():
    """Two creatures in same group is valid."""
    creatures = [
        make_creature(1, "gods_group_a"),
        make_creature(2, "gods_group_a"),
    ]
    
    result = GodManager.is_valid_god_configuration(creatures)
    check("Two creatures valid", result == True)


def test_is_valid_god_configuration_four_creatures():
    """Four creatures (2x2 grid) is valid."""
    creatures = [
        make_creature(1, "gods_group_a"),
        make_creature(2, "gods_group_a"),
        make_creature(3, "gods_group_a"),
        make_creature(4, "gods_group_a"),
    ]
    
    result = GodManager.is_valid_god_configuration(creatures)
    check("Four creatures (2x2) valid", result == True)


def test_is_valid_god_configuration_six_creatures():
    """Six creatures is valid."""
    creatures = [make_creature(i, "gods_group_a") for i in range(1, 7)]
    
    result = GodManager.is_valid_god_configuration(creatures)
    check("Six creatures valid", result == True)


def test_is_valid_god_configuration_mixed_groups():
    """Creatures from different groups cannot form valid config."""
    creatures = [
        make_creature(1, "gods_group_a"),
        make_creature(2, "gods_group_b"),
    ]
    
    result = GodManager.is_valid_god_configuration(creatures)
    check("Mixed groups invalid", result == False)


def test_is_valid_god_configuration_single_creature():
    """Single creature is invalid (need at least 2)."""
    creatures = [make_creature(1, "gods_group_a")]
    
    result = GodManager.is_valid_god_configuration(creatures)
    check("Single creature invalid", result == False)


def test_is_valid_god_configuration_five_creatures():
    """Five creatures is invalid (not a valid layout)."""
    creatures = [make_creature(i, "gods_group_a") for i in range(1, 6)]
    
    result = GodManager.is_valid_god_configuration(creatures)
    check("Five creatures invalid", result == False)


def test_is_valid_god_configuration_nine_creatures():
    """Nine creatures (3x3 grid) is valid."""
    creatures = [make_creature(i, "gods_group_a") for i in range(1, 10)]
    
    result = GodManager.is_valid_god_configuration(creatures)
    check("Nine creatures (3x3) valid", result == True)


def test_is_valid_god_configuration_sixteen_creatures():
    """Sixteen creatures (4x4 grid) is valid."""
    creatures = [make_creature(i, "gods_group_a") for i in range(1, 17)]
    
    result = GodManager.is_valid_god_configuration(creatures)
    check("Sixteen creatures (4x4) valid", result == True)


if __name__ == "__main__":
    print("=" * 60)
    print("Gods Core Tests (Rule 804)")
    print("=" * 60)
    
    test_validate_god_link_matching_group()
    test_validate_god_link_different_group()
    test_validate_god_link_no_slots()
    test_validate_god_link_not_gods()
    test_is_valid_god_configuration_two_creatures()
    test_is_valid_god_configuration_four_creatures()
    test_is_valid_god_configuration_six_creatures()
    test_is_valid_god_configuration_mixed_groups()
    test_is_valid_god_configuration_single_creature()
    test_is_valid_god_configuration_five_creatures()
    test_is_valid_god_configuration_nine_creatures()
    test_is_valid_god_configuration_sixteen_creatures()
    
    print("=" * 60)
    print("All Gods core tests completed")
    print("=" * 60)
