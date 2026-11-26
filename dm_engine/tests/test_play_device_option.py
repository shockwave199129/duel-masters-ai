"""
tests/test_play_device_option.py - play scripts accept --device and default to CPU.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import eval_bots
import play_neural_game
import run_self_play

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


for module, required in (
    (run_self_play, []),
    (eval_bots, ["--model-path", "model.pt"]),
    (play_neural_game, []),
):
    parser = module._build_parser()
    name = module.__name__
    check(f"{name} defaults to CPU", parser.parse_args(required).device == "cpu")
    check(f"{name} accepts cuda", parser.parse_args([*required, "--device", "cuda"]).device == "cuda")

if FAILURES:
    print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
    sys.exit(1)
