"""Train ActionScoreNet from recorded self-play JSONL rows."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from bot.action_encoder import ACTION_VECTOR_SIZE_V2, ACTION_VECTOR_SIZE_V3
from bot.neural_model import (
    DEFAULT_DROPOUT,
    DEFAULT_HIDDEN_SIZE,
    DEFAULT_NUM_BLOCKS,
    ActionScoreNet,
    save_model,
)
from bot.state_encoder import OBSERVATION_VECTOR_SIZE_V2, OBSERVATION_VECTOR_SIZE_V3

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrainSummary:
    rows: int
    epochs: int
    final_loss: float
    output_path: Path
    loss_mode: str = "mse"


def _expected_sizes(schema_version: int) -> tuple[int, int]:
    if schema_version == 2:
        return OBSERVATION_VECTOR_SIZE_V2, ACTION_VECTOR_SIZE_V2
    if schema_version == 3:
        return OBSERVATION_VECTOR_SIZE_V3, ACTION_VECTOR_SIZE_V3
    raise ValueError(f"Unsupported training schema_version={schema_version}")


def _load_jsonl_dataset(path: str | Path) -> TensorDataset:
    features: list[list[float]] = []
    targets: list[float] = []
    dataset_schema_version: int | None = None
    with Path(path).open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            schema_version = int(row.get("schema_version", 2))
            if dataset_schema_version is None:
                dataset_schema_version = schema_version
            elif schema_version != dataset_schema_version:
                raise ValueError("Mixed schema versions are not supported in one training file")
            observation_size, action_size = _expected_sizes(schema_version)
            state_features = row.get("state_features")
            legal_action_features = row.get("legal_action_features")
            chosen_index = int(row.get("chosen_index", -1))
            if not isinstance(state_features, list) or len(state_features) != observation_size:
                raise ValueError(
                    f"Invalid state_features at {path}:{line_number}; "
                    f"expected {observation_size} values"
                )
            if not isinstance(legal_action_features, list) or not legal_action_features:
                raise ValueError(f"Missing legal_action_features at {path}:{line_number}")

            chosen_target = float(row.get("blended_target", row.get("value_target", 0.0)))
            heuristic_target = float(row.get("heuristic_target", 0.0))
            non_chosen_target = max(-1.0, min(1.0, heuristic_target - 0.10))

            for index, action_features in enumerate(legal_action_features):
                if not isinstance(action_features, list) or len(action_features) != action_size:
                    raise ValueError(
                        f"Invalid action vector at {path}:{line_number}; "
                        f"expected {action_size} values"
                    )
                features.append([float(value) for value in state_features + action_features])
                targets.append(chosen_target if index == chosen_index else non_chosen_target)

    if not features:
        raise ValueError(f"No training rows found in {path}")

    feature_tensor = torch.tensor(features, dtype=torch.float32)
    target_tensor = torch.tensor(targets, dtype=torch.float32)
    return TensorDataset(feature_tensor, target_tensor)


def _load_pairwise_dataset(path: str | Path) -> TensorDataset:
    chosen_features: list[list[float]] = []
    other_features: list[list[float]] = []
    labels: list[float] = []
    dataset_schema_version: int | None = None
    with Path(path).open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            schema_version = int(row.get("schema_version", 2))
            if dataset_schema_version is None:
                dataset_schema_version = schema_version
            elif schema_version != dataset_schema_version:
                raise ValueError("Mixed schema versions are not supported in one training file")
            observation_size, action_size = _expected_sizes(schema_version)
            state_features = row.get("state_features")
            legal_action_features = row.get("legal_action_features")
            chosen_index = int(row.get("chosen_index", -1))
            if not isinstance(state_features, list) or len(state_features) != observation_size:
                raise ValueError(
                    f"Invalid state_features at {path}:{line_number}; expected {observation_size} values"
                )
            if not isinstance(legal_action_features, list) or not legal_action_features:
                raise ValueError(f"Missing legal_action_features at {path}:{line_number}")
            if not 0 <= chosen_index < len(legal_action_features):
                raise ValueError(f"Invalid chosen_index at {path}:{line_number}")
            chosen_action = legal_action_features[chosen_index]
            if not isinstance(chosen_action, list) or len(chosen_action) != action_size:
                raise ValueError(f"Invalid chosen action vector at {path}:{line_number}")
            chosen_row = [float(value) for value in state_features + chosen_action]
            for index, action_features in enumerate(legal_action_features):
                if index == chosen_index:
                    continue
                if not isinstance(action_features, list) or len(action_features) != action_size:
                    raise ValueError(
                        f"Invalid action vector at {path}:{line_number}; expected {action_size} values"
                    )
                chosen_features.append(chosen_row)
                other_features.append([float(value) for value in state_features + action_features])
                labels.append(1.0)
    if not chosen_features:
        raise ValueError(f"No ranking pairs found in {path}")
    return TensorDataset(
        torch.tensor(chosen_features, dtype=torch.float32),
        torch.tensor(other_features, dtype=torch.float32),
        torch.tensor(labels, dtype=torch.float32),
    )


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
    loss_mode: str = "mse",
    ranking_margin: float = 0.10,
) -> TrainSummary:
    """Train and save an ActionScoreNet checkpoint."""
    if epochs < 1:
        raise ValueError("epochs must be at least 1")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    if loss_mode not in {"mse", "pairwise"}:
        raise ValueError("loss_mode must be 'mse' or 'pairwise'")

    torch.manual_seed(seed)
    dataset = (
        _load_pairwise_dataset(input_path)
        if loss_mode == "pairwise"
        else _load_jsonl_dataset(input_path)
    )
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
    input_size = int(dataset.tensors[0].shape[1])
    model = ActionScoreNet(
        input_size=input_size,
        hidden_size=hidden_size,
        num_blocks=num_blocks,
        dropout=dropout,
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    mse_loss_fn = nn.MSELoss()
    ranking_loss_fn = nn.MarginRankingLoss(margin=ranking_margin)
    final_loss = 0.0

    model.train()
    for epoch in range(epochs):
        running_loss = 0.0
        batches = 0
        for batch in loader:
            if loss_mode == "pairwise":
                batch_chosen, batch_other, batch_labels = batch
                chosen_scores = model(batch_chosen).squeeze(-1)
                other_scores = model(batch_other).squeeze(-1)
                loss = ranking_loss_fn(chosen_scores, other_scores, batch_labels)
            else:
                batch_features, batch_targets = batch
                predictions = model(batch_features).squeeze(-1)
                loss = mse_loss_fn(predictions, batch_targets)

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
        loss_mode=loss_mode,
    )
