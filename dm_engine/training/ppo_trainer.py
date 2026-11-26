"""Offline PPO trainer using recorded self-play JSONL rows."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from bot.neural_model import (
    ActionCriticNet,
    DEFAULT_DROPOUT,
    DEFAULT_HIDDEN_SIZE,
    DEFAULT_NUM_BLOCKS,
    load_actor_critic_init,
    save_model,
)
from training.device import autocast, get_device, optimizer_step
from training.replay_buffer import ReplayBuffer, Transition
from training.train_action_score import score_legal_actions
from training.rewards import trajectory_rewards

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PPOTrainSummary:
    transitions: int
    epochs: int
    final_loss: float
    policy_loss: float
    value_loss: float
    entropy: float
    output_path: Path


@dataclass(frozen=True)
class PPOTransitionBatch:
    state_features: list[float]
    legal_action_features: list[list[float]]
    chosen_index: int
    reward: float
    done: bool
    player: int
    value_target: float
    behavior_log_prob: float | None


def _read_rows(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_replay_buffer_from_jsonl(path: str | Path, *, capacity: int = 100_000) -> ReplayBuffer:
    buffer = ReplayBuffer(capacity=capacity)
    for episode in _build_episodes_from_jsonl(path):
        for item in episode:
            buffer.add(
                Transition(
                    state_features=item.state_features,
                    legal_action_features=item.legal_action_features,
                    chosen_index=item.chosen_index,
                    reward=item.reward,
                    done=item.done,
                    player=item.player,
                    value_target=item.value_target,
                    behavior_log_prob=item.behavior_log_prob,
                )
            )
    return buffer


def _build_episodes_from_jsonl(path: str | Path) -> list[list[PPOTransitionBatch]]:
    """
    Build one episode per player per finished game.

    The two players' rows are never mixed: each is scored from its own point of
    view, and only a player's last decision ends the episode. Unfinished games
    have no outcome to learn from, so they are left out.
    """
    # Seed is part of the key so reused game ids with different seeds stay apart
    trajectories: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    unfinished: set[tuple[str, str]] = set()
    for row in _read_rows(path):
        game = (str(row.get("game_id", "unknown")), str(row.get("seed", "")))
        if not row.get("terminal", False):
            unfinished.add(game)
            continue
        trajectories.setdefault((*game, int(row.get("player", 0))), []).append(row)
    if unfinished:
        logger.warning("Left %s unfinished game(s) out of PPO training", len(unfinished))

    episodes: list[list[PPOTransitionBatch]] = []
    for (_, _, player), rows in trajectories.items():
        rows.sort(key=lambda row: int(row.get("step", 0)))
        rewards = trajectory_rewards(rows)
        episodes.append(
            [
                PPOTransitionBatch(
                    state_features=[float(v) for v in row.get("state_features", [])],
                    legal_action_features=[
                        [float(v) for v in action]
                        for action in row.get("legal_action_features", [])
                    ],
                    chosen_index=int(row.get("chosen_index", -1)),
                    reward=reward,
                    done=index == len(rows) - 1,
                    player=player,
                    value_target=float(row.get("value_target", 0.0)),
                    behavior_log_prob=(
                        float(row["behavior_log_prob"])
                        if row.get("behavior_log_prob") is not None
                        else None
                    ),
                )
                for index, (row, reward) in enumerate(zip(rows, rewards))
            ]
        )
    return episodes


def _policy_loss(scores: torch.Tensor, chosen_index: torch.Tensor) -> torch.Tensor:
    return nn.CrossEntropyLoss()(scores, chosen_index)


def _value_loss(values: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
    return nn.MSELoss()(values, targets)


def _pad_actions(
    batch_actions: list[torch.Tensor],
) -> tuple[torch.Tensor, torch.Tensor]:
    max_actions = max(action.shape[0] for action in batch_actions)
    action_dim = batch_actions[0].shape[1]
    padded = torch.zeros(len(batch_actions), max_actions, action_dim, dtype=torch.float32)
    mask = torch.zeros(len(batch_actions), max_actions, dtype=torch.bool)
    for index, actions in enumerate(batch_actions):
        count = actions.shape[0]
        padded[index, :count] = actions
        mask[index, :count] = True
    return padded, mask


def _normalize_advantages(advantages: torch.Tensor) -> torch.Tensor:
    if advantages.numel() < 2:
        return advantages
    return (advantages - advantages.mean()) / (advantages.std(unbiased=False) + 1e-8)


def _compute_gae(
    rewards: torch.Tensor,
    values: torch.Tensor,
    dones: torch.Tensor,
    *,
    gamma: float,
    lam: float,
) -> tuple[torch.Tensor, torch.Tensor]:
    advantages = torch.zeros_like(rewards)
    gae = 0.0
    for index in range(len(rewards) - 1, -1, -1):
        next_value = values[index + 1] if index + 1 < len(values) and not dones[index] else torch.tensor(0.0, dtype=values.dtype)
        non_terminal = 1.0 - float(dones[index].item())
        delta = rewards[index] + gamma * float(next_value.item()) * non_terminal - float(values[index].item())
        gae = delta + gamma * lam * non_terminal * gae
        advantages[index] = gae
    returns = advantages + values
    return advantages, returns


def _compute_episode_gae(
    episodes: list[list[PPOTransitionBatch]],
    values: torch.Tensor,
    *,
    gamma: float,
    lam: float,
) -> tuple[torch.Tensor, torch.Tensor]:
    advantages: list[torch.Tensor] = []
    returns: list[torch.Tensor] = []
    offset = 0
    for episode in episodes:
        length = len(episode)
        episode_values = values[offset:offset + length]
        episode_rewards = torch.tensor([transition.reward for transition in episode], dtype=values.dtype)
        episode_dones = torch.tensor([transition.done for transition in episode], dtype=torch.bool)
        episode_advantages, episode_returns = _compute_gae(
            episode_rewards,
            episode_values,
            episode_dones,
            gamma=gamma,
            lam=lam,
        )
        advantages.append(episode_advantages)
        returns.append(episode_returns)
        offset += length
    return torch.cat(advantages, dim=0), torch.cat(returns, dim=0)


def train_ppo(
    *,
    input_path: str | Path,
    output_path: str | Path,
    epochs: int = 4,
    batch_size: int = 32,
    lr: float = 3e-4,
    hidden_size: int = DEFAULT_HIDDEN_SIZE,
    num_blocks: int = DEFAULT_NUM_BLOCKS,
    dropout: float = DEFAULT_DROPOUT,
    gamma: float = 0.99,
    clip_ratio: float = 0.2,
    value_coef: float = 0.5,
    entropy_coef: float = 0.01,
    seed: int = 1,
    init_checkpoint: str | Path | None = None,
    device: str = "auto",
) -> PPOTrainSummary:
    torch.manual_seed(seed)
    train_device = get_device(device)
    episodes = _build_episodes_from_jsonl(input_path)
    transitions = [transition for episode in episodes for transition in episode]
    if not transitions:
        raise ValueError(f"No transitions from finished games found in {input_path}")

    states = torch.tensor([t.state_features for t in transitions], dtype=torch.float32)
    rewards = torch.tensor([t.reward for t in transitions], dtype=torch.float32)
    dones = torch.tensor([t.done for t in transitions], dtype=torch.bool)

    max_action_dim = max(len(action) for t in transitions for action in t.legal_action_features)
    input_size = int(states.shape[-1] + max_action_dim)
    if init_checkpoint is None:
        model = ActionCriticNet(
            input_size=input_size,
            hidden_size=hidden_size,
            num_blocks=num_blocks,
            dropout=dropout,
        )
    else:
        # The checkpoint decides the architecture; size arguments are ignored
        model = load_actor_critic_init(init_checkpoint)
        if model.input_size != input_size:
            raise ValueError(
                f"{init_checkpoint} expects input size {model.input_size}, but the data has {input_size}"
            )
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    loader = DataLoader(list(range(len(transitions))), batch_size=batch_size, shuffle=True)
    model.to(train_device)

    def policy_log_probs(indices: list[int]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Masked (log_probs, probs, chosen indices) for these transitions."""
        actions, mask = _pad_actions(
            [torch.tensor(transitions[i].legal_action_features, dtype=torch.float32) for i in indices]
        )
        actions = actions.to(train_device)
        mask = mask.to(train_device)
        scores = score_legal_actions(
            model.forward_policy, states[indices].to(train_device), actions, mask, train_device
        )
        chosen = torch.tensor([transitions[i].chosen_index for i in indices], device=train_device)
        return torch.log_softmax(scores, dim=-1), torch.softmax(scores, dim=-1), chosen

    # Fixed targets are computed once, without dropout, before any update
    model.eval()
    with torch.no_grad():
        with autocast(train_device):
            values = torch.cat(
                [model.forward_value(chunk.to(train_device)) for chunk in states.split(batch_size)]
            )
        values = values.float().squeeze(-1).cpu()
        advantages, returns = _compute_episode_gae(episodes, values, gamma=gamma, lam=0.95)
        old_log_probs = torch.tensor(
            [t.behavior_log_prob or 0.0 for t in transitions], dtype=torch.float32
        )
        unrecorded = [i for i, t in enumerate(transitions) if t.behavior_log_prob is None]
        for start in range(0, len(unrecorded), batch_size):
            indices = unrecorded[start : start + batch_size]
            log_probs, _, chosen = policy_log_probs(indices)
            old_log_probs[indices] = log_probs.gather(1, chosen.unsqueeze(1)).squeeze(1).cpu()

    final_loss = policy_loss = value_loss = entropy = 0.0
    model.train()
    for epoch in range(epochs):
        started = time.perf_counter()
        running_loss = running_policy = running_value = running_entropy = 0.0
        batches = 0
        for batch_indices in loader:
            batch_indices = batch_indices.long()
            batch_states = states[batch_indices].to(train_device)
            batch_advantages = _normalize_advantages(advantages[batch_indices]).to(train_device)
            batch_returns = returns[batch_indices].to(train_device)
            log_probs, probs, batch_chosen = policy_log_probs(batch_indices.tolist())
            chosen_log_probs = log_probs.gather(1, batch_chosen.unsqueeze(1)).squeeze(1)
            ratio = torch.exp(chosen_log_probs - old_log_probs[batch_indices].to(train_device))
            clipped = torch.clamp(ratio, 1.0 - clip_ratio, 1.0 + clip_ratio)
            policy_loss_value = -torch.min(ratio * batch_advantages, clipped * batch_advantages).mean()
            entropy_value = -(probs * log_probs).sum(dim=-1).mean()
            with autocast(train_device):
                values_pred = model.forward_value(batch_states)
            values_pred = values_pred.float().squeeze(-1)
            value_loss_value = _value_loss(values_pred, batch_returns)
            loss = policy_loss_value + value_coef * value_loss_value - entropy_coef * entropy_value

            optimizer_step(model, optimizer, loss)

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
    return PPOTrainSummary(
        transitions=len(transitions),
        epochs=epochs,
        final_loss=final_loss,
        policy_loss=policy_loss,
        value_loss=value_loss,
        entropy=entropy,
        output_path=output,
    )
