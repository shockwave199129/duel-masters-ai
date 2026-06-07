"""
Repair legacy rows whose fields were only captured inside cards.raw_text.

Earlier wikitable parsing kept labels such as "card type:" with the trailing
colon, so core columns like cost/power/card_type were left empty. This module
extracts those values from the old raw_text dict strings and updates the DB.
"""

from __future__ import annotations

import ast
import json
import logging
from typing import Any

import psycopg2
import psycopg2.extras

from scripts.scraper import (
    _extract_cost,
    _extract_power,
    _normalize_table_key,
    _parse_civilizations,
    _split_card_type,
)

logger = logging.getLogger(__name__)


def _parse_raw_text(raw_text: str) -> dict[str, str]:
    """Parse old Python-dict raw_text and normalize keys."""
    if not raw_text or not raw_text.lstrip().startswith("{"):
        return {}
    if _load_json_raw_text(raw_text):
        return {}
    try:
        parsed = ast.literal_eval(raw_text)
    except (SyntaxError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        _normalize_table_key(str(key)): str(value)
        for key, value in parsed.items()
        if value is not None
    }


def _first(data: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return ""


def _needs_update(row: dict[str, Any], parsed: dict[str, Any]) -> bool:
    return any(
        (
            parsed.get("cost") is not None and row["cost"] is None,
            parsed.get("power") and not row["power"],
            parsed.get("card_type") and row["card_type"] in (None, "", "Unknown"),
            parsed.get("card_subtype") and not row["card_subtype"],
            parsed.get("flavor_text") and not row["flavor_text"],
        )
    )


def _extract_repair_values(data: dict[str, str]) -> dict[str, Any]:
    raw_type = _first(data, "card type", "type")
    card_type, card_subtype = _split_card_type(raw_type or "Unknown")
    flavor_text = _first(data, "flavor text", "flavor texts")
    race_text = _first(data, "race", "races")

    return {
        "cost": _extract_cost(_first(data, "cost", "mana cost")),
        "power": _extract_power(_first(data, "power")),
        "card_type": card_type if card_type != "Unknown" else None,
        "card_subtype": card_subtype,
        "flavor_text": flavor_text or None,
        "civilizations": _parse_civilizations(_first(data, "civilization", "civs")),
        "races": [race.strip() for race in race_text.split("/") if race.strip()],
    }


def _load_json_raw_text(raw_text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_text)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _raw_text_is_json(raw_text: str) -> bool:
    return bool(_load_json_raw_text(raw_text))


def _extract_abilities(data: dict[str, str]) -> list[str]:
    text = _first(data, "english text", "english_text")
    if not text:
        return []
    parts = text.split("■")
    return [
        "■ " + part.strip()
        for part in parts
        if len(part.strip()) > 5
    ]


def _extract_abilities_from_lines(raw_text: str) -> list[str]:
    return [
        line.strip()
        for line in raw_text.splitlines()
        if "■" in line and len(line.strip()) > 5
    ]


def _fields_from_ordered_lines(lines: list[str]) -> dict[str, str]:
    """Convert old [name, type, cost, power, race, abilities...] lines to fields."""
    clean_lines = [line.strip() for line in lines if line and line.strip()]
    fields: dict[str, str] = {}
    if len(clean_lines) > 1:
        fields["card type"] = clean_lines[1]
    if len(clean_lines) > 2:
        fields["mana cost"] = clean_lines[2]
    if len(clean_lines) > 3:
        fields["power"] = clean_lines[3]
    if len(clean_lines) > 4:
        fields["race"] = clean_lines[4]

    ability_lines = [line for line in clean_lines[5:] if "■" in line]
    if ability_lines:
        fields["effect text"] = "\n".join(ability_lines)
    return fields


def _raw_lines_from_json(parsed: dict[str, Any]) -> list[str]:
    fields = parsed.get("fields")
    if not isinstance(fields, dict):
        return []

    raw_lines = fields.get("raw_lines")
    if isinstance(raw_lines, list):
        return [str(line) for line in raw_lines]

    nested_fields = fields.get("fields")
    if isinstance(nested_fields, str):
        try:
            nested = ast.literal_eval(nested_fields)
        except (SyntaxError, ValueError):
            return []
        if isinstance(nested, dict) and isinstance(nested.get("raw_lines"), list):
            return [str(line) for line in nested["raw_lines"]]

    return []


def _json_needs_structuring(parsed: dict[str, Any]) -> bool:
    fields = parsed.get("fields")
    if not isinstance(fields, dict):
        return False
    return "raw_lines" in fields or "fields" in fields


def _build_json_raw_text(row: dict[str, Any], data: dict[str, str]) -> str:
    if data:
        fields = data
        abilities = _extract_abilities(data)
    else:
        raw_text = row.get("raw_text") or ""
        parsed = _load_json_raw_text(raw_text)
        raw_lines = _raw_lines_from_json(parsed)

        if raw_lines:
            fields = _fields_from_ordered_lines(raw_lines)
            abilities = [line.strip() for line in raw_lines if "■" in line]
        else:
            fields = _fields_from_ordered_lines(raw_text.splitlines())
            abilities = _extract_abilities_from_lines(raw_text)

    return json.dumps(
        {
            "name": row["name"],
            "source_url": row.get("source_url") or "",
            "fields": fields,
            "abilities": abilities,
        },
        ensure_ascii=False,
    )


def repair_cards_from_raw_text(
    dsn: str,
    dry_run: bool = True,
    delete_unrepairable: bool = False,
) -> dict[str, int]:
    """Repair existing card rows from old raw_text dict strings."""
    conn = psycopg2.connect(dsn)
    counts = {
        "scanned": 0,
        "repairable": 0,
        "updated": 0,
        "raw_text_jsonable": 0,
        "raw_text_jsonified": 0,
        "deleteable": 0,
        "deleted": 0,
    }

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    id, name, cost, power, card_type, card_subtype,
                    flavor_text, raw_text, source_url
                FROM cards
                WHERE raw_text IS NOT NULL
                """
            )
            rows = cur.fetchall()

        with conn.cursor() as cur:
            for row in rows:
                counts["scanned"] += 1
                json_raw_text = _load_json_raw_text(row["raw_text"])
                data = {} if json_raw_text else _parse_raw_text(row["raw_text"])
                fields = json_raw_text.get("fields")
                should_jsonify_raw_text = (
                    not json_raw_text
                    or (isinstance(fields, dict) and _json_needs_structuring(json_raw_text))
                )
                parsed = _extract_repair_values(data) if data else {
                    "cost": None,
                    "power": None,
                    "card_type": None,
                    "card_subtype": None,
                    "flavor_text": None,
                    "civilizations": [],
                    "races": [],
                }
                should_update_fields = bool(data) and _needs_update(row, parsed)

                if should_jsonify_raw_text:
                    counts["raw_text_jsonable"] += 1

                if not should_update_fields and not should_jsonify_raw_text:
                    continue

                if should_update_fields:
                    counts["repairable"] += 1
                if dry_run:
                    continue

                cur.execute(
                    """
                    UPDATE cards
                    SET
                        cost = COALESCE(cost, %s),
                        power = COALESCE(power, %s),
                        card_type = CASE
                            WHEN card_type IS NULL OR card_type = '' OR card_type = 'Unknown'
                            THEN COALESCE(%s, card_type)
                            ELSE card_type
                        END,
                        card_subtype = COALESCE(card_subtype, %s),
                        flavor_text = COALESCE(flavor_text, %s),
                        raw_text = CASE
                            WHEN %s THEN %s
                            ELSE raw_text
                        END,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        parsed["cost"],
                        parsed["power"],
                        parsed["card_type"],
                        parsed["card_subtype"],
                        parsed["flavor_text"],
                        should_jsonify_raw_text,
                        _build_json_raw_text(row, data),
                        row["id"],
                    ),
                )

                for civilization in parsed["civilizations"]:
                    cur.execute(
                        """
                        INSERT INTO card_civilizations (card_id, civilization)
                        VALUES (%s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (row["id"], civilization),
                    )

                for race in parsed["races"]:
                    cur.execute(
                        """
                        INSERT INTO card_races (card_id, race)
                        VALUES (%s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (row["id"], race),
                    )

                counts["updated"] += 1
                if should_jsonify_raw_text:
                    counts["raw_text_jsonified"] += 1

            if delete_unrepairable:
                cur.execute(
                    """
                    SELECT id, source_url
                    FROM cards
                    WHERE cost IS NULL
                      AND (card_type IS NULL OR card_type = '' OR card_type = 'Unknown')
                    """
                )
                bad_rows = cur.fetchall()
                counts["deleteable"] = len(bad_rows)

                if not dry_run and bad_rows:
                    bad_ids = [row[0] for row in bad_rows]
                    bad_urls = [row[1] for row in bad_rows if row[1]]
                    if bad_urls:
                        cur.execute(
                            """
                            UPDATE card_urls
                            SET status = 'pending', scraped_at = NULL, parsed_at = NULL
                            WHERE url = ANY(%s)
                            """,
                            (bad_urls,),
                        )
                    cur.execute("DELETE FROM cards WHERE id = ANY(%s)", (bad_ids,))
                    counts["deleted"] = cur.rowcount

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

        logger.info(
            (
                "Repair complete: scanned=%s repairable=%s updated=%s "
                "raw_text_jsonable=%s raw_text_jsonified=%s "
                "deleteable=%s deleted=%s dry_run=%s"
            ),
            counts["scanned"],
            counts["repairable"],
            counts["updated"],
            counts["raw_text_jsonable"],
            counts["raw_text_jsonified"],
            counts["deleteable"],
            counts["deleted"],
            dry_run,
        )
        return counts

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
