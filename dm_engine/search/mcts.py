"""
search/mcts.py - PUCT search guided by a move scorer and a position scorer.

There are no random play-outs: a new position is scored by the evaluator, or by
the result if the game is over. Positions with a single legal move are played
straight through, without a node and without calling the evaluator.

The search plays out the state it is given, including cards the player cannot
see. Give it a sampled world, not the real game, for fair play.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from core.actions import Action
from core.state import GameState
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from search.node import Edge, Node

# (state, legal moves, db) -> (probability of each move, value for the player moving)
Evaluator = Callable[[GameState, list, object], tuple[list[float], float]]

# A longer run of single moves than this means the game is not advancing
MAX_FORCED_MOVES = 500


@dataclass
class SearchConfig:
    simulations: int = 512
    c_puct: float = 1.5
    # Only the best-scored moves are tried at first, and more as a position is
    # visited. This matters when paying mana gives hundreds of combinations.
    widen_start: int = 4
    widen_rate: float = 1.0


@dataclass
class SearchResult:
    actions: list[Action]
    visits: list[int]               # one count per action, in the same order
    values: list[float]             # mean result per action; 0 if never tried
    best_index: int
    root_value: float               # for the player to move


class MCTS:
    def __init__(self, evaluator: Evaluator, config: SearchConfig | None = None, db=None):
        self.evaluator = evaluator
        self.config = config or SearchConfig()
        self.db = db

    def search(
        self, state: GameState, actions: list[Action] | None = None, *, salt: str = "search",
    ) -> SearchResult:
        """Search from ``state``, which is left unchanged."""
        root_state = state.copy()
        # Keeps imagined random effects away from the real game's chance
        root_state.rng_salt = salt
        actions = list(actions) if actions is not None else get_legal_actions(root_state, self.db)
        if not actions:
            raise ValueError("No legal actions available")
        if len(actions) == 1:
            return SearchResult(actions, [0], [0.0], 0, 0.0)

        root = self._node(root_state, actions)
        for _ in range(self.config.simulations):
            self._simulate(root)
        return self._result(root, actions)

    # ── one simulation ───────────────────────────────────────────────────────

    def _simulate(self, root: Node) -> None:
        node, path = root, []
        while not node.is_terminal:
            edge = self._select(node)
            path.append((node, edge))
            if edge.child is None:
                edge.child = self._expand(node.state, edge.action)
                node = edge.child
                break
            node = edge.child

        leaf = node
        leaf.visits += 1
        for parent, edge in path:
            parent.visits += 1
            edge.visits += 1
            edge.value_sum += parent.reward_for(parent.player, leaf)

    def _select(self, node: Node) -> Edge:
        config = self.config
        width = config.widen_start + int(config.widen_rate * math.sqrt(node.visits))
        scale = config.c_puct * math.sqrt(node.visits + 1)
        # An untried move is assumed to be about as good as the position itself
        return max(
            node.edges[:width],
            key=lambda e: e.mean_value(node.value) + scale * e.prior / (1 + e.visits),
        )

    def _expand(self, state: GameState, action: Action) -> Node:
        """Play ``action``, then any forced moves, up to the next real choice."""
        state = execute_action(state, action, db=self.db, validate=False)
        for _ in range(MAX_FORCED_MOVES):
            if state.is_terminal():
                return Node(state, player=None, winner=state.winner())
            actions = get_legal_actions(state, self.db)
            if len(actions) > 1:
                return self._node(state, actions)
            state = execute_action(state, actions[0], db=self.db, validate=False)
        raise RuntimeError(f"No choice or result after {MAX_FORCED_MOVES} forced moves")

    def _node(self, state: GameState, actions: list[Action]) -> Node:
        priors, value = self.evaluator(state, actions, self.db)
        if len(priors) != len(actions):
            raise ValueError("The evaluator must give one prior per action")
        edges = [Edge(action, index, float(prior))
                 for index, (action, prior) in enumerate(zip(actions, priors))]
        edges.sort(key=lambda edge: -edge.prior)
        return Node(state, player=actions[0].player, value=float(value), edges=edges)

    # ── result ───────────────────────────────────────────────────────────────

    def _result(self, root: Node, actions: list[Action]) -> SearchResult:
        visits = [0] * len(actions)
        values = [0.0] * len(actions)
        for edge in root.edges:
            visits[edge.index] = edge.visits
            values[edge.index] = edge.mean_value()
        # Most visited; ties go to the better result, then the higher prior
        best = max(root.edges, key=lambda e: (e.visits, e.mean_value(), e.prior))
        total = sum(visits)
        root_value = (
            sum(edge.value_sum for edge in root.edges) / total if total else root.value)
        return SearchResult(actions, visits, values, best.index, root_value)
