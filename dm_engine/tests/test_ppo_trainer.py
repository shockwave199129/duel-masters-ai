"""
tests/test_ppo_trainer.py - PPO buffer and reward smoke tests.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from training.replay_buffer import ReplayBuffer, Transition
from training.rewards import step_reward

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" - {detail}" if detail else ""))


buffer = ReplayBuffer(capacity=2)
buffer.add(Transition(state_features=[0.0], legal_action_features=[[1.0]], chosen_index=0, reward=0.1, done=False, player=0))
buffer.add(Transition(state_features=[1.0], legal_action_features=[[0.0]], chosen_index=0, reward=0.2, done=True, player=1))
buffer.add(Transition(state_features=[2.0], legal_action_features=[[0.0]], chosen_index=0, reward=0.3, done=True, player=0))

check("Replay buffer caps size", len(buffer) == 2)
check("Replay buffer keeps newest transition", buffer.to_list()[-1].state_features == [2.0])
check("Row-based step reward returns float", isinstance(step_reward({"heuristic_target": 0.1, "value_target": 0.0}, {"heuristic_target": 0.2, "value_target": 1.0}, 0), float))

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
