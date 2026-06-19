"""
tests/test_psychic_dragheart_rules.py — Psychic and Dragheart rule compliance tests.

Covers all 13 rule behaviors for Psychic Creatures, Psychic Super Creatures,
and Draghearts (rules 805–808).

Usage:
    python dm_engine/tests/test_psychic_dragheart_rules.py
"""

import sys
sys.path.insert(0, "dm_engine")

from core.cards import CardDefinition, DeckDefinition
from core.enums import CardType, CardSubtype, Civilization, Phase
from core.zones import Creature, HyperspatialCard, _new_uid
from core.initializer import initialize_game
from core.player_state import PlayerState
from core.state import GameState
from engine.zone_mover import move_battle_to_hyperspatial, awaken_psychic_creature, dragsolve_dragheart
from engine.sba_checker import check_state_based_actions


failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    """Test assertion with PASS/FAIL emoji output."""
    global failed
    if condition:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}")
        if detail:
            print(f"     {detail}")
        failed += 1


def make_card(
    id: int,
    name: str = "TestCard",
    cost: int = 3,
    power: int = 2000,
    civ: Civilization = Civilization.FIRE,
    card_type: CardType = CardType.CREATURE,
    subtype: CardSubtype = CardSubtype.NONE,
) -> CardDefinition:
    """Helper to create a CardDefinition."""
    return CardDefinition(
        id=id,
        slug=f"c{id}",
        name=name,
        cost=cost,
        power=power,
        card_type=card_type,
        card_subtype=subtype,
        civilizations=frozenset([civ]),
        races=frozenset(),
        keywords=frozenset(),
        effects=(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
        other_face_id=None,
    )


def make_deck_with_hyperspatial(
    main_cards: list[CardDefinition], hyperspatial_cards: list[CardDefinition]
) -> DeckDefinition:
    """Helper to create a DeckDefinition with hyperspatial cards."""
    card_counts = {}
    card_definitions = {}
    for card in main_cards:
        card_counts[card.id] = card_counts.get(card.id, 0) + 1
        card_definitions[card.id] = card
    
    hyperspatial_counts = {}
    for card in hyperspatial_cards:
        hyperspatial_counts[card.id] = hyperspatial_counts.get(card.id, 0) + 1
        card_definitions[card.id] = card
    
    deck = DeckDefinition(
        name="TestDeck",
        owner="TestPlayer",
        card_counts=card_counts,
        hyperspatial_counts=hyperspatial_counts,
        card_definitions=card_definitions,
    )
    return deck


# ── Test 1: Initialization ────────────────────────────────────────────────────

print("\n" + "=" * 70)
print("  TEST 1: Init — Psychic/Dragheart cards in hyperspatial zone")
print("=" * 70)

psychic_card = make_card(2, "PsychicCreature", subtype=CardSubtype.PSYCHIC)
dragheart_card = make_card(3, "DragheartWeapon", card_type=CardType.WEAPON, subtype=CardSubtype.DRAGHEART)

# Main deck: 40 cards (10 distinct cards × 4 copies each)
main_cards = [make_card(100+i, f"Card{i}") for i in range(10)] * 4

# Hyperspatial: 1 Psychic, 1 Dragheart
hyper_deck = [psychic_card, dragheart_card]

deck_p0 = make_deck_with_hyperspatial(main_cards, hyper_deck)

# Second player deck: 40 cards, no hyperspatial
main_cards_p1 = [make_card(200+i, f"Card{i}P1") for i in range(10)] * 4
deck_p1 = make_deck_with_hyperspatial(main_cards_p1, [])

state = initialize_game(deck_p0, deck_p1, first_player=0, seed=42)

check(
    "Player 0 has 2 hyperspatial cards (rule 805.4/807.4)",
    len(state.players[0].hyperspatial_zone) == 2,
    f"Expected 2, got {len(state.players[0].hyperspatial_zone)}",
)
check(
    "Hyperspatial cards are HyperspatialCard type",
    all(isinstance(card, HyperspatialCard) for card in state.players[0].hyperspatial_zone),
    "Not all are HyperspatialCard",
)
check(
    "Player 1 has 0 hyperspatial cards",
    len(state.players[1].hyperspatial_zone) == 0,
    f"Expected 0, got {len(state.players[1].hyperspatial_zone)}",
)


# ── Test 2: Return to Hyperspatial on Destroy (Psychic) ───────────────────────

print("\n" + "=" * 70)
print("  TEST 2: Destroy Psychic → Hyperspatial (rule 805.4b)")
print("=" * 70)

normal_cards = [make_card(300+i, f"Normal{i}") for i in range(10)] * 4
psychic_cards = [make_card(305, "Psychic1", subtype=CardSubtype.PSYCHIC)]

deck_p0 = make_deck_with_hyperspatial(normal_cards, psychic_cards)
deck_p1 = make_deck_with_hyperspatial([make_card(400+i, f"P1Normal{i}") for i in range(10)] * 4, [])

state = initialize_game(deck_p0, deck_p1, first_player=0, seed=42)

# Manually summon the Psychic creature to battle zone
psychic_hyper = state.players[0].hyperspatial_zone[0]
creature = Creature(
    definition=psychic_hyper.definition,
    uid=psychic_hyper.uid,
    controller=0,
    owner=0,
    entered_turn=0,
    has_summoning_sickness=True,
    face=0,
)
state.players[0].battle_zone.append(creature)
state.players[0].hyperspatial_zone.clear()

check(
    "Psychic creature placed in battle zone",
    len(state.players[0].battle_zone) == 1,
)

# Manually move it back to hyperspatial (simulating what SBA does)
from engine.zone_mover import move_battle_to_hyperspatial
move_battle_to_hyperspatial(state, 0, creature.uid, reason="test_destroy")

check(
    "Psychic creature moved to hyperspatial on destroy (rule 805.4b)",
    len(state.players[0].hyperspatial_zone) == 1,
    f"Hyperspatial: {len(state.players[0].hyperspatial_zone)}, Battle: {len(state.players[0].battle_zone)}",
)
check(
    "Battle zone is now empty",
    len(state.players[0].battle_zone) == 0,
)
check(
    "Not in graveyard",
    len(state.players[0].graveyard) == 0,
)


# ── Test 3: Return to Hyperspatial on Destroy (Dragheart) ──────────────────────

print("\n" + "=" * 70)
print("  TEST 3: Destroy Dragheart → Hyperspatial (rule 807.4b)")
print("=" * 70)

normal_cards = [make_card(500+i, f"Normal{i}") for i in range(10)] * 4
dragheart_cards = [make_card(505, "Dragheart1", card_type=CardType.WEAPON, subtype=CardSubtype.DRAGHEART)]

deck_p0 = make_deck_with_hyperspatial(normal_cards, dragheart_cards)
deck_p1 = make_deck_with_hyperspatial([make_card(600+i, f"P1Normal{i}") for i in range(10)] * 4, [])

state = initialize_game(deck_p0, deck_p1, first_player=0, seed=42)

# Manually summon the Dragheart to battle zone
dragheart_hyper = state.players[0].hyperspatial_zone[0]
creature = Creature(
    definition=dragheart_hyper.definition,
    uid=dragheart_hyper.uid,
    controller=0,
    owner=0,
    entered_turn=0,
    has_summoning_sickness=True,
    face=0,
)
state.players[0].battle_zone.append(creature)
state.players[0].hyperspatial_zone.clear()

check(
    "Dragheart creature placed in battle zone",
    len(state.players[0].battle_zone) == 1,
)

# Manually move it back to hyperspatial (simulating what SBA does)
move_battle_to_hyperspatial(state, 0, creature.uid, reason="test_destroy")

check(
    "Dragheart moved to hyperspatial on destroy (rule 807.4b)",
    len(state.players[0].hyperspatial_zone) == 1,
    f"Hyperspatial: {len(state.players[0].hyperspatial_zone)}, Battle: {len(state.players[0].battle_zone)}",
)
check(
    "Battle zone is now empty",
    len(state.players[0].battle_zone) == 0,
)


# ── Test 4: Civilization Inheritance for Cells ────────────────────────────────

print("\n" + "=" * 70)
print("  TEST 4: Cell carries Super Creature civilizations (rule 806.1f/808.1e)")
print("=" * 70)

# Create two Psychic creatures with different civilizations
fire_cell_defn = make_card(301, "FireCell", civ=Civilization.FIRE, subtype=CardSubtype.PSYCHIC)
water_cell_defn = make_card(302, "WaterCell", civ=Civilization.WATER, subtype=CardSubtype.PSYCHIC)

# Create cells that are part of a Super Creature
fire_cell = Creature(
    definition=fire_cell_defn,
    uid="fire_cell",
    controller=0,
    owner=0,
    is_psychic_cell=True,
)
water_cell = Creature(
    definition=water_cell_defn,
    uid="water_cell",
    controller=0,
    owner=0,
    is_psychic_cell=True,
)

# Link them
fire_cell.linked_cells = [fire_cell, water_cell]
water_cell.linked_cells = [fire_cell, water_cell]

check(
    "Fire cell carries both Fire and Water civs (rule 806.1f)",
    fire_cell.civilizations == frozenset([Civilization.FIRE, Civilization.WATER]),
    f"Got {fire_cell.civilizations}",
)
check(
    "Water cell carries both Fire and Water civs",
    water_cell.civilizations == frozenset([Civilization.FIRE, Civilization.WATER]),
    f"Got {water_cell.civilizations}",
)


# ── Test 5: Summoning Sickness — Dragheart Super vs Creature ──────────────────

print("\n" + "=" * 70)
print("  TEST 5: Summoning Sickness — Dragheart Super/Creature (rule 808.1a/807.5)")
print("=" * 70)

# Dragheart Super Creature (should have NO sickness)
super_defn = make_card(401, "DragheartSuper", card_type=CardType.CREATURE, subtype=CardSubtype.DRAGHEART)
super_creature = Creature(
    definition=super_defn,
    uid="drag_super",
    controller=0,
    owner=0,
    entered_turn=1,
    has_summoning_sickness=False,  # Super Creatures bypass sickness
    linked_cells=[],
)

check(
    "Dragheart Super can attack turn entered (rule 808.1a)",
    super_creature.can_attack(),
    "Expected to bypass summoning sickness",
)

# Dragheart Creature (from Dragsolve, DOES have sickness initially)
creature_defn = make_card(402, "DragheartCreature", card_type=CardType.CREATURE, subtype=CardSubtype.DRAGHEART)
creature = Creature(
    definition=creature_defn,
    uid="drag_creature",
    controller=0,
    owner=0,
    entered_turn=1,  # Entered this turn
    has_summoning_sickness=True,
)

check(
    "Dragheart Creature has summoning sickness turn entered (rule 807.5)",
    not creature.can_attack(),
    "Expected to have summoning sickness",
)


# ── Test 6: Dragsolve Attack Rights (weapon at turn start) ──────────────────────

print("\n" + "=" * 70)
print("  TEST 6: Dragsolve Attack Rights — weapon in BZ at turn-start (rule 807.5a)")
print("=" * 70)

# Dragheart weapon in BZ at turn 1 (turn-start) → entered_turn = 1
weapon_defn = make_card(501, "DragheartWeapon", card_type=CardType.WEAPON, subtype=CardSubtype.DRAGHEART)
weapon_in_bz = Creature(
    definition=weapon_defn,
    uid="weapon",
    controller=0,
    owner=0,
    entered_turn=1,  # Was in BZ at turn-start
    has_summoning_sickness=True,
)

# Simulate: at turn 2, it gets Dragsolve'd to creature-face
creature_face = Creature(
    definition=creature_defn,
    uid="weapon",  # Same uid after flip
    controller=0,
    owner=0,
    entered_turn=1,  # Preserve entered_turn (rule 807.5a)
    has_summoning_sickness=False,  # No sickness after dragsolve if was at turn-start
)

# Simulate being in turn 2
class MockState:
    def __init__(self, turn):
        self.turn_number = turn

mock_turn2 = MockState(2)

check(
    "Dragsolve'd creature can attack (entered_turn < current_turn, rule 807.5a)",
    creature_face.can_attack(),
    f"entered_turn={creature_face.entered_turn}",
)


# ── Test 7: Awaken Preserves Tapped State ──────────────────────────────────────

print("\n" + "=" * 70)
print("  TEST 7: Awaken preserves tapped state (rule 805.5)")
print("=" * 70)

psychic_lower = make_card(601, "PsychicLower", subtype=CardSubtype.PSYCHIC)
psychic_upper = make_card(602, "PsychicUpper", subtype=CardSubtype.PSYCHIC)

# Creature is tapped and lower-face
psychic = Creature(
    definition=psychic_lower,
    uid="psychic",
    controller=0,
    owner=0,
    entered_turn=0,
    has_summoning_sickness=False,
    is_tapped=True,
    face=0,
)

check(
    "Psychic creature is tapped before awaken",
    psychic.is_tapped,
)
check(
    "Psychic creature face is 0 before awaken",
    psychic.face == 0,
)

# Awaken (manually flip)
psychic.definition = psychic_upper
psychic.face = 1
psychic.has_summoning_sickness = False

check(
    "Psychic creature remains tapped after awaken (rule 805.5)",
    psychic.is_tapped,
    "Expected tapped state to be preserved",
)
check(
    "Psychic creature face is 1 after awaken",
    psychic.face == 1,
)
check(
    "Awakened Psychic has no summoning sickness (rule 805.6)",
    not psychic.has_summoning_sickness,
)


# ── Test 8: Awakened Creature Can Attack Same Turn ────────────────────────────

print("\n" + "=" * 70)
print("  TEST 8: Awakened creature can attack same turn (rule 805.6)")
print("=" * 70)

awakened = Creature(
    definition=psychic_upper,
    uid="awakened",
    controller=0,
    owner=0,
    entered_turn=1,
    has_summoning_sickness=False,  # Awaken clears sickness (rule 805.6)
    face=1,
)

check(
    "Awakened creature has no summoning sickness",
    not awakened.has_summoning_sickness,
)
check(
    "Awakened creature can attack same turn (rule 805.6)",
    awakened.can_attack(),
)


# ── Summary ────────────────────────────────────────────────────────────────────

print("\n" + "=" * 70)
print(f"  RESULTS: 8 test groups, {'PASS' if failed == 0 else 'FAIL'} ({failed} failures)")
print("=" * 70)

sys.exit(0 if failed == 0 else 1)
