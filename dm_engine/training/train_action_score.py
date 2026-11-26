"""Train ActionScoreNet from recorded self-play JSONL rows."""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn
from torch.nn.utils.rnn import pad_sequence
from torch.utils.data import DataLoader, Dataset, TensorDataset

from bot.action_encoder import ACTION_VECTOR_SIZE_V2, ACTION_VECTOR_SIZE_V3
from bot.neural_model import (
    DEFAULT_DROPOUT,
    DEFAULT_HIDDEN_SIZE,
    DEFAULT_NUM_BLOCKS,
    ActionScoreNet,
    save_model,
)
from bot.state_encoder import OBSERVATION_VECTOR_SIZE_V2, OBSERVATION_VECTOR_SIZE_V3
from training.device import autocast, get_device, optimizer_step

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


def _read_jsonl_rows(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _normalize_target_field(value: Any, length: int) -> list[float]:
    if isinstance(value, list):
        if len(value) != length:
            raise ValueError(f"Expected target vector of length {length}")
        return [float(item) for item in value]
    scalar = float(value)
    return [scalar for _ in range(length)]


def _normalize_state_action_row(
    row: dict[str, Any],
    *,
    path: str | Path,
    line_number: int,
) -> tuple[int, list[float], list[list[float]], int]:
    schema_version = int(row.get("schema_version", 2))
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

    normalized_actions: list[list[float]] = []
    for action_features in legal_action_features:
        if not isinstance(action_features, list) or len(action_features) != action_size:
            raise ValueError(
                f"Invalid action vector at {path}:{line_number}; expected {action_size} values"
            )
        normalized_actions.append([float(value) for value in action_features])
    return schema_version, [float(value) for value in state_features], normalized_actions, chosen_index


def _balance_decisions_by_player(
    rows: list[dict[str, Any]],
    *,
    seed: int,
) -> list[dict[str, Any]]:
    """Keep equal decision counts from player 0 and player 1 when both are present."""
    by_player: dict[int, list[dict[str, Any]]] = {0: [], 1: []}
    for row in rows:
        player = int(row.get("player", 0))
        if player in by_player:
            by_player[player].append(row)

    player0_rows = by_player[0]
    player1_rows = by_player[1]
    logger.info(
        "Training decisions loaded: player0=%s player1=%s total=%s",
        len(player0_rows),
        len(player1_rows),
        len(rows),
    )
    if not player0_rows or not player1_rows:
        return rows

    rng = random.Random(seed)
    per_player = min(len(player0_rows), len(player1_rows))
    balanced = rng.sample(player0_rows, per_player) + rng.sample(player1_rows, per_player)
    rng.shuffle(balanced)
    logger.info(
        "Balanced training decisions: player0=%s player1=%s total=%s",
        per_player,
        per_player,
        len(balanced),
    )
    return balanced


def _load_decision_rows(
    path: str | Path,
    *,
    balance_players: bool = True,
    seed: int = 1,
) -> list[dict[str, Any]]:
    rows = _read_jsonl_rows(path)
    if balance_players:
        rows = _balance_decisions_by_player(rows, seed=seed)
    return rows


def _load_jsonl_dataset(
    path: str | Path,
    *,
    balance_players: bool = True,
    seed: int = 1,
) -> TensorDataset:
    rows = _load_decision_rows(path, balance_players=balance_players, seed=seed)

    features: list[list[float]] = []
    targets: list[float] = []
    dataset_schema_version: int | None = None
    for line_number, row in enumerate(rows, start=1):
        schema_version, state_features, legal_action_features, chosen_index = _normalize_state_action_row(
            row,
            path=path,
            line_number=line_number,
        )
        if dataset_schema_version is None:
            dataset_schema_version = schema_version
        elif schema_version != dataset_schema_version:
            raise ValueError("Mixed schema versions are not supported in one training file")
        for index, action_features in enumerate(legal_action_features):
            features.append([float(value) for value in state_features + action_features])
            targets.append(1.0 if index == chosen_index else 0.0)

    if not features:
        raise ValueError(f"No training rows found in {path}")

    feature_tensor = torch.tensor(features, dtype=torch.float32)
    target_tensor = torch.tensor(targets, dtype=torch.float32)
    return TensorDataset(feature_tensor, target_tensor)


class GroupedDecisionDataset(Dataset):
    """One item per decision; each keeps its own number of legal actions."""

    def __init__(self, items: list[tuple[torch.Tensor, ...]]):
        self.items = items
        self.state_size = int(items[0][0].shape[0])
        self.action_size = int(items[0][1].shape[1])

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, ...]:
        return self.items[index]


