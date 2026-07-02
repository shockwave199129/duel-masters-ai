"""Actor-critic trainer for recorded self-play JSONL rows."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from bot.neural_model import (
    ActionCriticNet,
    DEFAULT_DROPOUT,
    DEFAULT_HIDDEN_SIZE,
    DEFAULT_NUM_BLOCKS,
    save_model,
)
from training.train_action_score import _load_grouped_dataset

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActorCriticSummary:
    rows: int
    epochs: int
    final_loss: float
    output_path: Path
    policy_loss: float
    value_loss: float
    entropy: float


def train_actor_critic_model(
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
    policy_loss_mode: str = "pairwise",
    value_target_field: str = "blended_target",
    value_coef: float = 1.0,
    entropy_coef: float = 0.0,
    balance_players: bool = True,
) -> ActorCriticSummary:
    if policy_loss_mode not in {"pairwise", "mse"}:
        raise ValueError("policy_loss_mode must be 'pairwise' or 'mse'")
    if value_target_field not in {"value_target", "heuristic_target", "blended_target"}:
        raise ValueError("Unsupported value_target_field")

    torch.manual_seed(seed)
    dataset = _load_grouped_dataset(input_path, balance_players=balance_players, seed=seed)
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
    input_size = int(dataset.tensors[1].shape[-1] + dataset.tensors[0].shape[-1])
    model = ActionCriticNet(
        input_size=input_size,
        hidden_size=hidden_size,
        num_blocks=num_blocks,
        dropout=dropout,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    mse_loss_fn = nn.MSELoss()

    final_loss = policy_loss = value_loss = entropy = 0.0
    model.train()
    for epoch in range(epochs):
        running_loss = running_policy = running_value = running_entropy = 0.0
        batches = 0
        for batch_state, batch_actions, batch_policy, batch_blended, batch_heuristic, batch_value in loader:
            batch_targets = {
                "blended_target": batch_blended,
                "heuristic_target": batch_heuristic,
                "value_target": batch_value,
            }[value_target_field]
            batch_size_, action_count, _ = batch_actions.shape
            state_expanded = batch_state.unsqueeze(1).expand(-1, action_count, -1)
            merged = torch.cat([state_expanded, batch_actions], dim=-1).reshape(batch_size_ * action_count, -1)
            scores = model.forward_policy(merged).reshape(batch_size_, action_count)
            if policy_loss_mode == "pairwise":
                chosen = batch_policy.argmax(dim=-1, keepdim=True)
                chosen_scores = scores.gather(1, chosen)
                margin_loss = torch.relu(1.0 - (chosen_scores - scores)).masked_fill(batch_policy.bool(), 0.0)
                policy_loss_value = margin_loss.sum(dim=-1).mean()
            else:
                policy_loss_value = -(batch_policy.clamp(min=1e-8) * torch.log_softmax(scores, dim=-1)).sum(dim=-1).mean()
            values = model.forward_value(batch_state).squeeze(-1)
            value_loss_value = mse_loss_fn(values, batch_targets)
            entropy_value = -(torch.softmax(scores, dim=-1) * torch.log_softmax(scores, dim=-1)).sum(dim=-1).mean()
            loss = policy_loss_value + value_coef * value_loss_value - entropy_coef * entropy_value

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            running_loss += float(loss.item())
            running_policy += float(policy_loss_value.item())
            running_value += float(value_loss_value.item())
            running_entropy += float(entropy_value.item())
            batches += 1

        final_loss = running_loss / max(batches, 1)
        policy_loss = running_policy / max(batches, 1)
        value_loss = running_value / max(batches, 1)
        entropy = running_entropy / max(batches, 1)
        logger.info(
            "epoch=%s/%s loss=%.6f policy=%.6f value=%.6f entropy=%.6f",
            epoch + 1,
            epochs,
            final_loss,
            policy_loss,
            value_loss,
            entropy,
        )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    save_model(model, output)
    return ActorCriticSummary(
        rows=len(dataset),
        epochs=epochs,
        final_loss=final_loss,
        output_path=output,
        policy_loss=policy_loss,
        value_loss=value_loss,
        entropy=entropy,
    )
