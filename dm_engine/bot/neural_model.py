"""
bot/neural_model.py — action scoring networks for legal-action selection.
"""

from __future__ import annotations

from pathlib import Path

import torch
import torch.nn as nn

from bot.action_encoder import ACTION_VECTOR_SIZE_V2
from bot.state_encoder import OBSERVATION_VECTOR_SIZE_V2


MODEL_INPUT_SIZE = OBSERVATION_VECTOR_SIZE_V2 + ACTION_VECTOR_SIZE_V2
DEFAULT_HIDDEN_SIZE = 256
DEFAULT_NUM_BLOCKS = 4
DEFAULT_DROPOUT = 0.10
MODEL_ARCHITECTURE = "ActionScoreNetV2"


class ResidualMLPBlock(nn.Module):
    """Pre-norm residual block for stable deeper action scoring."""

    def __init__(self, hidden_size: int, dropout: float = DEFAULT_DROPOUT):
        super().__init__()
        self.block = nn.Sequential(
            nn.LayerNorm(hidden_size),
            nn.Linear(hidden_size, hidden_size * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size * 2, hidden_size),
            nn.Dropout(dropout),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return features + self.block(features)


class ActionScoreNet(nn.Module):
    """Residual network that scores one legal action from state/action features."""

    def __init__(
        self,
        input_size: int = MODEL_INPUT_SIZE,
        hidden_size: int = DEFAULT_HIDDEN_SIZE,
        num_blocks: int = DEFAULT_NUM_BLOCKS,
        dropout: float = DEFAULT_DROPOUT,
    ):
        super().__init__()
        if hidden_size < 1:
            raise ValueError("hidden_size must be at least 1")
        if num_blocks < 1:
            raise ValueError("num_blocks must be at least 1")
        if not 0.0 <= dropout < 1.0:
            raise ValueError("dropout must be in the range [0.0, 1.0)")

        self.input_size = input_size
        self.hidden_size = hidden_size
        self.num_blocks = num_blocks
        self.dropout = dropout
        self.input_norm = nn.LayerNorm(input_size)
        self.input_projection = nn.Sequential(
            nn.Linear(input_size, hidden_size),
            nn.GELU(),
            nn.Dropout(dropout),
        )
        self.blocks = nn.Sequential(
            *[ResidualMLPBlock(hidden_size, dropout=dropout) for _ in range(num_blocks)]
        )
        self.head = nn.Sequential(
            nn.LayerNorm(hidden_size),
            nn.Linear(hidden_size, hidden_size // 2 or 1),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size // 2 or 1, 1),
            nn.Tanh(),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        x = self.input_norm(features)
        x = self.input_projection(x)
        x = self.blocks(x)
        return self.head(x)


class LegacyActionScoreNet(nn.Module):
    """Generation-0 MLP kept so older checkpoints remain loadable."""

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


def save_model(model: nn.Module, path: str | Path) -> None:
    """Save weights for later generations."""
    metadata = {
        "state_dict": model.state_dict(),
        "schema_version": 2,
    }
    if isinstance(model, ActionScoreNet):
        metadata.update(
            {
                "input_size": model.input_size,
                "hidden_size": model.hidden_size,
                "num_blocks": model.num_blocks,
                "dropout": model.dropout,
                "model": MODEL_ARCHITECTURE,
            }
        )
    elif isinstance(model, LegacyActionScoreNet):
        metadata.update(
            {
                "input_size": model.net[0].in_features,
                "hidden_size": model.net[0].out_features,
                "model": "ActionScoreNet",
            }
        )
    else:
        raise TypeError(f"Unsupported model type: {type(model).__name__}")
    torch.save(
        metadata,
        Path(path),
    )


def _is_legacy_state_dict(state_dict: dict[str, torch.Tensor]) -> bool:
    return any(key.startswith("net.") for key in state_dict)


def load_model(
    path: str | Path,
    *,
    hidden_size: int = DEFAULT_HIDDEN_SIZE,
    num_blocks: int = DEFAULT_NUM_BLOCKS,
    dropout: float = DEFAULT_DROPOUT,
) -> nn.Module:
    """Load a saved action-scoring model."""
    checkpoint = torch.load(Path(path), map_location="cpu")
    if isinstance(checkpoint, dict) and "state_dict" in checkpoint:
        state_dict = checkpoint["state_dict"]
        if checkpoint.get("model") == "ActionScoreNet" or _is_legacy_state_dict(state_dict):
            model = LegacyActionScoreNet(
                input_size=int(checkpoint.get("input_size", MODEL_INPUT_SIZE)),
                hidden_size=int(checkpoint.get("hidden_size", 128)),
            )
            model.load_state_dict(state_dict)
        else:
            model = ActionScoreNet(
                input_size=int(checkpoint.get("input_size", MODEL_INPUT_SIZE)),
                hidden_size=int(checkpoint.get("hidden_size", hidden_size)),
                num_blocks=int(checkpoint.get("num_blocks", num_blocks)),
                dropout=float(checkpoint.get("dropout", dropout)),
            )
            model.load_state_dict(state_dict)
    else:
        model = LegacyActionScoreNet(hidden_size=128)
        model.load_state_dict(checkpoint)
    model.eval()
    return model
