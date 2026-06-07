"""
ingest_postgres.py
Parses Duel_Masters_rules.md and inserts everything into PostgreSQL.

Usage:
    python -m rules_ingest.ingest_postgres --md path/to/Duel_Masters_rules.md
"""

import argparse
import json
from pathlib import Path

import psycopg2
import psycopg2.extras

from rules_ingest.db_config import get_database_url
from rules_ingest.parser import parse_rules_md
from rules_ingest.seed_data import GAME_PHASES, PHASE_ACTIONS, STATE_BASED_ACTIONS, KEYWORDS


# ── helpers ──────────────────────────────────────────────────────────────────

def _exec(cur, sql: str, params=None):
    cur.execute(sql, params)


def _run_schema(cur, schema_path: Path):
    cur.execute(schema_path.read_text())
    print("  ✔ Schema applied")


# ── chapters ─────────────────────────────────────────────────────────────────

def ingest_chapters(cur, chapters) -> dict[int, int]:
    """Insert chapters; return {chapter_number → db_id}."""
    sql = """
        INSERT INTO dm_chapters (number, title)
        VALUES (%s, %s)
        ON CONFLICT (number) DO UPDATE SET title = EXCLUDED.title
        RETURNING id, number
    """
    mapping = {}
    for ch in chapters:
        cur.execute(sql, (ch.number, ch.title))
        row = cur.fetchone()
        mapping[row[1]] = row[0]
    print(f"  ✔ Chapters : {len(mapping)}")
    return mapping


# ── sections ─────────────────────────────────────────────────────────────────

def ingest_sections(cur, sections, chapter_map: dict[int, int]) -> dict[int, int]:
    """Insert sections; return {section_number → db_id}."""
    sql = """
        INSERT INTO dm_sections (chapter_id, number, title)
        VALUES (%s, %s, %s)
        ON CONFLICT (chapter_id, number) DO UPDATE SET title = EXCLUDED.title
        RETURNING id, number
    """
    mapping = {}
    skipped = 0
    for sec in sections:
        chap_id = chapter_map.get(sec.chapter_number)
        if chap_id is None:
            skipped += 1
            continue
        cur.execute(sql, (chap_id, sec.number, sec.title))
        row = cur.fetchone()
        mapping[row[1]] = row[0]
    print(f"  ✔ Sections : {len(mapping)}  (skipped {skipped})")
    return mapping


# ── rules ────────────────────────────────────────────────────────────────────

def ingest_rules(cur, rules, section_map: dict[int, int]) -> dict[str, int]:
    """Insert rules; return {rule_number → db_id}."""
    sql = """
        INSERT INTO dm_rules (
            section_id, rule_number, parent_rule, depth, text,
            rule_category, applies_in_phase, applies_in_zone,
            is_state_based, is_turn_based, is_keyword_rule, priority
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (rule_number) DO UPDATE SET
            text            = EXCLUDED.text,
            rule_category   = EXCLUDED.rule_category,
            applies_in_phase= EXCLUDED.applies_in_phase,
            applies_in_zone = EXCLUDED.applies_in_zone,
            is_state_based  = EXCLUDED.is_state_based,
            is_turn_based   = EXCLUDED.is_turn_based,
            is_keyword_rule = EXCLUDED.is_keyword_rule,
            priority        = EXCLUDED.priority
        RETURNING id, rule_number
    """
    mapping: dict[str, int] = {}
    skipped = 0

    for r in rules:
        sec_id = section_map.get(r.section_number)
        if sec_id is None:
            skipped += 1
            continue

        cur.execute(sql, (
            sec_id,
            r.rule_number,
            r.parent_rule,
            r.depth,
            r.text,
            r.rule_category,
            r.applies_in_phase or [],
            r.applies_in_zone  or [],
            r.is_state_based,
            r.is_turn_based,
            r.is_keyword_rule,
            r.priority,
        ))
        row = cur.fetchone()
        mapping[row[1]] = row[0]

    print(f"  ✔ Rules    : {len(mapping)}  (skipped {skipped})")
    return mapping


# ── keywords ─────────────────────────────────────────────────────────────────

def ingest_keywords(cur, rule_map: dict[str, int]) -> dict[str, int]:
    """Insert keyword seed data; return {keyword_name → db_id}."""
    sql = """
        INSERT INTO dm_keywords (
            name, short_desc, full_rule_ref,
            overrides_summoning_sickness,
            is_triggered, is_activated, is_static, is_replacement,
            requires_declaration, usable_in_phase
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (name) DO UPDATE SET
            short_desc                   = EXCLUDED.short_desc,
            full_rule_ref                = EXCLUDED.full_rule_ref,
            overrides_summoning_sickness = EXCLUDED.overrides_summoning_sickness,
            is_triggered                 = EXCLUDED.is_triggered,
            is_activated                 = EXCLUDED.is_activated,
            is_static                    = EXCLUDED.is_static,
            is_replacement               = EXCLUDED.is_replacement,
            requires_declaration         = EXCLUDED.requires_declaration,
            usable_in_phase              = EXCLUDED.usable_in_phase
        RETURNING id, name
    """
    mapping: dict[str, int] = {}
    for kw in KEYWORDS:
        rule_ref = kw.get("full_rule_ref")
        # validate FK — set NULL if the rule doesn't exist yet
        if rule_ref and rule_ref not in rule_map:
            rule_ref = None

        cur.execute(sql, (
            kw["name"],
            kw.get("short_desc"),
            rule_ref,
            kw.get("overrides_summoning_sickness", False),
            kw.get("is_triggered",    False),
            kw.get("is_activated",    False),
            kw.get("is_static",       False),
            kw.get("is_replacement",  False),
            kw.get("requires_declaration", False),
            kw.get("usable_in_phase", []),
        ))
        row = cur.fetchone()
        mapping[row[1]] = row[0]

    print(f"  ✔ Keywords : {len(mapping)}")
    return mapping


