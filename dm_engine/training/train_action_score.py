"""Train ActionScoreNet from recorded self-play JSONL rows."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from bot.action_encoder import ACTION_VECTOR_SIZE_V2
from bot.neural_model import (
    DEFAULT_DROPOUT,
    DEFAULT_HIDDEN_SIZE,
    DEFAULT_NUM_BLOCKS,
    ActionScoreNet,
    save_model,
)
from bot.state_encoder import OBSERVATION_VECTOR_SIZE_V2

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrainSummary:
    rows: int
    epochs: int
    final_loss: float
    output_path: Path


def _load_jsonl_dataset(path: str | Path) -> TensorDataset:
    features: list[list[float]] = []
    targets: list[float] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("schema_version") != 2:
                raise ValueError(
                    f"Unsupported training row at {path}:{line_number}; "
                    "v2 trainer requires schema_version=2"
                )
            state_features = row.get("state_features")
            legal_action_features = row.get("legal_action_features")
            chosen_index = int(row.get("chosen_index", -1))
            if not isinstance(state_features, list) or len(state_features) != OBSERVATION_VECTOR_SIZE_V2:
                raise ValueError(
                    f"Invalid state_features at {path}:{line_number}; "
                    f"expected {OBSERVATION_VECTOR_SIZE_V2} values"
                )
            if not isinstance(legal_action_features, list) or not legal_action_features:
                raise ValueError(f"Missing legal_action_features at {path}:{line_number}")

            chosen_target = float(row.get("blended_target", row.get("value_target", 0.0)))
            heuristic_target = float(row.get("heuristic_target", 0.0))
            non_chosen_target = max(-1.0, min(1.0, heuristic_target - 0.10))

            for index, action_features in enumerate(legal_action_features):
                if not isinstance(action_features, list) or len(action_features) != ACTION_VECTOR_SIZE_V2:
                    raise ValueError(
                        f"Invalid action vector at {path}:{line_number}; "
                        f"expected {ACTION_VECTOR_SIZE_V2} values"
                    )
                features.append([float(value) for value in state_features + action_features])
                targets.append(chosen_target if index == chosen_index else non_chosen_target)

    if not features:
        raise ValueError(f"No training rows found in {path}")

    feature_tensor = torch.tensor(features, dtype=torch.float32)
    target_tensor = torch.tensor(targets, dtype=torch.float32)
    return TensorDataset(feature_tensor, target_tensor)


def train_action_score_model(
    *,
    input_path: str | Path,
    output_path: str | Path,
    epochs: int = 10,
    batch_size: int = 64,
    lr: float = 1e-3,
    hidden_size: int = DEFAULT_HIDDEN_SIZE,
    num_blocks: int = DEFAULT_NUM_BLOCKS,
    dropout: float = DEFAULT_DROPOUT,
    seed: int = 1,
) -> TrainSummary:
    """Train and save an ActionScoreNet checkpoint."""
    if epochs < 1:
        raise ValueError("epochs must be at least 1")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    torch.manual_seed(seed)
    dataset = _load_jsonl_dataset(input_path)
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
    model = ActionScoreNet(
        hidden_size=hidden_size,
        num_blocks=num_blocks,
        dropout=dropout,
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()
    final_loss = 0.0

    model.train()
    for epoch in range(epochs):
        running_loss = 0.0
        batches = 0
        for batch_features, batch_targets in loader:
            predictions = model(batch_features).squeeze(-1)
            loss = loss_fn(predictions, batch_targets)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            running_loss += float(loss.item())
            batches += 1
        final_loss = running_loss / max(batches, 1)
        logger.info("epoch=%s/%s loss=%.6f", epoch + 1, epochs, final_loss)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    save_model(model, output)
    return TrainSummary(
        rows=len(dataset),
        epochs=epochs,
        final_loss=final_loss,
        output_path=output,
    )
