import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.factory import make_bot
from bot.neural_bot import NeuralBot

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


if __name__ == "__main__":
    bot = make_bot("neural", seed=1)
    check("factory returns NeuralBot", isinstance(bot, NeuralBot))
    check("bot uses CPU by default", bot.device.type == "cpu", f"device={bot.device}")
    check("explicit CPU is honoured", make_bot("neural", seed=1, device="cpu").device.type == "cpu")
    if FAILURES:
        sys.exit(1)
