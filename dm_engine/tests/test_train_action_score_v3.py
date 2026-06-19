"""
tests/test_train_action_score_v3.py - v3 trainer and ranking-loss smoke tests.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.action_encoder import ACTION_ENCODER_VERSION_V3, ACTION_VECTOR_SIZE_V3
from bot.state_encoder import OBSERVATION_ENCODER_VERSION_V3, OBSERVATION_VECTOR_SIZE_V3
from training.train_action_score import train_action_score_model

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
    }


print("\n" + "=" * 60)
print("  DM ENGINE - V3 TRAINER TESTS")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    data_path = tmp_path / "v3_rows.jsonl"
    data_path.write_text(
        "\n".join(json.dumps(_row(i % 2)) for i in range(4)) + "\n",
        encoding="utf-8",
    )

    mse_output = tmp_path / "mse.pt"
    mse_summary = train_action_score_model(
        input_path=data_path,
        output_path=mse_output,
        epochs=1,
        batch_size=2,
        hidden_size=16,
        num_blocks=1,
        dropout=0.0,
        loss_mode="mse",
    )
    check("MSE trainer writes checkpoint", mse_output.exists())
    check("MSE trainer reports v3 flattened examples", mse_summary.rows == 8)

    pairwise_output = tmp_path / "pairwise.pt"
    pairwise_summary = train_action_score_model(
        input_path=data_path,
        output_path=pairwise_output,
        epochs=1,
        batch_size=2,
        hidden_size=16,
        num_blocks=1,
        dropout=0.0,
        loss_mode="pairwise",
    )
    check("Pairwise trainer writes checkpoint", pairwise_output.exists())
    check("Pairwise trainer reports ranking pairs", pairwise_summary.rows == 4)

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
