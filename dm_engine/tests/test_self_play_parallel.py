"""
tests/test_self_play_parallel.py - parallel self-play summary smoke tests.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from training.self_play import SelfPlaySummary
from training.self_play_parallel import _merge_summaries

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

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
