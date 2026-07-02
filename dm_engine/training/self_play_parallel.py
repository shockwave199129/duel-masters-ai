"""Parallel self-play helpers using per-process database connections."""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from db.card_database import CardDatabase
from rules import RuleKnowledgeService
from training.self_play import SelfPlaySummary, run_self_play_games

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParallelSelfPlaySummary:
    games: int
    decisions: int
    player0_wins: int
    player1_wins: int
    no_winner_terminal: int
    unfinished: int


def _merge_summaries(parts: list[SelfPlaySummary]) -> ParallelSelfPlaySummary:
    return ParallelSelfPlaySummary(
        games=sum(part.games for part in parts),
        decisions=sum(part.decisions for part in parts),
        player0_wins=sum(part.player0_wins for part in parts),
        player1_wins=sum(part.player1_wins for part in parts),
        no_winner_terminal=sum(part.no_winner_terminal for part in parts),
        unfinished=sum(part.unfinished for part in parts),
    )


def _worker_run(payload: dict[str, Any]) -> dict[str, Any]:
    dsn = payload["dsn"]
    db = CardDatabase(dsn)
    db.load()
    rule_service = RuleKnowledgeService.from_card_database(db)
    summary = run_self_play_games(db=db, rule_service=rule_service, **payload["kwargs"])
    shard_path = Path(payload["shard_path"])
    return {"summary": summary.__dict__, "shard_path": str(shard_path)}


def _merge_shards(shard_paths: list[Path], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as out_f:
        for shard_path in shard_paths:
            if not shard_path.exists():
                continue
            with shard_path.open("r", encoding="utf-8") as shard_f:
                for line in shard_f:
                    out_f.write(line)


def run_self_play_games_parallel(
    *,
    dsn: str,
    workers: int,
    shard_dir: str | Path,
    **kwargs,
) -> ParallelSelfPlaySummary:
    if workers < 1:
        raise ValueError("workers must be at least 1")
    shard_dir = Path(shard_dir)
    shard_dir.mkdir(parents=True, exist_ok=True)
    games = int(kwargs["games"])
    base = games // workers
    remainder = games % workers
    payloads: list[dict[str, Any]] = []
    summaries: list[SelfPlaySummary] = []
    shard_paths: list[Path] = []
    with tempfile.TemporaryDirectory(dir=shard_dir) as tmpdir:
        tmpdir_path = Path(tmpdir)
        for worker_index in range(workers):
            worker_games = base + (1 if worker_index < remainder else 0)
            if worker_games == 0:
                continue
            shard_path = tmpdir_path / f"shard_{worker_index}.jsonl"
            worker_kwargs = dict(kwargs)
            worker_kwargs["games"] = worker_games
            worker_kwargs["seed_start"] = int(kwargs["seed_start"]) + worker_index * 100000
            worker_kwargs["output_path"] = shard_path
            worker_kwargs["overwrite"] = True
            payloads.append({"dsn": dsn, "kwargs": worker_kwargs, "shard_path": shard_path})
            shard_paths.append(shard_path)

        if len(payloads) == 1:
            result = _worker_run(payloads[0])
            summaries.append(SelfPlaySummary(**result["summary"]))
        else:
            with mp.Pool(processes=len(payloads)) as pool:
                for result in pool.map(_worker_run, payloads):
                    summaries.append(SelfPlaySummary(**result["summary"]))

        _merge_shards(shard_paths, Path(kwargs["output_path"]))

    return _merge_summaries(summaries)