def collate_decisions(batch: list[tuple[torch.Tensor, ...]]) -> tuple[torch.Tensor, ...]:
    """
    Pad legal actions to the largest count in this batch.

    Returns (states, actions, policy, blended, heuristic, value, action_mask).
    action_mask is True for real actions and False for padding.
    """
    states, actions, policies, blended, heuristic, value = zip(*batch)
    counts = torch.tensor([action.shape[0] for action in actions])
    padded_actions = pad_sequence(list(actions), batch_first=True)
    action_mask = torch.arange(padded_actions.shape[1]).unsqueeze(0) < counts.unsqueeze(1)
    return (
        torch.stack(states),
        padded_actions,
        pad_sequence(list(policies), batch_first=True),
        torch.stack(blended),
        torch.stack(heuristic),
        torch.stack(value),
        action_mask,
    )


def score_legal_actions(
    score_fn,
    states: torch.Tensor,
    actions: torch.Tensor,
    action_mask: torch.Tensor,
    device: torch.device,
) -> torch.Tensor:
    """
    Score only the real actions and return a [decisions, max_actions] tensor.

    Padding slots are never run through the model, so memory grows with the
    number of legal moves and not with batch size times the longest move list.
    Padding scores are -1e9. Results are float32 for the loss arithmetic.
    """
    decision_index = action_mask.nonzero(as_tuple=True)[0]
    merged = torch.cat([states[decision_index], actions[action_mask]], dim=-1)
    with autocast(device):
        flat = score_fn(merged)
    scores = torch.full(action_mask.shape, -1e9, dtype=torch.float32, device=action_mask.device)
    return scores.masked_scatter(action_mask, flat.float().reshape(-1))


def _load_grouped_dataset(
    path: str | Path,
    *,
    balance_players: bool = True,
    seed: int = 1,
) -> GroupedDecisionDataset:
    rows = _load_decision_rows(path, balance_players=balance_players, seed=seed)

    state_rows: list[list[float]] = []
    action_rows: list[list[list[float]]] = []
    policy_rows: list[list[float]] = []
    blended_rows: list[float] = []
    heuristic_rows: list[float] = []
    value_rows: list[float] = []
    dataset_schema_version: int | None = None
    for line_number, row in enumerate(rows, start=1):
        schema_version, state_features, legal_action_features, chosen_index = _normalize_state_action_row(
            row,
            path=path,
            line_number=line_number,
        )
        if dataset_schema_version is None:
            dataset_schema_version = schema_version
        elif schema_version != dataset_schema_version:
            raise ValueError("Mixed schema versions are not supported in one training file")
        state_rows.append(state_features)
        action_rows.append(legal_action_features)
        policy_value = row.get("policy_target")
        if policy_value is None:
            policy_row = [0.0] * len(legal_action_features)
            policy_row[chosen_index] = 1.0
        else:
            policy_row = _normalize_target_field(policy_value, len(legal_action_features))
        policy_rows.append(policy_row)
        blended_rows.append(float(row.get("blended_target", row.get("heuristic_target", 0.0))))
        heuristic_rows.append(float(row.get("heuristic_target", 0.0)))
        value_rows.append(float(row.get("value_target", 0.0)))

    if not state_rows:
        raise ValueError(f"No training rows found in {path}")

    def as_tensor(value) -> torch.Tensor:
        return torch.tensor(value, dtype=torch.float32)

    columns = (state_rows, action_rows, policy_rows, blended_rows, heuristic_rows, value_rows)
    return GroupedDecisionDataset(
        [tuple(as_tensor(value) for value in item) for item in zip(*columns)]
    )


def _load_blended_dataset(
    path: str | Path,
    *,
    balance_players: bool = True,
    seed: int = 1,
) -> TensorDataset:
    rows = _load_decision_rows(path, balance_players=balance_players, seed=seed)

    features: list[list[float]] = []
    targets: list[float] = []
    dataset_schema_version: int | None = None
    for line_number, row in enumerate(rows, start=1):
        schema_version, state_features, legal_action_features, chosen_index = _normalize_state_action_row(
            row,
            path=path,
            line_number=line_number,
        )
        if dataset_schema_version is None:
            dataset_schema_version = schema_version
        elif schema_version != dataset_schema_version:
            raise ValueError("Mixed schema versions are not supported in one training file")
        chosen_target = float(row.get("blended_target", row.get("heuristic_target", 0.0)))
        non_chosen_target = float(row.get("heuristic_target", 0.0))
        for index, action_features in enumerate(legal_action_features):
            features.append(state_features + action_features)
            targets.append(chosen_target if index == chosen_index else non_chosen_target)

    if not features:
        raise ValueError(f"No training rows found in {path}")

    return TensorDataset(
        torch.tensor(features, dtype=torch.float32),
        torch.tensor(targets, dtype=torch.float32),
    )


