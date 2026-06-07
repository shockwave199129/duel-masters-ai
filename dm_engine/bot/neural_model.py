"""
bot/neural_model.py — generation-0 action scoring network.
"""

from __future__ import annotations

from pathlib import Path

import torch
import torch.nn as nn

from bot.action_encoder import ACTION_VECTOR_SIZE
from bot.state_encoder import OBSERVATION_VECTOR_SIZE


MODEL_INPUT_SIZE = OBSERVATION_VECTOR_SIZE + ACTION_VECTOR_SIZE


class ActionScoreNet(nn.Module):
    """Five-hidden-layer network that scores one legal action."""

    def __init__(
        self,
        input_size: int = MODEL_INPUT_SIZE,
        hidden_size: int = 128,
    ):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, 1),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.net(features)


def save_model(model: ActionScoreNet, path: str | Path) -> None:
    """Save weights for later generations."""
    torch.save(model.state_dict(), Path(path))


def load_model(path: str | Path, *, hidden_size: int = 128) -> ActionScoreNet:
    """Load a saved action-scoring model."""
    model = ActionScoreNet(hidden_size=hidden_size)
    model.load_state_dict(torch.load(Path(path), map_location="cpu"))
    model.eval()
    return model
