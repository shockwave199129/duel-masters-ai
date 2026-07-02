"""Simple fixed-size replay buffer for PPO training."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Transition:
    state_features: list[float]
    legal_action_features: list[list[float]]
    chosen_index: int
    reward: float
    done: bool
    player: int
    value_target: float = 0.0
    behavior_log_prob: float | None = None
    policy_log_prob: float | None = None


class ReplayBuffer:
    def __init__(self, capacity: int = 100_000):
        self.capacity = capacity
        self._buffer: deque[Transition] = deque(maxlen=capacity)

    def add(self, transition: Transition) -> None:
        self._buffer.append(transition)

    def extend(self, transitions: list[Transition]) -> None:
        self._buffer.extend(transitions)

    def __len__(self) -> int:
        return len(self._buffer)

    def to_list(self) -> list[Transition]:
        return list(self._buffer)