def _load_pairwise_dataset(
    path: str | Path,
    *,
    balance_players: bool = True,
    seed: int = 1,
) -> TensorDataset:
    rows = _read_jsonl_rows(path)
    if balance_players:
        rows = _balance_decisions_by_player(rows, seed=seed)

    chosen_features: list[list[float]] = []
    other_features: list[list[float]] = []
    labels: list[float] = []
    dataset_schema_version: int | None = None
    for line_number, row in enumerate(rows, start=1):
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
    balance_players: bool = True,
    device: str = "auto",
) -> TrainSummary:
    """Train and save an ActionScoreNet checkpoint."""
    if epochs < 1:
        raise ValueError("epochs must be at least 1")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    if loss_mode not in {"mse", "pairwise", "blended", "distillation"}:
        raise ValueError("loss_mode must be 'mse', 'pairwise', 'blended', or 'distillation'")

    torch.manual_seed(seed)
    train_device = get_device(device)
    pin = train_device.type == "cuda"
    loader_kwargs = {"balance_players": balance_players, "seed": seed}
    if loss_mode == "pairwise":
        dataset = _load_pairwise_dataset(input_path, **loader_kwargs)
    elif loss_mode == "distillation":
        dataset = _load_grouped_dataset(input_path, **loader_kwargs)
    elif loss_mode == "blended":
        dataset = _load_blended_dataset(input_path, **loader_kwargs)
    else:
        dataset = _load_jsonl_dataset(input_path, **loader_kwargs)
    if loss_mode == "distillation":
        loader = DataLoader(
            dataset, batch_size=batch_size, shuffle=True, collate_fn=collate_decisions, pin_memory=pin
        )
        input_size = dataset.state_size + dataset.action_size
    else:
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, pin_memory=pin)
        input_size = int(dataset.tensors[0].shape[1])
    model = ActionScoreNet(
        input_size=input_size,
        hidden_size=hidden_size,
        num_blocks=num_blocks,
        dropout=dropout,
    )
    model.to(train_device)
    # A trainable temperature can satisfy ranking margins by rescaling scores
    model.temperature.requires_grad_(False)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    bce_loss_fn = nn.BCEWithLogitsLoss()
    mse_loss_fn = nn.MSELoss()
    ranking_loss_fn = nn.MarginRankingLoss(margin=ranking_margin)
    final_loss = 0.0

    model.train()
    for epoch in range(epochs):
        started = time.perf_counter()
        running_loss = 0.0
        batches = 0
        for batch in loader:
            batch = [tensor.to(train_device, non_blocking=True) for tensor in batch]
            with autocast(train_device):
                if loss_mode == "pairwise":
                    batch_chosen, batch_other, batch_labels = batch
                    chosen_scores = model(batch_chosen).float().squeeze(-1)
                    other_scores = model(batch_other).float().squeeze(-1)
                    loss = ranking_loss_fn(chosen_scores, other_scores, batch_labels)
                elif loss_mode == "distillation":
                    batch_state, batch_actions, batch_policy, *_rest, action_mask = batch
                    scores = score_legal_actions(model, batch_state, batch_actions, action_mask, train_device)
                    log_probs = torch.log_softmax(scores, dim=-1)
                    loss = -(batch_policy * log_probs.masked_fill(~action_mask, 0.0)).sum(dim=-1).mean()
                elif loss_mode == "blended":
                    batch_features, batch_targets = batch
                    predictions = model(batch_features).float().squeeze(-1)
                    # Targets range from -1 to 1, so predictions must too
                    loss = mse_loss_fn(torch.tanh(predictions), batch_targets)
                else:
                    batch_features, batch_targets = batch
                    predictions = model(batch_features).float().squeeze(-1)
                    loss = bce_loss_fn(predictions, batch_targets)

            optimizer_step(model, optimizer, loss)

            running_loss += float(loss.item())
            batches += 1
        final_loss = running_loss / max(batches, 1)
        logger.info(
            "epoch=%s/%s loss=%.6f device=%s batch_size=%s seconds=%.1f",
            epoch + 1, epochs, final_loss, train_device.type, batch_size, time.perf_counter() - started,
        )

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
