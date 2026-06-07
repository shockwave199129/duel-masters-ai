"""
state_manager.py — Persistent crawl state for resumable pipeline.

Tracks progress at 3 levels:
  Level 1 — set URLs discovered from the sets-list page
  Level 2 — card URLs discovered from each set page
  Level 3 — scrape + parse status of each card URL

State is stored in two places:
  - PostgreSQL  (card_sets, card_urls tables) — primary truth
  - Local JSON  (./state/crawl_state.json)    — fast checkpoint, survives DB restarts

Usage:
    sm = StateManager(dsn="postgresql://...", state_dir="./state")
    sm.load()
    sm.mark_set_done("DMRP-22")
    sm.save()
"""

from __future__ import annotations
import json
import os
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SetState:
    set_code: str
    set_name: str
    set_url: str
    series: str
    status: str = "pending"   # pending | discovered | done | error
    card_count: int = 0
    error_msg: str = ""


@dataclass
class CardUrlState:
    url: str
    card_name: str
    set_code: str
    status: str = "pending"   # pending | scraped | parsed | error
    error_msg: str = ""


@dataclass
class CrawlState:
    started_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    sets: dict[str, SetState] = field(default_factory=dict)
    # card URLs keyed by URL string
    card_urls: dict[str, CardUrlState] = field(default_factory=dict)

    # summary counts
    @property
    def total_sets(self) -> int:
        return len(self.sets)

    @property
    def done_sets(self) -> int:
        return sum(1 for s in self.sets.values() if s.status == "done")

    @property
    def total_cards(self) -> int:
        return len(self.card_urls)

    @property
    def scraped_cards(self) -> int:
        return sum(1 for c in self.card_urls.values() if c.status in ("scraped", "parsed"))

    @property
    def parsed_cards(self) -> int:
        return sum(1 for c in self.card_urls.values() if c.status == "parsed")


