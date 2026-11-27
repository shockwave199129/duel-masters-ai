"""
search/node.py - search tree: one node per position where a player has a real choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.actions import Action
from core.state import GameState


@dataclass
class Edge:
    """One move from a node. Results are scored for the player choosing it."""

    action: Action
    index: int                      # position in the node's legal move list
    prior: float
    visits: int = 0
    value_sum: float = 0.0
    child: "Node | None" = None

    def mean_value(self, unvisited: float = 0.0) -> float:
        return self.value_sum / self.visits if self.visits else unvisited


@dataclass
class Node:
    """A position. A finished game has no player and no edges."""

    state: GameState
    player: int | None              # who chooses here; None once the game is over
    value: float = 0.0              # first estimate, for ``player``
    winner: int | None = None       # set for a finished game; None also means no winner
    edges: list[Edge] = field(default_factory=list)   # highest prior first
    visits: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.player is None

    def reward_for(self, player: int, leaf: "Node") -> float:
        """What reaching ``leaf`` is worth to ``player``, from -1 to 1."""
        if leaf.is_terminal:
            if leaf.winner is None:
                return 0.0
            return 1.0 if leaf.winner == player else -1.0
        return leaf.value if leaf.player == player else -leaf.value
