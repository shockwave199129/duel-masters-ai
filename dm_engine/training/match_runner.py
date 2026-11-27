"""
training/match_runner.py - bot matches, several games at a time.

Games come in pairs that share one deal: the same two decks, the same card order
and the same first seat. The player being tested ("contender") sits in seat 0 in
one game of the pair and in seat 1 in the other, so the luck of the deal cancels.

A bot is described by a short string: "random", "heuristic", "neural:<model>" or
"mcts:<model>".
"""

from __future__ import annotations

import logging
import math
import multiprocessing as mp
import random
import time
from dataclasses import dataclass
from pathlib import Path

from core.initializer import initialize_game
from decks.prebuilt import _apply_extra_zones
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions

BOT_KINDS = ("random", "heuristic", "neural", "mcts")


@dataclass(frozen=True)
class MatchConfig:
    contender: str
    opponent: str
    games: int = 100
    seed: int = 10_000
    max_steps: int = 1000
    device: str = "cpu"
    trees: int = 8
    simulations: int = 512          # per decision, shared by the trees
    c_puct: float = 1.5
    prior_temperature: float = 1.0


def parse_bot(spec: str) -> tuple[str, str | None]:
    kind, _, path = spec.partition(":")
    if kind not in BOT_KINDS:
        raise ValueError(f"Unsupported bot: {spec!r}; use one of {', '.join(BOT_KINDS)}")
    needs_model = kind in ("neural", "mcts")
    if needs_model != bool(path):
        raise ValueError(f"{kind!r} {'needs' if needs_model else 'takes no'} model path: {spec!r}")
    return kind, path or None


def contender_seat(game: int) -> int:
    return game % 2


def new_match_state(pool, game: int, seed: int):
    """The deal for ``game``; both games of a pair get the same one."""
    pair = game // 2
    rng = random.Random(seed + pair)
    first, second = rng.sample(list(pool.ids), 2)
    state = initialize_game(
        pool.decks[first].main_deck, pool.decks[second].main_deck,
        first_player=pair % 2, seed=seed + pair, game_id=f"match-{seed}-{pair}")
    _apply_extra_zones(state, 0, pool.decks[first])
    _apply_extra_zones(state, 1, pool.decks[second])
    return state


