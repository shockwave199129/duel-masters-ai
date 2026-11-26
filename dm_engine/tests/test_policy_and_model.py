"""
tests/test_policy_and_model.py - move sampling, value head, model sizes and deck odds.
"""

import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch

from bot.neural_bot import NeuralBot
from bot.neural_model import (
    MODEL_INPUT_SIZE_V2, MODEL_INPUT_SIZE_V3, ActionCriticNet, ActionScoreNet,
    load_model, save_model,
)
from bot.state_encoder import OBSERVATION_VECTOR_SIZE_V2, OBSERVATION_VECTOR_SIZE_V3
from core.cards import CardDefinition
from core.enums import CardSubtype, CardType, Civilization
from core.initializer import initialize_game
from core.zones import Creature, EvolutionStackEntry
from decks.prebuilt import make_demo_decks
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


SIZES = {"hidden_size": 16, "num_blocks": 1, "dropout": 0.0}
torch.manual_seed(0)
deck0, deck1 = make_demo_decks()
state = initialize_game(deck0, deck1, first_player=0, seed=3, game_id="policy")
actions = get_legal_actions(state)
for _ in range(60):  # advance until the player has a real choice
    if len(actions) >= 3:
        break
    state = execute_action(state, actions[0], validate=False)
    actions = get_legal_actions(state)
check("Position offers a choice", len(actions) > 1, str(len(actions)))

# M1: a v2 model plays with v2 slices
for version, size in ((2, MODEL_INPUT_SIZE_V2), (3, MODEL_INPUT_SIZE_V3)):
    bot = NeuralBot(model=ActionScoreNet(input_size=size, **SIZES), seed=1)
    check(f"v{version} model chooses a move", bot.choose_from_actions(state, actions) in actions)
try:
    ActionScoreNet(input_size=123, **SIZES)
    rejected = False
except ValueError:
    rejected = True
check("Unknown input size is rejected", rejected)

# M2 and M3
model = ActionScoreNet(**SIZES)
greedy = NeuralBot(model=model, seed=1)
chosen = {repr(greedy.choose_from_actions(state, actions)) for _ in range(40)}
check("Evaluation is greedy by default", len(chosen) == 1 and greedy.last_behavior_log_prob == 0.0)

sampler = NeuralBot(model=model, seed=1, sample=True)
picked = set()
exact = True
for _ in range(200):
    sampler.choose_from_actions(state, actions)
    picked.add(sampler.last_selected_index)
    expected = math.log(sampler.last_policy_probs[sampler.last_selected_index])
    exact &= abs(sampler.last_behavior_log_prob - expected) < 1e-5
check("Sampling explores several moves", len(picked) > 1, str(sorted(picked)))
check("Recorded probability is the sampling probability", exact)

for sample in (False, True):
    bot = NeuralBot(model=model, seed=2, sample=sample, epsilon=0.4)
    consistent = True
    for _ in range(100):
        bot.choose_from_actions(state, actions)
        i, n = bot.last_selected_index, len(actions)
        best = max(range(n), key=lambda j: bot.last_policy_probs[j])
        base = bot.last_policy_probs[i] if sample else float(i == best)
        consistent &= abs(math.exp(bot.last_behavior_log_prob) - (0.6 * base + 0.4 / n)) < 1e-5
    check(f"Exploration probability is exact (sample={sample})", consistent)

# M4 and checkpoint compatibility
critic = ActionCriticNet(**SIZES)
check("Value head has its own blocks", len(critic.value_blocks) == 2)
check("v2 value head accepts v2 states",
      ActionCriticNet(input_size=MODEL_INPUT_SIZE_V2, **SIZES).forward_value(
          torch.zeros(2, OBSERVATION_VECTOR_SIZE_V2)).shape == (2, 1))
with tempfile.TemporaryDirectory() as tmp:
    for blocks in (0, 2):
        saved = ActionCriticNet(value_blocks=blocks, **SIZES).eval()
        path = Path(tmp) / f"critic{blocks}.pt"
        save_model(saved, path)
        if blocks == 0:
            checkpoint = torch.load(path)
            del checkpoint["value_blocks"]  # as written before this setting existed
            torch.save(checkpoint, path)
        states = torch.ones(1, OBSERVATION_VECTOR_SIZE_V3)
        check(f"Critic checkpoint with {blocks} value blocks reloads",
              torch.equal(load_model(path).forward_value(states), saved.forward_value(states)))


# M7: evolution under-cards are no longer counted as unseen
def definition(cid):
    return CardDefinition(
        id=cid, slug=f"c{cid}", name=f"c{cid}", cost=1, power=1000, card_type=CardType.CREATURE,
        card_subtype=CardSubtype.NONE, civilizations=frozenset([Civilization.FIRE]),
        races=frozenset(), keywords=frozenset(), effects=(), evolution_source_races=frozenset(),
        evolution_source_types=frozenset(), is_multiface=False,
    )


player = state.players[0]
player.hand, player.mana_zone, player.graveyard = [], [], []
creature = Creature(definition=definition(1), controller=0, owner=0)
creature.evolution_stack.append(EvolutionStackEntry(definition=definition(2)))
player.battle_zone = [creature]
player.deck_composition = {1: 2, 2: 2}
check("Under-cards count as seen", player.cards_remaining_in_deck_by_id() == {1: 1, 2: 1})

if FAILURES:
    print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
    sys.exit(1)
