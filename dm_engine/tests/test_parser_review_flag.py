"""
tests/test_parser_review_flag.py - coerced parser output is flagged for review.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "crawler"))

from scripts.effect_parser import _coerce_effect_enums

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


valid = {
    "effect_type": "triggered", "trigger_event": "on_attack", "effect_action": "draw",
    "active_in_zone": ["battle_zone"], "active_in_phase": ["any"], "parse_confidence": 0.95,
}
effect = dict(valid)
check("Valid effect is left alone", not _coerce_effect_enums(1, effect) and effect == valid)

effect = dict(valid, effect_action="invented_action")
check("Invalid value is coerced", _coerce_effect_enums(1, effect) and effect["effect_action"] == "none")
check("Confidence is capped", effect["parse_confidence"] == 0.3)
check("Effect is flagged for review", effect["needs_review"] is True)

effect = dict(valid, active_in_zone=["nowhere"], parse_confidence=0.1)
_coerce_effect_enums(1, effect)
check("Lower confidence is kept", effect["parse_confidence"] == 0.1 and effect["needs_review"])

if FAILURES:
    print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
    sys.exit(1)
