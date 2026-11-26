"""
tests/test_self_play_parallel.py - parallel self-play summary smoke tests.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from training.self_play import SelfPlaySummary
from training import self_play
from training.self_play_parallel import _merge_shards, _merge_summaries

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" - {detail}" if detail else ""))


summary = _merge_summaries(
    [
        SelfPlaySummary(games=1, decisions=2, player0_wins=1, player1_wins=0, no_winner_terminal=0, unfinished=0),
        SelfPlaySummary(games=2, decisions=5, player0_wins=0, player1_wins=2, no_winner_terminal=0, unfinished=1),
    ]
)

check("Merged games", summary.games == 3)
check("Merged decisions", summary.decisions == 7)
check("Merged wins", summary.player0_wins == 1 and summary.player1_wins == 2)
check("Merged unfinished", summary.unfinished == 1)

with tempfile.TemporaryDirectory() as tmp:
    shard = Path(tmp) / "shard.jsonl"
    shard.write_text("new\n", encoding="utf-8")
    output = Path(tmp) / "games.jsonl"
    output.write_text("old\n", encoding="utf-8")
    _merge_shards([shard], output, overwrite=False)
    check("Merge appends without overwrite", output.read_text(encoding="utf-8") == "old\nnew\n")
    _merge_shards([shard], output, overwrite=True)
    check("Merge replaces with overwrite", output.read_text(encoding="utf-8") == "new\n")

game_ids = []


def fake_game(**kwargs):
    game_ids.append(kwargs["game_id"])
    return type("State", (), {"result": self_play.GameResult.PLAYER_0_WINS})(), 0


self_play.run_recorded_game = fake_game
for seed_start in (1, 100001):  # seed offsets used by two parallel workers
    self_play.run_self_play_games(db=None, deck_json=None, output_path="unused", games=3, seed_start=seed_start)
check("Game ids are unique across workers", len(set(game_ids)) == 6, str(game_ids))

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
