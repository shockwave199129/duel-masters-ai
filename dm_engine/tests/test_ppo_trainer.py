"""
tests/test_ppo_trainer.py - PPO buffer and reward smoke tests.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import torch

from bot.action_encoder import ACTION_VECTOR_SIZE_V3
from bot.neural_model import ActionCriticNet, ActionScoreNet, load_actor_critic_init, load_model, save_model
from bot.state_encoder import OBSERVATION_VECTOR_SIZE_V3
from training.ppo_trainer import _build_episodes_from_jsonl, train_ppo
from training.replay_buffer import ReplayBuffer, Transition

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


def game_rows(game_id, *, winner, terminal=True, heuristics=None):
    """Six alternating decisions with 2 to 4 legal moves, seen by the acting player."""
    rows = []
    for step in range(6):
        player = step % 2
        outcome = 0.0 if winner is None else (1.0 if player == winner else -1.0)
        rows.append(
            {
                "game_id": game_id,
                "seed": 1,
                "step": step,
                "player": player,
                "terminal": terminal,
                "value_target": outcome,
                "heuristic_target": heuristics[step] if heuristics else 0.2 * outcome,
                "state_features": [0.0] * OBSERVATION_VECTOR_SIZE_V3,
                "legal_action_features": [[0.1 * i] * ACTION_VECTOR_SIZE_V3 for i in range(2 + step % 3)],
                "chosen_index": 1,
            }
        )
    return rows


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def total_reward(episodes, player):
    return sum(item.reward for episode in episodes for item in episode if item.player == player)


with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    data = tmp_path / "rows.jsonl"

    write_rows(data, game_rows("won", winner=0))
    episodes = _build_episodes_from_jsonl(data)
    check("One episode per player", [sorted({i.player for i in e}) for e in episodes] == [[0], [1]])
    check("Only each player's last step is terminal", all([i.done for i in e] == [False, False, True] for e in episodes))
    check("Winner's total reward is positive", total_reward(episodes, 0) > 0, str(total_reward(episodes, 0)))
    check("Loser's total reward is negative", total_reward(episodes, 1) < 0, str(total_reward(episodes, 1)))

    # Worst case for shaping: the winner's position looks as bad as possible by the end
    write_rows(data, game_rows("comeback", winner=0, heuristics=[1.0, -1.0, 0.0, 0.0, -1.0, 1.0]))
    episodes = _build_episodes_from_jsonl(data)
    check("Outcome outweighs shaping for the winner", total_reward(episodes, 0) > 0)
    check("Outcome outweighs shaping for the loser", total_reward(episodes, 1) < 0)

    write_rows(data, game_rows("won", winner=0) + game_rows("cut-off", winner=None, terminal=False))
    check("Unfinished games are left out", sum(len(e) for e in _build_episodes_from_jsonl(data)) == 6)

    sizes = {"hidden_size": 16, "num_blocks": 1, "dropout": 0.0}
    input_size = OBSERVATION_VECTOR_SIZE_V3 + ACTION_VECTOR_SIZE_V3
    base = ActionScoreNet(input_size=input_size, **sizes)
    base_path = tmp_path / "score.pt"
    save_model(base, base_path)
    trained_path = tmp_path / "ppo.pt"
    train_ppo(input_path=data, output_path=trained_path, init_checkpoint=base_path, epochs=1, batch_size=4, lr=0.0)
    trained = load_model(trained_path).state_dict()
    check(
        "PPO continues from an action-score checkpoint",
        all(torch.equal(value, trained[key]) for key, value in base.state_dict().items()),
    )

    resumed = load_actor_critic_init(trained_path).state_dict()
    check("Actor-critic checkpoint resumes in full", all(torch.equal(v, resumed[k]) for k, v in trained.items()))

    checkpoint = torch.load(trained_path)
    del checkpoint["state_dict"]["input_norm.weight"]
    broken_path = tmp_path / "broken.pt"
    torch.save(checkpoint, broken_path)
    try:
        load_model(broken_path)
        rejected = False
    except RuntimeError:
        rejected = True
    check("Checkpoint missing a shared layer is rejected", rejected)

    train_ppo(input_path=data, output_path=trained_path, epochs=1, batch_size=4, **sizes)
    check("PPO trains from scratch", isinstance(load_model(trained_path), ActionCriticNet))

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
