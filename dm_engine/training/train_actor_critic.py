"""Actor-critic trainer for recorded self-play JSONL rows."""

from __future__ import annotations

import logging
import time
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
from training.compact_data import DecisionBatches, load_compact
from training.device import autocast, get_device, optimizer_step, warmup
from training.train_action_score import (
    _load_grouped_dataset, _normalize_state_action_row, collate_decisions, score_legal_actions,
)

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
    device: str = "auto",
    data_on_gpu: bool = False,
    warmup_steps: int = 0,
) -> ActorCriticSummary:
    if policy_loss_mode not in {"pairwise", "mse"}:
        raise ValueError("policy_loss_mode must be 'pairwise' or 'mse'")
    if value_target_field not in {"value_target", "heuristic_target", "blended_target"}:
        raise ValueError("Unsupported value_target_field")

    torch.manual_seed(seed)
    train_device = get_device(device)
    if data_on_gpu:
        dataset = load_compact(
            input_path, device=train_device, balance_players=balance_players, seed=seed,
            normalize=_normalize_state_action_row)
        logger.info("Training data on %s: %s decisions, %.2f GB",
                    train_device.type, len(dataset), dataset.bytes_used() / 1e9)
        any_won = bool((dataset.value > 0).any())
        loader = DecisionBatches(dataset, batch_size, seed=seed)
    else:
        dataset = _load_grouped_dataset(input_path, balance_players=balance_players, seed=seed)
        any_won = any(float(item[5]) > 0 for item in dataset.items)
        loader = DataLoader(
            dataset,
            batch_size=batch_size,
            shuffle=True,
            collate_fn=collate_decisions,
            pin_memory=train_device.type == "cuda",
        )
    if not any_won:
        logger.warning("No decisions from a won game in %s; the policy has nothing to reinforce", input_path)
    input_size = dataset.state_size + dataset.action_size
    model = ActionCriticNet(
        input_size=input_size,
        hidden_size=hidden_size,
        num_blocks=num_blocks,
        dropout=dropout,
    )
    model.to(train_device)
    if policy_loss_mode == "pairwise":
        # A trainable temperature can satisfy ranking margins by rescaling scores
        model.temperature.requires_grad_(False)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    mse_loss_fn = nn.MSELoss()

    final_loss = policy_loss = value_loss = entropy = 0.0
    model.train()
    step = 0
    for epoch in range(epochs):
        started = time.perf_counter()
        running_loss = running_policy = running_value = running_entropy = 0.0
        batches = 0
        for batch in loader:
            batch = [tensor.to(train_device, non_blocking=True) for tensor in batch]
            batch_state, batch_actions, batch_policy, batch_blended, batch_heuristic, batch_value, action_mask = batch
            batch_targets = {
                "blended_target": batch_blended,
                "heuristic_target": batch_heuristic,
                "value_target": batch_value,
            }[value_target_field]
            scores = score_legal_actions(model.forward_policy, batch_state, batch_actions, action_mask, train_device)
            with autocast(train_device):
                values = model.forward_value(batch_state)
            log_probs = torch.log_softmax(scores, dim=-1)
            values = values.float().squeeze(-1)
            if policy_loss_mode == "pairwise":
                chosen = batch_policy.argmax(dim=-1, keepdim=True)
                chosen_scores = scores.gather(1, chosen)
                margin_loss = torch.relu(1.0 - (chosen_scores - scores))
                margin_loss = margin_loss.masked_fill(batch_policy.bool() | ~action_mask, 0.0)
                decision_loss = margin_loss.sum(dim=-1)
            else:
                decision_loss = -(batch_policy * log_probs.masked_fill(~action_mask, 0.0)).sum(dim=-1)
            # Reinforce a move by how much the game went better than predicted.
            # Clipped at zero: a negative weight would make these losses unbounded.
            advantage_weight = (batch_value - values.detach()).clamp(min=0.0)
            policy_loss_value = (advantage_weight * decision_loss).mean()
            value_loss_value = mse_loss_fn(values, batch_targets)
            entropy_value = -(torch.softmax(scores, dim=-1) * log_probs).sum(dim=-1).mean()
            loss = policy_loss_value + value_coef * value_loss_value - entropy_coef * entropy_value

            warmup(optimizer, lr, step, warmup_steps)
            optimizer_step(model, optimizer, loss)
            step += 1

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
            "epoch=%s/%s loss=%.6f policy=%.6f value=%.6f entropy=%.6f device=%s batch_size=%s seconds=%.1f",
            epoch + 1,
            epochs,
            final_loss,
            policy_loss,
            value_loss,
            entropy,
            train_device.type,
            batch_size,
            time.perf_counter() - started,
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
