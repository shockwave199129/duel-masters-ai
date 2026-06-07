"""
bot/random_bot.py — baseline legal random player.
"""

from __future__ import annotations

import random

from core.actions import Action
from core.state import GameState
from engine.action_generator import get_legal_actions


class RandomBot:
    """Chooses uniformly from currently legal actions."""

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    def choose_action(self, state: GameState, db=None) -> Action:
        actions = get_legal_actions(state, db)
        if not actions:
            raise ValueError("No legal actions available")
        return self.rng.choice(actions)
