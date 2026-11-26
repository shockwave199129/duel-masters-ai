"""
training/compact_data.py - the whole training set held on the training device.

Each decision's state is stored once and its legal actions once, so a data set
that would need many gigabytes as one row per (state, action) pair fits in GPU
memory. Batches are assembled on the device and have the same shape as the ones
the ordinary data loaders give.
"""

from __future__ import annotations

import json
import logging
import random
from array import array
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch

logger = logging.getLogger(__name__)


@dataclass
class CompactData:
    states: torch.Tensor      # [decisions, state_size]
    actions: torch.Tensor     # [all legal actions, action_size]
    first: torch.Tensor       # [decisions] position of each decision's first action
    count: torch.Tensor       # [decisions] number of legal actions
    chosen: torch.Tensor      # [decisions] which of them was played
    policy: torch.Tensor      # [all legal actions]
    blended: torch.Tensor     # [decisions]
    heuristic: torch.Tensor   # [decisions]
    value: torch.Tensor       # [decisions]

    @property
    def state_size(self) -> int:
        return int(self.states.shape[1])

    @property
    def action_size(self) -> int:
        return int(self.actions.shape[1])

    def __len__(self) -> int:
        return int(self.states.shape[0])

    def bytes_used(self) -> int:
        return sum(t.element_size() * t.nelement() for t in vars(self).values())


def load_compact(
    path: str | Path,
    *,
    device: torch.device,
    balance_players: bool,
    seed: int,
    normalize,
) -> CompactData:
    """
    Read self-play rows and put them on ``device``. ``normalize`` checks one row
    and returns (schema version, state, legal actions, chosen index).
    """
    keep = _balanced_lines(path, seed) if balance_players else None
    states, actions, policy = array("f"), array("f"), array("f")
    counts, chosen, blended, heuristic, value = (
        array("q"), array("q"), array("f"), array("f"), array("f"))
    schema, state_size, action_size = None, 0, 0
    with Path(path).open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip() or (keep is not None and number not in keep):
                continue
            row = json.loads(line)
            version, state, legal, picked = normalize(row, path=path, line_number=number)
            if schema is None:
                schema, state_size, action_size = version, len(state), len(legal[0])
            elif version != schema:
                raise ValueError("Mixed schema versions are not supported in one training file")
            states.extend(state)
            for features in legal:
                actions.extend(features)
            target = row.get("policy_target")
            if target is None:
                target = [1.0 if i == picked else 0.0 for i in range(len(legal))]
            elif not isinstance(target, list):
                target = [float(target)] * len(legal)
            if len(target) != len(legal):
                raise ValueError(f"Expected target vector of length {len(legal)}")
            policy.extend(float(item) for item in target)
            counts.append(len(legal))
            chosen.append(picked)
            blended.append(float(row.get("blended_target", row.get("heuristic_target", 0.0))))
            heuristic.append(float(row.get("heuristic_target", 0.0)))
            value.append(float(row.get("value_target", 0.0)))
    if not counts:
        raise ValueError(f"No training rows found in {path}")

    # The tensors share the read buffers, so a large data set is not held twice
    # in system memory; moving to the GPU then makes the only copy there.
    def floats(buffer: array, width: int | None = None) -> torch.Tensor:
        view = np.frombuffer(buffer, dtype=np.float32)
        if width:
            view = view.reshape(-1, width)
        return torch.from_numpy(view).to(device)

    def integers(buffer: array) -> torch.Tensor:
        return torch.from_numpy(np.frombuffer(buffer, dtype=np.int64)).to(device)

    count = integers(counts)
    return CompactData(
        states=floats(states, state_size), actions=floats(actions, action_size),
        first=torch.cumsum(count, 0) - count, count=count, chosen=integers(chosen),
        policy=floats(policy), blended=floats(blended), heuristic=floats(heuristic),
        value=floats(value),
    )


def _balanced_lines(path: str | Path, seed: int) -> set[int] | None:
    """Line numbers that give both players the same number of decisions."""
    by_player: dict[int, list[int]] = {0: [], 1: []}
    with Path(path).open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if line.strip():
                player = int(json.loads(line).get("player", 0))
                if player in by_player:
                    by_player[player].append(number)
    smaller = min(len(rows) for rows in by_player.values())
    if not smaller:
        return None
    rng = random.Random(seed)
    return set(rng.sample(by_player[0], smaller)) | set(rng.sample(by_player[1], smaller))


class DecisionBatches:
    """Batches of decisions: states, padded actions, targets and a mask."""

    def __init__(self, data: CompactData, batch_size: int, *, seed: int, shuffle: bool = True):
        self.data, self.batch_size, self.shuffle = data, batch_size, shuffle
        self.generator = torch.Generator(device=data.states.device).manual_seed(seed)

    def __len__(self) -> int:
        return -(-len(self.data) // self.batch_size)

    def __iter__(self):
        data = self.data
        device = data.states.device
        order = (torch.randperm(len(data), generator=self.generator, device=device)
                 if self.shuffle else torch.arange(len(data), device=device))
        for start in range(0, len(data), self.batch_size):
            picked = order[start:start + self.batch_size]
            count = data.count[picked]
            slots = torch.arange(int(count.max()), device=device)
            mask = slots.unsqueeze(0) < count.unsqueeze(1)
            where = (data.first[picked].unsqueeze(1) + slots)[mask]
            padded = data.actions.new_zeros((*mask.shape, data.action_size))
            padded[mask] = data.actions[where]
            policy = data.policy.new_zeros(mask.shape)
            policy[mask] = data.policy[where]
            yield (data.states[picked], padded, policy, data.blended[picked],
                   data.heuristic[picked], data.value[picked], mask)


class PairBatches:
    """Batches of (chosen move, other legal move) from the same decision."""

    def __init__(self, data: CompactData, batch_size: int, *, seed: int, shuffle: bool = True):
        self.data, self.batch_size, self.shuffle = data, batch_size, shuffle
        device = data.states.device
        decision = torch.repeat_interleave(torch.arange(len(data), device=device), data.count)
        position = torch.arange(len(decision), device=device) - data.first[decision]
        other = (position != data.chosen[decision]).nonzero(as_tuple=True)[0]
        if not len(other):
            raise ValueError("No ranking pairs found")
        self.other = other
        self.decision = decision[other]
        self.generator = torch.Generator(device=device).manual_seed(seed)

    @property
    def pairs(self) -> int:
        return int(len(self.other))

    def __len__(self) -> int:
        return -(-self.pairs // self.batch_size)

    def __iter__(self):
        data = self.data
        device = data.states.device
        order = (torch.randperm(self.pairs, generator=self.generator, device=device)
                 if self.shuffle else torch.arange(self.pairs, device=device))
        for start in range(0, self.pairs, self.batch_size):
            picked = order[start:start + self.batch_size]
            decision = self.decision[picked]
            state = data.states[decision]
            played = data.actions[data.first[decision] + data.chosen[decision]]
            yield (torch.cat([state, played], dim=-1),
                   torch.cat([state, data.actions[self.other[picked]]], dim=-1),
                   state.new_ones(len(picked)))
