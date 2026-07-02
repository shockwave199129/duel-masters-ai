"""
tests/test_eval_bots.py - evaluation harness smoke tests.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from training.eval import summarize_matchups, write_eval_reports

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" - {detail}" if detail else ""))


rows = [
    {"opponent": "random", "winner": 0, "result": "player_0_wins", "steps": 2, "p0_shields": 5, "p1_shields": 0},
    {"opponent": "random", "winner": 1, "result": "player_1_wins", "steps": 4, "p0_shields": 1, "p1_shields": 3},
]

summary = summarize_matchups(rows)
check("Summary produced one opponent row", len(summary) == 1)
check("Summary win-rate computed", abs(summary[0].win_rate - 0.5) < 1e-9)
check("Summary average steps computed", abs(summary[0].avg_steps - 3.0) < 1e-9)
check("Summary unfinished rate computed", abs(summary[0].unfinished_rate - 0.0) < 1e-9)
check("Summary shield average computed", abs(summary[0].avg_shields_remaining - 4.5) < 1e-9)

tmp_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "dm_eval_bots_test"
tmp_dir.mkdir(parents=True, exist_ok=True)
json_path = tmp_dir / "eval.json"
csv_path = tmp_dir / "eval.csv"
reported = write_eval_reports(results=rows, json_path=json_path, csv_path=csv_path)
check("JSON report written", json_path.exists())
check("CSV report written", csv_path.exists())
check("Report summary returned", len(reported) == 1)

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