class StateManager:
    """
    Manages crawl state across restarts using PostgreSQL + local JSON checkpoint.
    """

    def __init__(self, dsn: str, state_dir: str = "./state"):
        self.dsn = dsn
        self.state_path = Path(state_dir) / "crawl_state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = CrawlState()
        self._conn = None

    # ── DB connection ──────────────────────────────────────────────────────────

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.dsn)
            self._conn.autocommit = False
        return self._conn

    def close(self):
        if self._conn and not self._conn.closed:
            self._conn.close()

    # ── Load ───────────────────────────────────────────────────────────────────

    def load(self):
        """Load state from PostgreSQL (primary) then overlay local JSON for status."""
        self._load_from_db()
        self._overlay_json()
        logger.info(
            f"State loaded: {self._state.total_sets} sets, "
            f"{self._state.total_cards} card URLs "
            f"({self._state.scraped_cards} scraped, {self._state.parsed_cards} parsed)"
        )

    def _load_from_db(self):
        try:
            conn = self._get_conn()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Load sets
                cur.execute("SELECT set_code, set_name, set_url, series, scraped_at, card_count FROM card_sets")
                for row in cur.fetchall():
                    code = row["set_code"]
                    self._state.sets[code] = SetState(
                        set_code=code,
                        set_name=row["set_name"] or "",
                        set_url=row["set_url"],
                        series=row["series"] or "",
                        status="done" if row.get("scraped_at") else "discovered",
                        card_count=row["card_count"] or 0,
                    )

                # Load card URLs
                cur.execute("SELECT url, card_name, set_code, status FROM card_urls")
                for row in cur.fetchall():
                    self._state.card_urls[row["url"]] = CardUrlState(
                        url=row["url"],
                        card_name=row["card_name"] or "",
                        set_code=row["set_code"] or "",
                        status=row["status"],
                    )
        except Exception as e:
            logger.warning(f"DB load failed (fresh start?): {e}")

    def _overlay_json(self):
        """Overlay checkpoint data without masking PostgreSQL card URL status."""
        if not self.state_path.exists():
            return
        try:
            with open(self.state_path) as f:
                data = json.load(f)
            has_db_sets = bool(self._state.sets)
            has_db_card_urls = bool(self._state.card_urls)
            for code, s in data.get("sets", {}).items():
                if code in self._state.sets:
                    self._state.sets[code].status = s.get("status", "pending")
                    self._state.sets[code].error_msg = s.get("error_msg", "")
                elif not has_db_sets:
                    self._state.sets[code] = SetState(**s)
            for url, c in data.get("card_urls", {}).items():
                if url in self._state.card_urls:
                    self._state.card_urls[url].error_msg = c.get("error_msg", "")
                elif not has_db_card_urls:
                    self._state.card_urls[url] = CardUrlState(**c)
        except Exception as e:
            logger.warning(f"JSON checkpoint load failed: {e}")

    # ── Save ───────────────────────────────────────────────────────────────────

    def save(self):
        """Save current state to local JSON checkpoint."""
        self._state.updated_at = _now()
        data = {
            "started_at": self._state.started_at,
            "updated_at": self._state.updated_at,
            "sets": {k: asdict(v) for k, v in self._state.sets.items()},
            "card_urls": {k: asdict(v) for k, v in self._state.card_urls.items()},
        }
        tmp = self.state_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        tmp.replace(self.state_path)

    # ── Set-level operations ───────────────────────────────────────────────────

    def add_sets(self, sets: list[dict]):
        """Bulk-insert newly discovered sets into DB + state."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                for s in sets:
                    if s["set_code"] in self._state.sets:
                        continue
                    cur.execute(
                        """
                        INSERT INTO card_sets (set_code, set_name, set_url, series)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (set_code) DO NOTHING
                        """,
                        (s["set_code"], s["set_name"], s["set_url"], s.get("series", "")),
                    )
                    self._state.sets[s["set_code"]] = SetState(
                        set_code=s["set_code"],
                        set_name=s["set_name"],
                        set_url=s["set_url"],
                        series=s.get("series", ""),
                        status="pending",
                    )
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise

    def mark_set_discovered(self, set_code: str, card_count: int = 0):
        s = self._state.sets.get(set_code)
        if s:
            s.status = "discovered"
            s.card_count = card_count
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE card_sets SET card_count=%s WHERE set_code=%s",
                    (card_count, set_code),
                )
            conn.commit()
        except Exception as e:
            conn.rollback()

    def mark_set_done(self, set_code: str):
        s = self._state.sets.get(set_code)
        if s:
            s.status = "done"
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE card_sets SET scraped_at=NOW() WHERE set_code=%s",
                    (set_code,),
                )
            conn.commit()
        except Exception as e:
            conn.rollback()

    def mark_set_error(self, set_code: str, msg: str):
        s = self._state.sets.get(set_code)
        if s:
            s.status = "error"
            s.error_msg = msg

    def pending_sets(self) -> list[SetState]:
        return [s for s in self._state.sets.values() if s.status in ("pending", "error")]

    def discovered_sets(self) -> list[SetState]:
        return [s for s in self._state.sets.values() if s.status in ("pending", "discovered", "error")]

    # ── Card URL operations ────────────────────────────────────────────────────

    def add_card_urls(self, cards: list[dict]):
        """Bulk-insert newly discovered card URLs into DB + state."""
        new = [c for c in cards if c["url"] not in self._state.card_urls]
        if not new:
            return
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO card_urls (url, card_name, set_code, status)
                    VALUES %s
                    ON CONFLICT (url) DO NOTHING
                    """,
                    [(c["url"], c.get("card_name", ""), c.get("set_code", ""), "pending") for c in new],
                )
            conn.commit()
            for c in new:
                self._state.card_urls[c["url"]] = CardUrlState(
                    url=c["url"],
                    card_name=c.get("card_name", ""),
                    set_code=c.get("set_code", ""),
                )
        except Exception as e:
            conn.rollback()
            raise

    def mark_card_scraped(self, url: str):
        self._update_card_status(url, "scraped")

    def mark_card_parsed(self, url: str):
        self._update_card_status(url, "parsed")

    def mark_card_error(self, url: str, msg: str):
        c = self._state.card_urls.get(url)
        if c:
            c.status = "error"
            c.error_msg = msg
        self._update_card_status(url, "error")

    def _update_card_status(self, url: str, status: str):
        c = self._state.card_urls.get(url)
        if c:
            c.status = status
        col = {"scraped": "scraped_at", "parsed": "parsed_at"}.get(status)
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                if col:
                    cur.execute(f"UPDATE card_urls SET status=%s, {col}=NOW() WHERE url=%s", (status, url))
                else:
                    cur.execute("UPDATE card_urls SET status=%s WHERE url=%s", (status, url))
            conn.commit()
        except Exception as e:
            conn.rollback()

    def pending_cards(self) -> list[CardUrlState]:
        return [c for c in self._state.card_urls.values() if c.status == "pending"]

    def scraped_cards(self) -> list[CardUrlState]:
        return [c for c in self._state.card_urls.values() if c.status == "scraped"]

    def is_card_done(self, url: str) -> bool:
        c = self._state.card_urls.get(url)
        return c is not None and c.status in ("scraped", "parsed")

    # ── Summary ────────────────────────────────────────────────────────────────

    def print_summary(self):
        s = self._state
        print(f"\n{'─'*50}")
        print(f"  Crawl State Summary")
        print(f"{'─'*50}")
        print(f"  Sets   : {s.done_sets}/{s.total_sets} done")
        print(f"  Cards  : {s.total_cards} discovered")
        print(f"           {s.scraped_cards} scraped")
        print(f"           {s.parsed_cards} parsed")
        print(f"  Updated: {s.updated_at}")
        print(f"{'─'*50}\n")
