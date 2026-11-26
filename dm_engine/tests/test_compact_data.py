"""
tests/test_compact_data.py - the compact training data gives the same batches as
the ordinary loaders, and both trainers can use it.
"""

import json
import os
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import torch

from bot.action_encoder import ACTION_VECTOR_SIZE_V3
from bot.state_encoder import OBSERVATION_VECTOR_SIZE_V3
from training.compact_data import DecisionBatches, PairBatches, load_compact
from training.train_action_score import (
    _load_grouped_dataset, _load_pairwise_dataset, _normalize_state_action_row, collate_decisions,
    train_action_score_model,
)
from training.train_actor_critic import train_actor_critic_model

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


rng = random.Random(3)


def vector(size):
    return [round(rng.random(), 3) for _ in range(size)]


folder = Path(tempfile.mkdtemp())
data_file = folder / "games.jsonl"
with data_file.open("w", encoding="utf-8") as out:
    for number in range(40):
        legal = rng.randint(1, 5)
        out.write(json.dumps({
            "schema_version": 3, "player": 0 if number < 26 else 1,
            "state_features": vector(OBSERVATION_VECTOR_SIZE_V3),
            "legal_action_features": [vector(ACTION_VECTOR_SIZE_V3) for _ in range(legal)],
            "chosen_index": rng.randrange(legal), "value_target": rng.choice([-1.0, 1.0]),
            "blended_target": rng.random(), "heuristic_target": rng.random(),
        }) + "\n")

cpu = torch.device("cpu")
for balance in (False, True):
    compact = load_compact(data_file, device=cpu, balance_players=balance, seed=1,
                           normalize=_normalize_state_action_row)
    usual = _load_grouped_dataset(data_file, balance_players=balance, seed=1)
    check(f"balance={balance}: the same decisions are kept", len(compact) == len(usual)
          and len(compact) == (28 if balance else 40), f"{len(compact)} {len(usual)}")
    if not balance:  # file order is kept, so batches can be compared one to one
        mine = next(iter(DecisionBatches(compact, 40, seed=1, shuffle=False)))
        theirs = collate_decisions([usual[i] for i in range(40)])
        check("decision batches match the ordinary loader",
              all(torch.equal(a, b) for a, b in zip(mine, theirs)))
        pairs = PairBatches(compact, 10_000, seed=1, shuffle=False)
        mine = next(iter(pairs))
        theirs = _load_pairwise_dataset(data_file, balance_players=False).tensors
        check("move pairs match the ordinary loader",
              all(torch.equal(a, b) for a, b in zip(mine, theirs))
              and pairs.pairs == len(theirs[0]))
        shuffled = PairBatches(compact, 7, seed=5)
        seen = torch.cat([batch[1] for batch in shuffled])
        check("shuffled batches use every pair once",
              len(seen) == pairs.pairs
              and torch.equal(seen.sum(dim=1).sort().values, theirs[1].sum(dim=1).sort().values))

summary = train_action_score_model(
    input_path=data_file, output_path=folder / "score.pt", epochs=2, batch_size=16,
    loss_mode="pairwise", device="cpu", data_on_gpu=True, warmup_steps=3,
    hidden_size=32, num_blocks=1, balance_players=False)
check("the action score trainer runs on it",
      (folder / "score.pt").exists() and summary.final_loss == summary.final_loss)
summary = train_actor_critic_model(
    input_path=data_file, output_path=folder / "critic.pt", epochs=2, batch_size=8,
    device="cpu", data_on_gpu=True, hidden_size=32, num_blocks=1)
check("the actor-critic trainer runs on it",
      (folder / "critic.pt").exists() and summary.final_loss == summary.final_loss)

print(f"\n{len(FAILURES)} failed")
sys.exit(1 if FAILURES else 0)
