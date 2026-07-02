"""
tests/test_self_play_league.py - bot factory and league sampling smoke tests.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.factory import make_bot

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" - {detail}" if detail else ""))


random_bot = make_bot("random", seed=1)
heuristic_bot = make_bot("heuristic", seed=2)
check("Factory creates random bot", type(random_bot).__name__ == "RandomBot")
check("Factory creates heuristic bot", type(heuristic_bot).__name__ == "HeuristicBot")

try:
    make_bot("nonsense", seed=0)
    check("Factory rejects invalid spec", False, "expected ValueError")
except ValueError:
    check("Factory rejects invalid spec", True)

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
