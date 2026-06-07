"""
tests/test_prebuilt_decks.py — prebuilt deck simulation smoke test.
"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
import tempfile

from bot.random_bot import RandomBot
from core.cards import DeckDefinition
from core.initializer import initialize_game
from decks.prebuilt import load_prebuilt_game_json, make_demo_decks
from engine.game_runner import run_game

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))


print("\n" + "═"*60)
print("  DM ENGINE — PREBUILT DECK TESTS")
print("═"*60)

deck_p0, deck_p1 = make_demo_decks()

check("P0 prebuilt deck is legal", deck_p0.is_valid())
check("P1 prebuilt deck is legal", deck_p1.is_valid())
check("P0 deck has resolved definitions", len(deck_p0.card_definitions) == 10)
check("P1 deck has resolved definitions", len(deck_p1.card_definitions) == 10)

state = initialize_game(deck_p0, deck_p1, first_player=0, seed=7)
bot0 = RandomBot(seed=1)
bot1 = RandomBot(seed=2)


def policy(current_state, actions):
    bot = bot0 if current_state.active_player == 0 else bot1
    return bot.rng.choice(actions)


after = run_game(state, policy, max_steps=24)
check("RandomBot simulation advances", after.turn_number > 1 or after.current_phase.value > state.current_phase.value)


class FakeDb:
    def __init__(self, cards):
        self._by_id = {card.id: card for card in cards}
        self._by_slug = {card.slug: card for card in cards}

    def require(self, card_id):
        return self._by_id[card_id]

    def get_by_slug(self, slug):
        return self._by_slug.get(slug)

    def all_cards(self):
        return list(self._by_id.values())

    def build_deck(self, name, owner, cards):
        counts = {}
        definitions = {}
        name_index = {card.name.lower(): card for card in self._by_id.values()}
        for key, count in cards.items():
            card = self._by_slug.get(key) or name_index.get(key.lower())
            if card is None and str(key).isdigit():
                card = self._by_id.get(int(key))
            if card is None:
                raise ValueError(f"Card not found: {key}")
            counts[card.id] = count
            definitions[card.id] = card
        return DeckDefinition(name=name, owner=owner, card_counts=counts, card_definitions=definitions)


all_demo_cards = list(deck_p0.card_definitions.values()) + list(deck_p1.card_definitions.values())
psychic = all_demo_cards[0]
gr_card = all_demo_cards[1]
start_card = all_demo_cards[2]
fake_db = FakeDb(all_demo_cards)

game_json = {
    "players": [
        {
            "name": "json_p0",
            "owner": "Player 0",
            "main": {card.slug: 4 for card in all_demo_cards[:10]},
            "hyperspatial": {psychic.slug: 1},
            "ultra_gr": {gr_card.slug: 2, all_demo_cards[3].slug: 2, all_demo_cards[4].slug: 2,
                         all_demo_cards[5].slug: 2, all_demo_cards[6].slug: 2, all_demo_cards[7].slug: 2},
            "start_battle_zone": [start_card.slug],
        },
        {
            "name": "json_p1",
            "owner": "Player 1",
            "main": {card.slug: 4 for card in all_demo_cards[10:20]},
        },
    ],
}

with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fp:
    json.dump(game_json, fp)
    json_path = fp.name

try:
    json_state = load_prebuilt_game_json(json_path, fake_db, first_player=0, seed=11)
finally:
    os.remove(json_path)

check("JSON prebuilt initializes main deck", json_state.players[0].deck_composition == deck_p0.card_counts)
check("JSON prebuilt loads hyperspatial zone", len(json_state.players[0].hyperspatial_zone) == 1)
check("JSON prebuilt loads Ultra GR zone", len(json_state.players[0].ultra_gr_zone) == 12)
check("JSON prebuilt loads starting battle zone", len(json_state.players[0].battle_zone) == 1)

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
