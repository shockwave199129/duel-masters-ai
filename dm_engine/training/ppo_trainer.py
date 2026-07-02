"""Offline PPO trainer using recorded self-play JSONL rows."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from bot.neural_model import ActionCriticNet, DEFAULT_DROPOUT, DEFAULT_HIDDEN_SIZE, DEFAULT_NUM_BLOCKS, save_model
from training.replay_buffer import ReplayBuffer, Transition
from training.rewards import step_reward

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
    rows = _read_rows(path)
    buffer = ReplayBuffer(capacity=capacity)
    by_game: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_game.setdefault(str(row.get("game_id", "unknown")), []).append(row)

    for game_rows in by_game.values():
        game_rows.sort(key=lambda row: int(row.get("step", 0)))
        for index, row in enumerate(game_rows):
            next_row = game_rows[index + 1] if index + 1 < len(game_rows) else row
            reward = step_reward(row, next_row, int(row.get("player", 0)))
            done = bool(row.get("terminal", False))
            buffer.add(
                Transition(
                    state_features=[float(v) for v in row.get("state_features", [])],
                    legal_action_features=[
                        [float(v) for v in action]
                        for action in row.get("legal_action_features", [])
                    ],
                    chosen_index=int(row.get("chosen_index", -1)),
                    reward=reward,
                    done=done,
                    player=int(row.get("player", 0)),
                    value_target=float(row.get("value_target", 0.0)),
                    behavior_log_prob=(
                        float(row["behavior_log_prob"])
                        if row.get("behavior_log_prob") is not None
                        else None
                    ),
                )
            )
    return buffer


def _build_episodes_from_jsonl(path: str | Path) -> list[list[PPOTransitionBatch]]:
    rows = _read_rows(path)
    by_game: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_game.setdefault(str(row.get("game_id", "unknown")), []).append(row)

    episodes: list[list[PPOTransitionBatch]] = []
    for game_rows in by_game.values():
        game_rows.sort(key=lambda row: int(row.get("step", 0)))
        episode: list[PPOTransitionBatch] = []
        for index, row in enumerate(game_rows):
            next_row = game_rows[index + 1] if index + 1 < len(game_rows) else row
            reward = step_reward(row, next_row, int(row.get("player", 0)))
            episode.append(
                PPOTransitionBatch(
                    state_features=[float(v) for v in row.get("state_features", [])],
                    legal_action_features=[
                        [float(v) for v in action]
                        for action in row.get("legal_action_features", [])
                    ],
                    chosen_index=int(row.get("chosen_index", -1)),
                    reward=reward,
                    done=bool(row.get("terminal", False)),
                    player=int(row.get("player", 0)),
                    value_target=float(row.get("value_target", 0.0)),
                    behavior_log_prob=(
                        float(row["behavior_log_prob"])
                        if row.get("behavior_log_prob") is not None
                        else None
                    ),
                )
            )
        episodes.append(episode)
    return episodes


def _policy_loss(scores: torch.Tensor, chosen_index: torch.Tensor) -> torch.Tensor:
    return nn.CrossEntropyLoss()(scores, chosen_index)


def _value_loss(values: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
    return nn.MSELoss()(values, targets)


def _pad_actions(
    batch_actions: list[torch.Tensor],
    batch_chosen: torch.Tensor,
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
) -> PPOTrainSummary:
    torch.manual_seed(seed)
    episodes = _build_episodes_from_jsonl(input_path)
    transitions = [transition for episode in episodes for transition in episode]
    if not transitions:
        raise ValueError(f"No transitions found in {input_path}")

    states = torch.tensor([t.state_features for t in transitions], dtype=torch.float32)
    rewards = torch.tensor([t.reward for t in transitions], dtype=torch.float32)
    dones = torch.tensor([t.done for t in transitions], dtype=torch.bool)

    max_action_dim = max(len(action) for t in transitions for action in t.legal_action_features)
    model = ActionCriticNet(
        input_size=int(states.shape[-1] + max_action_dim),
        hidden_size=hidden_size,
        num_blocks=num_blocks,
        dropout=dropout,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    loader = DataLoader(list(range(len(transitions))), batch_size=batch_size, shuffle=True)
    with torch.no_grad():
        values = model.forward_value(states).squeeze(-1)
        advantages, returns = _compute_episode_gae(episodes, values, gamma=gamma, lam=0.95)
        old_log_probs = torch.zeros(len(transitions), dtype=torch.float32)
        for index, transition in enumerate(transitions):
            if transition.behavior_log_prob is not None:
                old_log_probs[index] = float(transition.behavior_log_prob)
            else:
                action_tensor = torch.tensor(transition.legal_action_features, dtype=torch.float32)
                state_tensor = states[index : index + 1]
                action_count = action_tensor.shape[0]
                state_expanded = state_tensor.unsqueeze(1).expand(-1, action_count, -1)
                merged = torch.cat([state_expanded, action_tensor.unsqueeze(0)], dim=-1).reshape(action_count, -1)
                scores = model.forward_policy(merged).reshape(1, action_count)
                old_log_probs[index] = torch.log_softmax(scores, dim=-1)[0, transition.chosen_index]

    final_loss = policy_loss = value_loss = entropy = 0.0
    model.train()
    for epoch in range(epochs):
        running_loss = running_policy = running_value = running_entropy = 0.0
        batches = 0
        for batch_indices in loader:
            batch_indices = batch_indices.long()
            batch_states = states[batch_indices]
            batch_advantages = advantages[batch_indices]
            batch_returns = returns[batch_indices]
            batch_chosen = torch.tensor([transitions[i].chosen_index for i in batch_indices.tolist()], dtype=torch.long)
            batch_actions_list = [
                torch.tensor(transitions[i].legal_action_features, dtype=torch.float32)
                for i in batch_indices.tolist()
            ]
            batch_actions, action_mask = _pad_actions(batch_actions_list, batch_chosen)

            batch_size_ = batch_states.shape[0]
            action_count = batch_actions.shape[1]
            state_expanded = batch_states.unsqueeze(1).expand(-1, action_count, -1)
            merged = torch.cat([state_expanded, batch_actions], dim=-1).reshape(batch_size_ * action_count, -1)
            scores = model.forward_policy(merged).reshape(batch_size_, action_count)
            scores = scores.masked_fill(~action_mask, -1e9)
            log_probs = torch.log_softmax(scores, dim=-1)
            probs = torch.softmax(scores, dim=-1)
            chosen_log_probs = log_probs.gather(1, batch_chosen.unsqueeze(1)).squeeze(1)
            ratio = torch.exp(chosen_log_probs - old_log_probs[batch_indices])
            clipped = torch.clamp(ratio, 1.0 - clip_ratio, 1.0 + clip_ratio)
            policy_loss_value = -torch.min(ratio * batch_advantages, clipped * batch_advantages).mean()
            entropy_value = -(probs * log_probs).sum(dim=-1).mean()
            values_pred = model.forward_value(batch_states).squeeze(-1)
            value_loss_value = _value_loss(values_pred, batch_returns)
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
    return PPOTrainSummary(
        transitions=len(transitions),
        epochs=epochs,
        final_loss=final_loss,
        policy_loss=policy_loss,
        value_loss=value_loss,
        entropy=entropy,
        output_path=output,
    )
