"""
api_client.py — Shared MediaWiki API client and DB upsert helpers.

Used by api_sets, api_set_page, and api_scraper.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional
from urllib.parse import quote

import psycopg2.extras
from curl_cffi import requests
from curl_cffi.requests.errors import RequestsError

logger = logging.getLogger(__name__)

BASE_URL = "https://duelmasters.fandom.com"
API_URL = f"{BASE_URL}/api.php"

DEFAULT_MIN_DELAY_MS = int(os.getenv("API_MIN_DELAY_MS", "100"))
DEFAULT_MAX_RETRIES = int(os.getenv("API_MAX_RETRIES", "5"))


def make_api_session() -> requests.Session:
    """Create a curl_cffi session configured for Fandom API calls."""
    session = requests.Session(impersonate="chrome110")
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "application/json",
        }
    )
    return session


_last_request_at = 0.0


def _rate_limit():
    global _last_request_at
    min_delay = DEFAULT_MIN_DELAY_MS / 1000.0
    elapsed = time.monotonic() - _last_request_at
    if elapsed < min_delay:
        time.sleep(min_delay - elapsed)
    _last_request_at = time.monotonic()


def api_get(
    session: requests.Session,
    params: dict[str, Any],
    *,
    retries: int = DEFAULT_MAX_RETRIES,
) -> dict:
    """
    Call MediaWiki api.php once and return parsed JSON.

    Retries on HTTP 429/503 and transient network errors.
    """
    query = {"format": "json", **params}
    last_error: Optional[Exception] = None

    for attempt in range(retries):
        _rate_limit()
        try:
            resp = session.get(API_URL, params=query, timeout=30)
            if resp.status_code in (429, 503):
                wait = min(60, 2 ** attempt)
                logger.warning("API HTTP %s — waiting %ss", resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                code = data["error"].get("code", "unknown")
                info = data["error"].get("info", "")
                raise RuntimeError(f"MediaWiki API error {code}: {info}")
            return data
        except (RequestsError, RuntimeError) as exc:
            last_error = exc
            wait = min(30, 2 ** attempt)
            logger.warning("API request failed (%s/%s): %s", attempt + 1, retries, exc)
            time.sleep(wait)

    raise RuntimeError(f"API request failed after {retries} retries: {last_error}")


def api_paginate(
    session: requests.Session,
    params: dict[str, Any],
    *,
    list_key: str,
    continue_keys: tuple[str, ...] = ("continue",),
) -> list[dict]:
    """
    Paginate a list= or prop= API query, collecting all items under query[list_key].

    Handles standard MediaWiki continue tokens (cmcontinue, plcontinue, apcontinue, …).
    """
    all_items: list[dict] = []
    cont_params: dict[str, str] = {}

    while True:
        data = api_get(session, {**params, **cont_params})
        query = data.get("query", {})
        items = query.get(list_key, [])
        if isinstance(items, list):
            all_items.extend(items)
        elif isinstance(items, dict):
            # prop=links returns pages dict — handled by caller
            return [query]

        if "continue" not in data:
            break
        cont = data["continue"]
        cont_params = {k: v for k, v in cont.items() if k in continue_keys or k.endswith("continue")}

    return all_items


def title_to_url(title: str) -> str:
    """Convert a wiki page title to a full card/set URL."""
    slug = title.replace(" ", "_")
    return f"{BASE_URL}/wiki/{quote(slug, safe='/_()~')}"


def url_to_title(url: str) -> str:
    """Extract wiki page title from a Fandom URL."""
    if "/wiki/" not in url:
        return url
    slug = url.split("/wiki/", 1)[1].split("#")[0].split("?")[0]
    from urllib.parse import unquote

    return unquote(slug).replace("_", " ")


def db_upsert_set(conn, set_dict: dict) -> None:
    """Upsert one row into card_sets (must run before card_urls inserts)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO card_sets (set_code, set_name, set_url, series)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (set_code) DO UPDATE SET
                set_name = EXCLUDED.set_name,
                set_url  = EXCLUDED.set_url,
                series   = EXCLUDED.series
            """,
            (
                set_dict["set_code"],
                set_dict.get("set_name", ""),
                set_dict["set_url"],
                set_dict.get("series", ""),
            ),
        )


def db_upsert_sets(conn, sets: list[dict]) -> int:
    """Bulk upsert card_sets rows. Returns count processed."""
    if not sets:
        return 0
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO card_sets (set_code, set_name, set_url, series)
            VALUES %s
            ON CONFLICT (set_code) DO UPDATE SET
                set_name = EXCLUDED.set_name,
                set_url  = EXCLUDED.set_url,
                series   = EXCLUDED.series
            """,
            [
                (s["set_code"], s.get("set_name", ""), s["set_url"], s.get("series", ""))
                for s in sets
            ],
        )
    return len(sets)


def db_upsert_card_urls(conn, cards: list[dict]) -> int:
    """
    Bulk upsert card_urls rows.

    Each dict: {url, card_name, set_code, status?}
    Status is preserved on conflict unless explicitly provided as 'pending'.
    """
    if not cards:
        return 0
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO card_urls (url, card_name, set_code, status)
            VALUES %s
            ON CONFLICT (url) DO UPDATE SET
                card_name = EXCLUDED.card_name,
                set_code  = EXCLUDED.set_code
            """,
            [
                (
                    c["url"],
                    c.get("card_name", ""),
                    c.get("set_code", ""),
                    c.get("status", "pending"),
                )
                for c in cards
            ],
        )
    return len(cards)