# ── game phases ───────────────────────────────────────────────────────────────

def ingest_phases(cur) -> dict[str, int]:
    """Insert game phase seed data; return {phase_key → db_id}."""
    sql_phase = """
        INSERT INTO dm_game_phases (
            phase_key, phase_name, phase_order,
            is_optional, can_repeat, rule_ref, description
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (phase_key) DO UPDATE SET
            phase_name  = EXCLUDED.phase_name,
            phase_order = EXCLUDED.phase_order,
            is_optional = EXCLUDED.is_optional,
            can_repeat  = EXCLUDED.can_repeat,
            rule_ref    = EXCLUDED.rule_ref,
            description = EXCLUDED.description
        RETURNING id, phase_key
    """
    sql_action = """
        INSERT INTO dm_phase_actions (
            phase_id, action_order, actor, action_type,
            action_key, condition, rule_ref, description
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """

    phase_map: dict[str, int] = {}
    action_count = 0

    for ph in GAME_PHASES:
        cur.execute(sql_phase, (
            ph["phase_key"],
            ph["phase_name"],
            ph["phase_order"],
            ph["is_optional"],
            ph["can_repeat"],
            ph.get("rule_ref"),
            ph.get("description"),
        ))
        row = cur.fetchone()
        phase_map[row[1]] = row[0]

    for phase_key, actions in PHASE_ACTIONS.items():
        phase_id = phase_map.get(phase_key)
        if phase_id is None:
            continue
        for action in actions:
            cur.execute(sql_action, (
                phase_id,
                action["action_order"],
                action["actor"],
                action["action_type"],
                action["action_key"],
                action.get("condition"),
                action.get("rule_ref"),
                action.get("description"),
            ))
            action_count += 1

    print(f"  ✔ Phases   : {len(phase_map)}  /  Actions: {action_count}")
    return phase_map


# ── state-based actions ───────────────────────────────────────────────────────

def ingest_state_based_actions(cur, rule_map: dict[str, int]):
    sql = """
        INSERT INTO dm_state_based_actions (
            rule_number, action_key, description,
            condition_json, effect_json, priority
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (action_key) DO UPDATE SET
            rule_number    = EXCLUDED.rule_number,
            description    = EXCLUDED.description,
            condition_json = EXCLUDED.condition_json,
            effect_json    = EXCLUDED.effect_json,
            priority       = EXCLUDED.priority
    """
    count = 0
    skipped = 0
    for sba in STATE_BASED_ACTIONS:
        if sba["rule_number"] not in rule_map:
            skipped += 1
            continue
        cur.execute(sql, (
            sba["rule_number"],
            sba["action_key"],
            sba["description"],
            json.dumps(sba["condition_json"]),
            json.dumps(sba["effect_json"]),
            sba["priority"],
        ))
        count += 1
    print(f"  ✔ SBAs     : {count}  (skipped {skipped} — rule FK not yet present)")


# ── rule relations ────────────────────────────────────────────────────────────

def ingest_rule_relations(cur, rules, rule_map: dict[str, int]):
    """
    Auto-generate parent→child 'see_also' relations from the parsed tree.
    """
    sql = """
        INSERT INTO dm_rule_relations (rule_from, rule_to, relation, notes)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """
    count = 0
    for r in rules:
        if r.parent_rule and r.parent_rule in rule_map and r.rule_number in rule_map:
            cur.execute(sql, (r.parent_rule, r.rule_number, "see_also", "auto-generated parent→child"))
            count += 1
    print(f"  ✔ Relations: {count}  (parent→child)")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Ingest DM rules into PostgreSQL")
    ap.add_argument("--md",  required=True, help="Path to Duel_Masters_rules.md")
    ap.add_argument("--dsn", default=None, help="PostgreSQL DSN; defaults to DATABASE_URL from .env")
    ap.add_argument("--schema", default=str(Path(__file__).parent / "sql" / "schema.sql"),
                    help="Path to schema.sql")
    args = ap.parse_args()
    database_url = get_database_url(args.dsn)

    if not database_url:
        ap.error("DATABASE_URL is required in .env unless --dsn is set")

    # ── 1. Parse markdown ────────────────────────────────────────────────────
    print("\n[1/3] Parsing markdown …")
    chapters, sections, rules = parse_rules_md(args.md)
    print(f"      {len(chapters)} chapters / {len(sections)} sections / {len(rules)} rules")

    # ── 2. Connect ───────────────────────────────────────────────────────────
    print("\n[2/3] Connecting to PostgreSQL …")
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # ── 3. Insert ────────────────────────────────────────────────────────
        print("\n[3/3] Inserting data …")

        _run_schema(cur, Path(args.schema))

        chapter_map = ingest_chapters(cur, chapters)
        section_map = ingest_sections(cur, sections, chapter_map)
        rule_map    = ingest_rules(cur, rules, section_map)

        ingest_keywords(cur, rule_map)
        ingest_phases(cur)
        ingest_state_based_actions(cur, rule_map)
        ingest_rule_relations(cur, rules, rule_map)

        conn.commit()
        print("\n✅  All data committed successfully.\n")

    except Exception as exc:
        conn.rollback()
        print(f"\n❌  Error — rolled back: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
