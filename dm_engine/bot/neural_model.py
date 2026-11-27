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
DEFAULT_VALUE_BLOCKS = 2
_MODALITY_SIZES = {
    MODEL_INPUT_SIZE_V2: (OBSERVATION_VECTOR_SIZE_V2, ACTION_VECTOR_SIZE_V2),
    MODEL_INPUT_SIZE_V3: (OBSERVATION_VECTOR_SIZE_V3, ACTION_VECTOR_SIZE_V3),
}


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

        if input_size not in _MODALITY_SIZES:
            raise ValueError(
                f"input_size={input_size} matches no encoder version; expected one of {sorted(_MODALITY_SIZES)}"
            )

        self.input_size = input_size
        self.hidden_size = hidden_size
        self.num_blocks = num_blocks
        self.dropout = dropout
        # Sizes of the raw state and action vectors for this encoder version
        self.obs_size, self.act_size = _MODALITY_SIZES[input_size]
        # Modality‑specific projections to the hidden dimension
        self.state_proj = ModalityProjection(self.obs_size, hidden_size, dropout)
        self.action_proj = ModalityProjection(self.act_size, hidden_size, dropout)
        # After concatenation we have hidden_size*2 dimensions
        self.input_norm = nn.LayerNorm(hidden_size * 2)
        self.input_projection = nn.Sequential(
            nn.Linear(hidden_size * 2, hidden_size),
            nn.GELU(),
        )
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
        x = self.input_projection(x)
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
        value_blocks: int = DEFAULT_VALUE_BLOCKS,
    ):
        super().__init__(
            input_size=input_size,
            hidden_size=hidden_size,
            num_blocks=num_blocks,
            dropout=dropout,
        )
        self.has_value_head = True
        # 0 reproduces checkpoints saved before the value head had its own trunk
        self.value_blocks = nn.Sequential(
            *[ResidualMLPBlock(hidden_size, dropout=dropout) for _ in range(value_blocks)]
        )
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

    def forward_policy_and_value(
        self, state_features: torch.Tensor, action_features: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Move scores and the value of one position, for search. ``state_features``
        has one row and ``action_features`` one row per move. The position is
        projected once and used by both heads.
        """
        position = self.state_proj(state_features)
        x = torch.cat(
            [position.expand(action_features.shape[0], -1), self.action_proj(action_features)],
            dim=-1)
        x = self.blocks(self.input_projection(self.input_norm(x)))
        scores = self.head(x) / self.temperature.clamp(min=0.1, max=10.0)
        value = torch.tanh(self.value_head(self.value_proj(self.value_blocks(position))))
        return scores.view(-1), value.view(-1)

    def forward_value(self, state_features: torch.Tensor) -> torch.Tensor:
        x = self.state_proj(state_features)
        x = self.value_blocks(x)
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
        "state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
        "schema_version": 3,
    }
    if isinstance(model, ActionScoreNet):
        encoder_version = 3 if model.input_size == MODEL_INPUT_SIZE_V3 else 2
        metadata.update(
            {
                "input_size": model.input_size,
                "obs_size": model.obs_size,
                "act_size": model.act_size,
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
            metadata["value_blocks"] = len(model.value_blocks)
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
    device: str | torch.device | None = None,
) -> nn.Module:
    """Load a saved action-scoring model."""
    resolved_device = torch.device(device or "cpu")
    if resolved_device.type == "cuda" and not torch.cuda.is_available():
        resolved_device = torch.device("cpu")
    checkpoint = torch.load(Path(path), map_location=resolved_device)
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
                value_blocks=int(checkpoint.get("value_blocks", 0)),
            )
            model.load_state_dict(state_dict)
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
    model.to(resolved_device)
    model.eval()
    return model


def load_actor_critic_init(path: str | Path) -> ActionCriticNet:
    """
    Load a checkpoint as the starting point for actor-critic training.

    An actor-critic checkpoint is loaded as is. An action-score checkpoint
    supplies every shared layer, and only the value head starts fresh.
    """
    base = load_model(path)
    if isinstance(base, ActionCriticNet):
        return base
    if not isinstance(base, ActionScoreNet):
        raise ValueError(f"{path} is a legacy checkpoint and cannot initialize an actor-critic model")
    model = ActionCriticNet(
        input_size=base.input_size,
        hidden_size=base.hidden_size,
        num_blocks=base.num_blocks,
        dropout=base.dropout,
    )
    missing, unexpected = model.load_state_dict(base.state_dict(), strict=False)
    shared_missing = [key for key in missing if not key.startswith(("value_blocks.", "value_proj.", "value_head."))]
    if shared_missing or unexpected:
        raise ValueError(f"{path} does not match: missing={shared_missing} unexpected={list(unexpected)}")
    return model
