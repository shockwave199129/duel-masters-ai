"""
bot/neural_bot.py — generation-0 neural legal-action player.
"""

from __future__ import annotations

import random
from pathlib import Path

import torch

from bot.action_encoder import encode_action
from bot.neural_model import ActionScoreNet, load_model
from bot.state_encoder import encode_observation
from core.actions import Action
from core.state import GameState
from engine.action_generator import get_legal_actions


class NeuralBot:
    """Scores legal actions with a randomly initialized or loaded model."""

    def __init__(
        self,
        *,
        model: ActionScoreNet | None = None,
        model_path: str | Path | None = None,
        epsilon: float = 0.05,
        seed: int | None = None,
    ):
        if model is not None and model_path is not None:
            raise ValueError("Pass either model or model_path, not both")
        self.model = model if model is not None else (
            load_model(model_path) if model_path is not None else ActionScoreNet()
        )
        self.model.eval()
        self.epsilon = epsilon
        self.rng = random.Random(seed)

    def choose_action(self, state: GameState, db=None) -> Action:
        actions = get_legal_actions(state, db)
        return self.choose_from_actions(state, actions)

    def choose_from_actions(self, state: GameState, actions: list[Action]) -> Action:
        if not actions:
            raise ValueError("No legal actions available")
        if self.epsilon > 0.0 and self.rng.random() < self.epsilon:
            return self.rng.choice(actions)

        perspective = actions[0].player
        state_features = encode_observation(state, perspective)
        rows = [
            state_features + encode_action(action)
            for action in actions
        ]
        with torch.no_grad():
            inputs = torch.tensor(rows, dtype=torch.float32)
            scores = self.model(inputs).squeeze(-1)
            best_index = int(torch.argmax(scores).item())
        return actions[best_index]
