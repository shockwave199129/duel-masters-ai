"""
tests/test_actor_critic.py - actor-critic smoke tests.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import torch

from bot.action_encoder import ACTION_ENCODER_VERSION_V3, ACTION_VECTOR_SIZE_V3
from bot.neural_model import ActionCriticNet, load_model
from bot.state_encoder import OBSERVATION_ENCODER_VERSION_V3, OBSERVATION_VECTOR_SIZE_V3
from training.train_actor_critic import train_actor_critic_model

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" - {detail}" if detail else ""))


def _row(chosen_index=0):
    state = [0.0] * OBSERVATION_VECTOR_SIZE_V3
    action_a = [0.0] * ACTION_VECTOR_SIZE_V3
    action_b = [0.0] * ACTION_VECTOR_SIZE_V3
    action_a[0] = 1.0
    action_b[1] = 1.0
    return {
        "schema_version": 3,
        "observation_version": OBSERVATION_ENCODER_VERSION_V3,
        "action_version": ACTION_ENCODER_VERSION_V3,
        "observation_vector_size": OBSERVATION_VECTOR_SIZE_V3,
        "action_vector_size": ACTION_VECTOR_SIZE_V3,
        "state_features": state,
        "legal_action_features": [action_a, action_b],
        "chosen_index": chosen_index,
        "blended_target": 0.75,
        "heuristic_target": 0.25,
        "value_target": 1.0 if chosen_index == 0 else -1.0,
    }


print("\n" + "=" * 60)
print("  DM ENGINE - ACTOR-CRITIC TESTS")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    data_path = tmp_path / "ac_rows.jsonl"
    data_path.write_text(
        "\n".join(json.dumps(_row(i % 2)) for i in range(4)) + "\n",
        encoding="utf-8",
    )

    model = ActionCriticNet(input_size=OBSERVATION_VECTOR_SIZE_V3 + ACTION_VECTOR_SIZE_V3, hidden_size=16, num_blocks=1, dropout=0.0)
    batch = torch.zeros(2, OBSERVATION_VECTOR_SIZE_V3 + ACTION_VECTOR_SIZE_V3)
    values = model.forward_value(batch[:, :OBSERVATION_VECTOR_SIZE_V3])
    scores = model.forward_policy(batch)
    check("Value head forward shape", values.shape == (2, 1))
    check("Policy head forward shape", scores.shape == (2, 1))

    output_path = tmp_path / "critic.pt"
    summary = train_actor_critic_model(
        input_path=data_path,
        output_path=output_path,
        epochs=1,
        batch_size=2,
        hidden_size=16,
        num_blocks=1,
        dropout=0.0,
        value_target_field="blended_target",
        entropy_coef=0.0,
    )
    check("Actor-critic writes checkpoint", output_path.exists())
    check("Actor-critic trains on grouped rows", summary.rows == 4)

    loaded = load_model(output_path)
    check("Loaded critic checkpoint", isinstance(loaded, ActionCriticNet))

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
