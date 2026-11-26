"""
tests/test_card_text_split.py - wiki links do not cut card text short.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "crawler"))

from scripts.api_scraper import _split_template_params

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


text = "■ Gets -2000 [[power]] until the [[End Step|end of the turn]]. ■ {{Mode|2}} [[A|B]] done."
params = _split_template_params("{{Cardtable\n| engtext = " + text + "\n| jptext = x\n}}")
check("Text after a piped link is kept", params.get("engtext") == text, repr(params.get("engtext")))
check("The next field is still separate", params.get("jptext") == "x")

if FAILURES:
    print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
    sys.exit(1)
