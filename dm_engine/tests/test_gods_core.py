"""test_gods_core.py — Gods (rule 804) core linking and validation."""

import sys
sys.path.insert(0, "dm_engine")

from unittest.mock import MagicMock
from engine.god_manager import GodManager
from core.zones import Creature
from core.cards import CardDefinition
from core.enums import CardType, CardSubtype


def check(name: str, condition: bool, detail: str = "") -> bool:
    """Print PASS/FAIL."""
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


def make_god_card(
    card_id: int,
    god_group: str,
    slug: str | None = None,
    partner_slug: str | None = None,
    has_slots: bool = True,
):
    """Create a mock God CardDefinition."""
    card = MagicMock()
    card.id = card_id
    card.slug = slug or f"god-{card_id}"
    card.god_link_group = god_group
    card.name = f"God {card_id}"
    card.civilizations = frozenset()
    if has_slots and partner_slug:
        card.god_glink_slots = (("left", partner_slug),)
    elif has_slots:
        card.god_glink_slots = (("left", "other"),)
    else:
        card.god_glink_slots = ()
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
    card1 = make_god_card(1, "gods_group_a", slug="god-a", partner_slug="god-b")
    card2 = make_god_card(2, "gods_group_a", slug="god-b", partner_slug="god-a")
    
    result = GodManager.validate_god_link(card1, card2)
    check("Same group can link", result == True)


def test_validate_god_link_different_group():
    """Cards from different God groups cannot link."""
    card1 = make_god_card(1, "gods_group_a", slug="god-a", partner_slug="god-b")
    card2 = make_god_card(2, "gods_group_b", slug="god-b", partner_slug="god-a")
    
    result = GodManager.validate_god_link(card1, card2)
    check("Different groups cannot link", result == False)


def test_validate_god_link_no_slots():
    """Cards without G-Link slots cannot link."""
    card1 = make_god_card(1, "gods_group_a", slug="god-a", has_slots=False)
    card2 = make_god_card(2, "gods_group_a", slug="god-b", partner_slug="god-a")
    
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


def _real_god_def(
    cid: int,
    slug: str,
    group: str,
    partner_slug: str,
    side: str = "right",
) -> CardDefinition:
    from core.enums import CardType, CardSubtype, Civilization
    return CardDefinition(
        id=cid, slug=slug, name=slug.replace("-", " ").title(),
        cost=6, power=6000, card_type=CardType.CREATURE,
        card_subtype=CardSubtype.NONE,
        civilizations=frozenset({Civilization.FIRE}),
        races=frozenset({"God"}),
        keywords=frozenset(),
        effects=tuple(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
        god_link_group=group,
        god_glink_slots=((side, partner_slug),),
    )


def test_link_gods_execution():
    """link_gods merges hand God onto battle-zone God (804.3)."""
    from core.state import GameState, TurnInfo
    from core.enums import Phase
    from core.player_state import PlayerState
    from core.zones import Creature, HandCard
    from engine.god_manager import GodManager

    god_a = _real_god_def(101, "god-a", "test_gods", "god-b", "right")
    god_b = _real_god_def(102, "god-b", "test_gods", "god-a", "left")

    primary = Creature(
        definition=god_a, uid="ga", controller=0, owner=0,
        entered_turn=1, has_summoning_sickness=False,
    )
    hand = HandCard(definition=god_b, uid="gb")
    state = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", battle_zone=[primary], hand=[hand]),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )

    result = GodManager.link_gods(state, 0, primary, hand)
    check("link_gods returns primary", result is primary)
    check("Hand emptied", len(state.players[0].hand) == 0)
    check("Still one BZ entry", len(state.players[0].battle_zone) == 1)
    check("Two linked members", len(primary.linked_cells) == 2)
    check("Aggregated names include both Gods", len(GodManager.get_aggregated_names(primary)) == 2)


def test_god_leave_only_one_card():
    """Rule 804.7: destroying linked God sends only anchor to graveyard."""
    from core.state import GameState, TurnInfo
    from core.enums import Phase
    from core.player_state import PlayerState
    from core.zones import Creature, HandCard
    from engine.god_manager import GodManager
    from engine.zone_mover import move_battle_to_graveyard

    god_a = _real_god_def(201, "god-a2", "leave_gods", "god-b2", "right")
    god_b = _real_god_def(202, "god-b2", "leave_gods", "god-a2", "left")

    primary = Creature(definition=god_a, uid="ga2", controller=0, owner=0, entered_turn=1)
    hand = HandCard(definition=god_b, uid="gb2")
    state = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", battle_zone=[primary], hand=[hand]),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    GodManager.link_gods(state, 0, primary, hand)
    move_battle_to_graveyard(state, 0, primary.uid, reason="effect")

    check("Anchor in graveyard", len(state.players[0].graveyard) == 1)
    check("Detached member remains in BZ", len(state.players[0].battle_zone) == 1)
    check("Detached member is god-b", state.players[0].battle_zone[0].definition.slug == "god-b2")


def test_god_invalid_link_detach_sba():
    """Rule 804.2b: invalid configuration detaches via SBA."""
    from core.state import GameState, TurnInfo
    from core.enums import Phase
    from core.player_state import PlayerState
    from core.zones import Creature
    from engine.sba.actions.god_link import _sba_god_link_invalid_detach

    god_a = _real_god_def(301, "god-x", "bad_gods", "god-y")
    primary = Creature(definition=god_a, uid="gx", controller=0, owner=0, entered_turn=1)
    fake_member = Creature(definition=god_a, uid="gy", controller=0, owner=0, entered_turn=1)
    primary.linked_cells = [primary, fake_member, fake_member, fake_member, fake_member]
    primary.temp_flags["god_linked"] = True  # 5 members — invalid layout count

    state = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", battle_zone=[primary]),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    fired = _sba_god_link_invalid_detach(state)
    check("SBA fired detach", fired is True)
    check("Members split into BZ", len(state.players[0].battle_zone) >= 2)
    check("Primary no longer linked", not GodManager.is_god_link(primary))


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

    test_link_gods_execution()
    test_god_leave_only_one_card()
    test_god_invalid_link_detach_sba()
    
    print("=" * 60)
    print("All Gods core tests completed")
    print("=" * 60)
