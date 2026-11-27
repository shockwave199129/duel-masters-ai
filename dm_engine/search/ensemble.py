"""
search/ensemble.py - search several possible worlds and add up the results.

One tree per sampled world (Cowling, Ward and Powley 2012). The move visited most
over all the trees is chosen, so no single guess about the hidden cards decides.
"""

from __future__ import annotations

import random

from core.actions import Action
from core.state import GameState
from search.determinize import sample_world
from search.mcts import MCTS, SearchResult


def ensemble_search(
    search: MCTS,
    state: GameState,
    actions: list[Action],
    *,
    trees: int,
    seed: str,
) -> SearchResult:
    """
    Run ``trees`` searches from the point of view of the player choosing among
    ``actions``. Each tree gets ``search.config.simulations`` simulations.
    """
    if trees < 1:
        raise ValueError("trees must be at least 1")
    if not actions:
        raise ValueError("No legal actions available")
    actions = list(actions)
    if len(actions) == 1:
        return SearchResult(actions, [0], [0.0], 0, 0.0)

    observer = actions[0].player
    rng = random.Random(seed)
    visits = [0] * len(actions)
    totals = [0.0] * len(actions)
    for tree in range(trees):
        world = sample_world(state, observer, rng)
        result = search.search(world, actions, salt=f"{seed}-tree{tree}")
        for index, (count, value) in enumerate(zip(result.visits, result.values)):
            visits[index] += count
            totals[index] += value * count

    values = [total / count if count else 0.0 for total, count in zip(totals, visits)]
    best = max(range(len(actions)), key=lambda i: (visits[i], values[i]))
    played = sum(visits)
    root_value = sum(totals) / played if played else 0.0
    return SearchResult(actions, visits, values, best, root_value)
