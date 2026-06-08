"""
tests/test_training_deck_sampling.py - training deck sampling and bias checks.
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bot.action_encoder import encode_action_v2
from core.actions import Action
from core.enums import ActionType
from decks.prebuilt import PrebuiltDeckSpec, make_demo_decks
from training.self_play import _balanced_bit, _load_self_play_state

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))


print("\n" + "=" * 60)
print("  DM ENGINE - TRAINING DECK SAMPLING TESTS")
print("=" * 60)


class FakeDb:
    def __init__(self):
        deck0, deck1 = make_demo_decks()
        self.specs = [
            (101, PrebuiltDeckSpec(main_deck=deck0)),
            (202, PrebuiltDeckSpec(main_deck=deck1)),
        ]
        cards = list(deck0.card_definitions.values()) + list(deck1.card_definitions.values())
        self._by_id = {card.id: card for card in cards}

    def sample_training_decks(self, rng, *, count=2, source=None, allow_mirror=False):
        assert count == 2
        assert source in (None, "demo")
        assert not allow_mirror
        return list(self.specs)

    def require(self, card_id):
        return self._by_id[card_id]


db = FakeDb()

balanced_values = [_balanced_bit(index, 1) for index in range(10)]
check("Balanced bit alternates exactly", balanced_values.count(0) == balanced_values.count(1))

state, deck_slots, deck_ids, deck_names, first_player = _load_self_play_state(
    deck_json=None,
    db=db,
    first_player=0,
    seed=7,
    game_id="bias-test-a",
    seat_flip=False,
    rng=random.Random(7),
    use_database_decks=True,
    deck_source="demo",
    allow_mirror_matches=False,
)
check("DB deck mode assigns first sampled deck to P0 without flip", deck_ids == (101, 202))
check("DB deck mode initializes P0 deck composition", state.players[0].deck_composition == db.specs[0][1].main_deck.card_counts)
check("Explicit first player is honored", first_player == 0)

_, flipped_slots, flipped_ids, _, _ = _load_self_play_state(
    deck_json=None,
    db=db,
    first_player=1,
    seed=8,
    game_id="bias-test-b",
    seat_flip=True,
    rng=random.Random(8),
    use_database_decks=True,
    deck_source="demo",
    allow_mirror_matches=False,
)
check("DB deck mode flips sampled decks onto opposite seats", flipped_ids == (202, 101))
check("Flipped source slots are recorded", flipped_slots == (1, 0))

p0_action = Action(player=0, action_type=ActionType.PASS)
p1_action = Action(player=1, action_type=ActionType.PASS)
check("Action encoding is seat-neutral", encode_action_v2(p0_action) == encode_action_v2(p1_action))

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
