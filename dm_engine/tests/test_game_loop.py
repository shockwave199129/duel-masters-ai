"""
tests/test_game_loop.py — game runner and AI-facing observation smoke tests.
"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.actions import Action
from core.cards import CardDefinition, DeckDefinition
from core.enums import ActionType, CardSubtype, CardType, Civilization
from core.initializer import initialize_game
from bot.state_encoder import encode_observation
from engine.game_runner import run_game

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))


def card(cid):
    return CardDefinition(
        id=cid, slug=f"c{cid}", name=f"c{cid}", cost=1, power=1000,
        card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]), races=frozenset(),
        keywords=frozenset(), effects=tuple(),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


def deck():
    cards = [card(i) for i in range(1, 11)]
    return DeckDefinition(
        name="deck",
        owner="p",
        card_counts={c.id: 4 for c in cards},
        card_definitions={c.id: c for c in cards},
    )


def pass_first_policy(state, actions: list[Action]) -> Action:
    for action in actions:
        if action.action_type == ActionType.PASS:
            return action
    return actions[0]


print("\n" + "═"*60)
print("  DM ENGINE — GAME LOOP TESTS")
print("═"*60)

state = initialize_game(deck(), deck(), first_player=0, seed=3)
after = run_game(state, pass_first_policy, max_steps=12)
check("Game runner advances state", after.turn_number > 1 or after.current_phase.value > state.current_phase.value)

encoded = encode_observation(after, 0)
check("Observation encoder returns floats", encoded and all(isinstance(v, float) for v in encoded))

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
