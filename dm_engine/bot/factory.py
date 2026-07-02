"""Bot construction helpers for self-play and evaluation."""

from __future__ import annotations

from pathlib import Path

from bot.heuristic_bot import HeuristicBot
from bot.random_bot import RandomBot


def make_bot(spec: str, *, seed: int, rule_service=None, model_path: str | Path | None = None):
    """Construct a bot from a compact spec string."""
    normalized = spec.strip().lower()
    if normalized == "random":
        return RandomBot(seed=seed)
    if normalized == "heuristic":
        return HeuristicBot(seed=seed)
    if normalized in {"neural", "current"}:
        from bot.neural_bot import NeuralBot
        return NeuralBot(model_path=model_path, seed=seed, rule_service=rule_service)
    if normalized.startswith("neural:"):
        from bot.neural_bot import NeuralBot
        return NeuralBot(model_path=Path(spec.split(":", 1)[1]), seed=seed, rule_service=rule_service)
    if normalized.startswith("checkpoint:"):
        from bot.neural_bot import NeuralBot
        return NeuralBot(model_path=Path(spec.split(":", 1)[1]), seed=seed, rule_service=rule_service)
    raise ValueError(f"Unsupported bot spec: {spec}")
