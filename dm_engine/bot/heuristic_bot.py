"""Heuristic legal-action bot for evaluation."""

from __future__ import annotations

import random

from core.actions import Action
from core.state import GameState
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from training.rewards import heuristic_state_value


class HeuristicBot:
    """Pick the action that most improves the heuristic state value."""

    def __init__(self, *, seed: int | None = None, max_candidates: int = 30):
        self.rng = random.Random(seed)
        self.max_candidates = max_candidates

    def generate_candidate_actions(self, state: GameState, db=None) -> list[Action]:
        actions = get_legal_actions(state, db)
        if len(actions) <= self.max_candidates:
            return actions
        return self.rng.sample(actions, self.max_candidates)

    def choose_from_actions(self, state: GameState, actions: list[Action], db=None) -> Action:
        if not actions:
            raise ValueError("No legal actions available")
        perspective = actions[0].player
        baseline = heuristic_state_value(state, perspective)
        best_score = None
        best_actions: list[Action] = []
        for action in actions:
            next_state = execute_action(state, action, db=db, validate=False)
            score = heuristic_state_value(next_state, perspective) - baseline
            if best_score is None or score > best_score:
                best_score = score
                best_actions = [action]
            elif score == best_score:
                best_actions.append(action)
        return self.rng.choice(best_actions)

    def choose_action(self, state: GameState, db=None) -> Action:
        actions = self.generate_candidate_actions(state, db=db)
        return self.choose_from_actions(state, actions, db=db)