class BotFactory:
    """Builds bots for each game, loading each model file once."""

    def __init__(self, config: MatchConfig, rule_service=None):
        self.config = config
        self.rule_service = rule_service
        self._models: dict[str, object] = {}

    def _model(self, path: str):
        if path not in self._models:
            from bot.neural_model import load_model

            self._models[path] = load_model(path, device=self.config.device)
        return self._models[path]

    def build(self, spec: str, seed: int):
        kind, path = parse_bot(spec)
        if kind == "random":
            from bot.random_bot import RandomBot

            return RandomBot(seed=seed)
        if kind == "heuristic":
            from bot.heuristic_bot import HeuristicBot

            return HeuristicBot(seed=seed)
        if kind == "neural":
            from bot.neural_bot import NeuralBot

            return NeuralBot(model=self._model(path), seed=seed, epsilon=0.0,
                             rule_service=self.rule_service, device=self.config.device)
        from bot.mcts_bot import MCTSBot
        from search.mcts import SearchConfig

        config = self.config
        return MCTSBot(
            model=self._model(path), seed=seed, rule_service=self.rule_service,
            device=config.device, trees=config.trees,
            prior_temperature=config.prior_temperature,
            config=SearchConfig(simulations=max(1, config.simulations // config.trees),
                                c_puct=config.c_puct))


def _choose(bot, state, actions, db):
    if hasattr(bot, "choose_from_actions"):
        return bot.choose_from_actions(state, actions, db=db)
    return bot.choose_action(state, db=db)


def play_match_game(pool, factory: BotFactory, game: int, db=None) -> dict:
    """Play one game. A crash is reported in the result, not raised."""
    config = factory.config
    seat = contender_seat(game)
    started = time.perf_counter()
    decisions, decision_seconds, steps = 0, 0.0, 0
    error = None
    state = None
    try:
        state = new_match_state(pool, game, config.seed)
        bots = [None, None]
        bots[seat] = factory.build(config.contender, config.seed + game)
        bots[1 - seat] = factory.build(config.opponent, config.seed + 50_000 + game)
        while steps < config.max_steps and not state.is_terminal():
            actions = get_legal_actions(state, db)
            if not actions:
                raise RuntimeError("No legal actions available")
            acting = actions[0].player
            timed = acting == seat and len(actions) > 1
            before = time.perf_counter()
            action = _choose(bots[acting], state, actions, db)
            if timed:  # only the contender's real choices
                decisions += 1
                decision_seconds += time.perf_counter() - before
            state = execute_action(state, action, db=db, validate=False)
            steps += 1
    except Exception as exc:  # noqa: BLE001 - one bad game must not lose the run
        error = f"{type(exc).__name__}: {exc}"
    winner = state.winner() if state is not None and state.is_terminal() else None
    if error:
        outcome = "error"
    elif winner is None:
        outcome = "unfinished"
    else:
        outcome = "win" if winner == seat else "loss"
    return {
        "game": game, "contender_seat": seat, "outcome": outcome, "error": error,
        "steps": steps, "seconds": time.perf_counter() - started,
        "decisions": decisions, "decision_seconds": decision_seconds,
        "contender_shields": len(state.players[seat].shield_zone) if state else 0,
        "opponent_shields": len(state.players[1 - seat].shield_zone) if state else 0,
    }


def summarize_games(rows: list[dict], wall_seconds: float) -> dict:
    count = {name: sum(1 for r in rows if r["outcome"] == name)
             for name in ("win", "loss", "unfinished", "error")}
    decided = count["win"] + count["loss"]
    rate = count["win"] / decided if decided else 0.0
    decisions = sum(r["decisions"] for r in rows)
    return {
        "games": len(rows),
        "wins": count["win"], "losses": count["loss"],
        "unfinished": count["unfinished"], "errors": count["error"],
        # Share of decided games, with a rough 95% margin
        "win_rate": rate,
        "margin": 1.96 * math.sqrt(rate * (1 - rate) / decided) if decided else 0.0,
        "wall_seconds": wall_seconds,
        "seconds_per_game": sum(r["seconds"] for r in rows) / len(rows) if rows else 0.0,
        "seconds_per_decision": (
            sum(r["decision_seconds"] for r in rows) / decisions if decisions else 0.0),
    }


def load_match_resources(dsn: str):
    """Card database, rules and the training decks."""
    from db.card_database import CardDatabase
    from rules import RuleKnowledgeService
    from training.self_play import load_training_deck_pool

    db = CardDatabase(dsn)
    db.load()
    rules = RuleKnowledgeService.from_card_database(db)
    rules.preload()
    return db, rules, load_training_deck_pool(db)


_worker: dict = {}


def _init_worker(dsn: str, config: MatchConfig) -> None:
    # A pool replaces a worker whose setup raises, again and again, so a failure
    # is kept here and raised by the first game instead
    try:
        import torch

        # One thread each: the workers already use the processor cores
        torch.set_num_threads(1)
        logging.disable(logging.WARNING)
        db, rules, pool = load_match_resources(dsn)
        _worker.update(db=db, pool=pool, factory=BotFactory(config, rules))
    except Exception as exc:  # noqa: BLE001
        _worker.update(failure=f"{type(exc).__name__}: {exc}")


def _worker_game(game: int) -> dict:
    if "failure" in _worker:
        raise RuntimeError(f"A worker could not start: {_worker['failure']}")
    return play_match_game(_worker["pool"], _worker["factory"], game, db=_worker["db"])


def run_match(dsn: str, config: MatchConfig, *, workers: int = 1, on_game=None) -> tuple[list, dict]:
    """
    Play ``config.games`` games on ``workers`` processes. ``on_game(row, rows)``
    is called as each game finishes. Returns the rows in game order and a summary.
    """
    if workers < 1:
        raise ValueError("workers must be at least 1")
    for spec in (config.contender, config.opponent):  # fail before starting any process
        _, path = parse_bot(spec)
        if path is not None and not Path(path).is_file():
            raise FileNotFoundError(f"No model file at {path}")
    if config.games < 1:
        raise ValueError("games must be at least 1")

    rows: list[dict] = []
    started = time.perf_counter()
    # "spawn" is needed for CUDA, and is the only method on Windows
    context = mp.get_context("spawn")
    with context.Pool(min(workers, config.games), initializer=_init_worker,
                      initargs=(dsn, config)) as processes:
        # One game at a time, so a long game does not hold up a queue of others
        for row in processes.imap_unordered(_worker_game, range(config.games), chunksize=1):
            rows.append(row)
            if on_game is not None:
                on_game(row, rows)
    wall = time.perf_counter() - started
    rows.sort(key=lambda row: row["game"])
    return rows, summarize_games(rows, wall)
