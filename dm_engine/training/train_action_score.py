"""Train ActionScoreNet from recorded self-play JSONL rows."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from bot.neural_model import MODEL_INPUT_SIZE, ActionScoreNet, save_model

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
            feature_row = row.get("features")
            if not isinstance(feature_row, list) or len(feature_row) != MODEL_INPUT_SIZE:
                raise ValueError(
                    f"Invalid feature vector at {path}:{line_number}; "
                    f"expected {MODEL_INPUT_SIZE} values"
                )
            features.append([float(value) for value in feature_row])
            targets.append(float(row.get("value_target", 0.0)))

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
    hidden_size: int = 128,
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
    model = ActionScoreNet(hidden_size=hidden_size)
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
