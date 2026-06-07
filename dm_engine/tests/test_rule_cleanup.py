"""
tests/test_rule_cleanup.py — deck legality, colorless, and hidden info.
"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.cards import CardDefinition, DeckDefinition
from core.enums import CardSubtype, CardType, Civilization
from core.initializer import initialize_game
from core.observation import Observation
from core.player_state import PlayerState
from core.zones import ShieldCard

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))


def card(cid, name, civs=frozenset([Civilization.FIRE])):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=1, power=1000,
        card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE,
        civilizations=frozenset(civs), races=frozenset(), keywords=frozenset(),
        effects=tuple(), evolution_source_races=frozenset(),
        evolution_source_types=frozenset(), is_multiface=False,
    )


CARDS = [card(i, f"c{i}") for i in range(1, 11)]


def legal_deck():
    counts = {c.id: 4 for c in CARDS}
    return DeckDefinition(
        name="legal",
        owner="p",
        card_counts=counts,
        card_definitions={c.id: c for c in CARDS},
    )


print("\n" + "═"*60)
print("  DM ENGINE — RULE CLEANUP TESTS")
print("═"*60)

check("Civilization has exactly five values", len(list(Civilization)) == 5)
check("Colorless card has no civilizations", card(99, "colorless", frozenset()).civilizations == frozenset())

deck = legal_deck()
state = initialize_game(deck, legal_deck(), first_player=0, seed=1)
check("Legal deck initializes", state.players[0].deck_size == 30)

bad_count = legal_deck()
bad_count.card_counts[1] = 5
bad_count.card_counts[2] = 3
try:
    initialize_game(bad_count, legal_deck(), first_player=0)
    check("5-copy deck rejected", False)
except ValueError:
    check("5-copy deck rejected", True)

bad_size = legal_deck()
bad_size.card_counts[1] = 3
try:
    initialize_game(bad_size, legal_deck(), first_player=0)
    check("39-card deck rejected", False)
except ValueError:
    check("39-card deck rejected", True)

p = PlayerState(
    player_index=0,
    player_name="P0",
    shield_zone=[ShieldCard(definition=CARDS[0]) for _ in range(4)],
    deck_composition={CARDS[0].id: 4},
)
check("Hidden shields not subtracted by default", p.cards_remaining_in_deck_by_id().get(CARDS[0].id) == 4)
check("Internal full-info helper can include shields", p.cards_remaining_in_deck_by_id(True) == {})

obs_state = initialize_game(deck, legal_deck(), first_player=0, seed=2)
obs = Observation.build(obs_state, 0)
check("Observation own remaining does not subtract shields",
      sum(obs.self_state.own_cards_remaining.values()) == 35)

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
