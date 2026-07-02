"""
bot/neural_model.py — action scoring networks for legal-action selection.
"""

from __future__ import annotations

from pathlib import Path

import torch
import torch.nn as nn

from bot.action_encoder import ACTION_VECTOR_SIZE_V2, ACTION_VECTOR_SIZE_V3
from bot.state_encoder import OBSERVATION_VECTOR_SIZE_V2, OBSERVATION_VECTOR_SIZE_V3


MODEL_INPUT_SIZE_V2 = OBSERVATION_VECTOR_SIZE_V2 + ACTION_VECTOR_SIZE_V2
MODEL_INPUT_SIZE_V3 = OBSERVATION_VECTOR_SIZE_V3 + ACTION_VECTOR_SIZE_V3
MODEL_INPUT_SIZE = MODEL_INPUT_SIZE_V3
DEFAULT_HIDDEN_SIZE = 384
DEFAULT_NUM_BLOCKS = 6
DEFAULT_DROPOUT = 0.10
MODEL_ARCHITECTURE = "ActionScoreNetV3"


class ModalityProjection(nn.Module):
    """Linear projection + activation for a single modality (state or action)."""

    def __init__(self, in_dim: int, out_dim: int, dropout: float = DEFAULT_DROPOUT):
        super().__init__()
        self.proj = nn.Sequential(
            nn.LayerNorm(in_dim),
            nn.Linear(in_dim, out_dim),
            nn.GELU(),
            nn.Dropout(dropout),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.proj(x)
class ResidualMLPBlock(nn.Module):
    """Pre-norm residual block for stable deeper action scoring."""

    def __init__(self, hidden_size: int, dropout: float = DEFAULT_DROPOUT):
        super().__init__()
        self.norm = nn.LayerNorm(hidden_size)
        self.fc1 = nn.Linear(hidden_size, hidden_size * 4)
        self.act = nn.GELU()
        self.fc2 = nn.Linear(hidden_size * 4, hidden_size)
        self.drop = nn.Dropout(dropout)

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        y = self.norm(features)
        y = self.fc1(y)
        y = self.act(y)
        y = self.drop(y)
        y = self.fc2(y)
        y = self.drop(y)
        return features + y


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
        # Sizes of the raw modality vectors (V3 schema)
        self.obs_size = OBSERVATION_VECTOR_SIZE_V3
        self.act_size = ACTION_VECTOR_SIZE_V3
        # Modality‑specific projections to the hidden dimension
        self.state_proj = ModalityProjection(self.obs_size, hidden_size, dropout)
        self.action_proj = ModalityProjection(self.act_size, hidden_size, dropout)
        # After concatenation we have hidden_size*2 dimensions
        self.input_norm = nn.LayerNorm(hidden_size * 2)
        self.input_projection = nn.Identity()
        self.blocks = nn.Sequential(
            *[ResidualMLPBlock(hidden_size, dropout=dropout) for _ in range(num_blocks)]
        )
        self.head = nn.Sequential(
            nn.LayerNorm(hidden_size),
            nn.Linear(hidden_size, hidden_size // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size // 2, 1),
        )
        self.temperature = nn.Parameter(torch.tensor(1.0))

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        # Split concatenated raw features into state and action parts
        state = features[..., :self.obs_size]
        action = features[..., self.obs_size:self.obs_size + self.act_size]
        # Project each modality to the hidden dimension
        state_proj = self.state_proj(state)
        action_proj = self.action_proj(action)
        # Concatenate projected vectors
        x = torch.cat([state_proj, action_proj], dim=-1)
        # Normalize and feed through residual blocks
        x = self.input_norm(x)
        x = self.input_projection(x)  # identity
        x = self.blocks(x)
        logits = self.head(x)
        # Scale logits with learnable temperature (clamp to avoid extreme values)
        return logits / self.temperature.clamp(min=0.1, max=10.0)


class ActionCriticNet(ActionScoreNet):
    """Dual-head action scorer with an additional state-value head."""

    def __init__(
        self,
        input_size: int = MODEL_INPUT_SIZE,
        hidden_size: int = DEFAULT_HIDDEN_SIZE,
        num_blocks: int = DEFAULT_NUM_BLOCKS,
        dropout: float = DEFAULT_DROPOUT,
    ):
        super().__init__(
            input_size=input_size,
            hidden_size=hidden_size,
            num_blocks=num_blocks,
            dropout=dropout,
        )
        self.has_value_head = True
        self.value_proj = nn.Sequential(
            nn.LayerNorm(hidden_size),
            nn.Linear(hidden_size, hidden_size // 2),
            nn.GELU(),
            nn.Dropout(dropout),
        )
        self.value_head = nn.Sequential(
            nn.Linear(hidden_size // 2, 1),
        )

    def _encode(self, features: torch.Tensor) -> torch.Tensor:
        state = features[..., :self.obs_size]
        action = features[..., self.obs_size:self.obs_size + self.act_size]
        state_proj = self.state_proj(state)
        action_proj = self.action_proj(action)
        x = torch.cat([state_proj, action_proj], dim=-1)
        x = self.input_norm(x)
        x = self.input_projection(x)
        return self.blocks(x)

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        x = self._encode(features)
        logits = self.head(x)
        return logits / self.temperature.clamp(min=0.1, max=10.0)

    def forward_policy(self, features: torch.Tensor) -> torch.Tensor:
        return self.forward(features)

    def forward_value(self, state_features: torch.Tensor) -> torch.Tensor:
        x = self.state_proj(state_features)
        x = self.value_proj(x)
        value = self.value_head(x)
        return torch.tanh(value)


class LegacyActionScoreNet(nn.Module):
    """Generation-0 MLP kept so older checkpoints remain loadable."""

    def __init__(
        self,
        input_size: int = MODEL_INPUT_SIZE_V2,
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
        encoder_version = 3 if model.input_size == MODEL_INPUT_SIZE_V3 else 2
        metadata.update(
            {
                "input_size": model.input_size,
                "hidden_size": model.hidden_size,
                "num_blocks": model.num_blocks,
                "dropout": model.dropout,
                "model": MODEL_ARCHITECTURE,
                "encoder_version": encoder_version,
            }
        )
        if isinstance(model, ActionCriticNet):
            metadata["model"] = "ActionCriticNetV1"
            metadata["has_value_head"] = True
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
        elif checkpoint.get("has_value_head"):
            model = ActionCriticNet(
                input_size=int(checkpoint.get("input_size", MODEL_INPUT_SIZE)),
                hidden_size=int(checkpoint.get("hidden_size", hidden_size)),
                num_blocks=int(checkpoint.get("num_blocks", num_blocks)),
                dropout=float(checkpoint.get("dropout", dropout)),
            )
            model.load_state_dict(state_dict, strict=False)
        else:
            input_size = int(checkpoint.get("input_size", MODEL_INPUT_SIZE))
            model = ActionScoreNet(
                input_size=input_size,
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
