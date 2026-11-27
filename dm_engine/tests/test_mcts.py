"""
tests/test_mcts.py - tree search on small positions.

The evaluator here knows nothing (equal priors, every position worth 0), so a
right answer can only come from the search reaching real wins and losses.
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.cards import CardDefinition
from core.enums import ActionType, CardSubtype, CardType, Civilization, GameResult, Keyword, Phase
from core.player_state import PlayerState
from core.state import GameState, TurnInfo
from core.zones import Creature, ShieldCard
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from search.mcts import MCTS, SearchConfig
from search.node import Edge, Node

results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" - {detail}" if detail else ""))


def card(cid, name, power=1000, keywords=()):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=1, power=power,
        card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]), races=frozenset(),
        keywords=frozenset(keywords), effects=tuple(),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


def creature(definition, controller):
    return Creature(definition, controller=controller, owner=controller,
                    has_summoning_sickness=False)


def attack_state(shields=0, blocker=False):
    """Player 0 may attack with one creature."""
    deck = [card(99, "deck") for _ in range(10)]
    state = GameState(
        players=(PlayerState(player_index=0, player_name="P0", deck=list(deck)),
                 PlayerState(player_index=1, player_name="P1", deck=list(deck))),
        turn_info=TurnInfo(turn_number=3, active_player=0, phase=Phase.ATTACK),
        game_id="mcts-test",
    )
    state.players[0].battle_zone = [creature(card(1, "attacker", 3000), 0)]
    state.players[1].shield_zone = [ShieldCard(card(2, f"shield{i}")) for i in range(shields)]
    if blocker:
        state.players[1].battle_zone = [
            creature(card(3, "blocker", 9000, [Keyword.BLOCKER]), 1)]
    return state


class UniformEvaluator:
    def __init__(self):
        self.calls = []

    def __call__(self, state, actions, db=None):
        self.calls.append(len(actions))
        return [1.0 / len(actions)] * len(actions), 0.0


def run(state, simulations=64, actions=None):
    evaluator = UniformEvaluator()
    search = MCTS(evaluator, SearchConfig(simulations=simulations))
    return search.search(state, actions), evaluator


print("DM ENGINE - TREE SEARCH TESTS")

# A win in one: the opponent has no shields
state = attack_state(shields=0)
actions = get_legal_actions(state)
result, evaluator = run(state)
chosen = result.actions[result.best_index]
check("The position has a real choice", len(actions) > 1, str(actions))
check("Search takes a win in one",
      chosen.action_type == ActionType.ATTACK_PLAYER, str(chosen))
check("The winning move is valued as a win", result.values[result.best_index] == 1.0)
check("Every simulation is counted at the root", sum(result.visits) == 64)

# The defender moves: only blocking avoids losing at once
state = attack_state(shields=0, blocker=True)
attack = next(a for a in get_legal_actions(state)
              if a.action_type == ActionType.ATTACK_PLAYER)
state = execute_action(state, attack)
actions = get_legal_actions(state)
while len(actions) == 1:  # steps before the defender gets to block
    state = execute_action(state, actions[0])
    actions = get_legal_actions(state)
result, evaluator = run(state, actions=actions)
chosen = result.actions[result.best_index]
check("The defender is the one choosing", actions[0].player == 1 and len(actions) > 1,
      str(actions))
check("Search blocks rather than lose at once",
      chosen.action_type == ActionType.DECLARE_BLOCKER, str(chosen))
passing = next(i for i, a in enumerate(actions)
               if a.action_type != ActionType.DECLARE_BLOCKER)
check("Not blocking is valued as a loss for the defender", result.values[passing] == -1.0,
      str(result.values))

# Forced moves are free
state = attack_state()
only = get_legal_actions(state)[:1]
result, evaluator = run(state, actions=only)
check("A single legal move is returned without searching",
      result.best_index == 0 and not evaluator.calls)
result, evaluator = run(attack_state(shields=2), simulations=100)
check("The evaluator is never asked about a forced move",
      evaluator.calls and min(evaluator.calls) > 1, str(sorted(set(evaluator.calls))))

# The real game is left alone, and a search can be repeated
state = attack_state(shields=2)
random.seed(7)
expected_random = random.random()
random.seed(7)
first, _ = run(state, simulations=100)
check("Search leaves the shared random generator alone", random.random() == expected_random)
check("Search leaves the given state unchanged",
      state.result == GameResult.IN_PROGRESS and state.rng_salt == ""
      and len(state.players[1].shield_zone) == 2
      and state.current_phase == Phase.ATTACK
      and not state.players[0].battle_zone[0].is_tapped)
second, _ = run(state, simulations=100)
check("The same search gives the same visit counts", first.visits == second.visits,
      f"{first.visits} {second.visits}")

# Rewards follow whoever is choosing, not just alternate by depth
mine = Node(state, player=0)
theirs_leaf = Node(state, player=1, value=0.25)
my_leaf = Node(state, player=0, value=0.25)
check("A leaf value flips for the other player", mine.reward_for(0, theirs_leaf) == -0.25)
check("A leaf value is kept for the same player", mine.reward_for(0, my_leaf) == 0.25)

# Hundreds of moves: only the best few are tried at first
moves = get_legal_actions(attack_state())
wide = Node(state, player=0, edges=[Edge(moves[0], i, 1 / 600) for i in range(600)])
search = MCTS(UniformEvaluator(), SearchConfig(widen_start=4, widen_rate=1.0))
for _ in range(100):
    edge = search._select(wide)
    edge.visits += 1
    wide.visits += 1
tried = sum(1 for e in wide.edges if e.visits)
check("Widening limits how many moves are tried", 4 < tried <= 14, str(tried))

passed = sum(results)
print(f"RESULTS: {passed}/{len(results)} passed, {len(results) - passed} failed")
sys.exit(0 if passed == len(results) else 1)
