"""
link_cards.py — Stage 3.5: Link multiface / God / Psychic Super cards after scrape.

Run after all cards are scraped so slug → id resolution works.

Usage:
    python main.py link-cards --use-api
    python -m scripts.link_cards --dsn $DATABASE_URL
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

_SCRIPT_DIR = os.path.dirname(__file__)
_CRAWLER_DIR = os.path.dirname(_SCRIPT_DIR)
if _CRAWLER_DIR not in sys.path:
    sys.path.insert(0, _CRAWLER_DIR)

from scripts.api_client import make_api_session, url_to_title  # noqa: E402
from scripts.api_scraper import fetch_card_wikitexts  # noqa: E402
from scripts.card_link_parser import (  # noqa: E402
    ParsedCardLinks,
    god_group_from_name,
    name_to_slug,
    parse_card_links,
)
from scripts.god_link_layout import (  # noqa: E402
    aggregate_group_layout,
    layout_rows_for_group,
    synthesize_template_glinks,
)

logger = logging.getLogger(__name__)

MANAGED_RELATION_TYPES = (
    "other_face",
    "psychic_super_link",
    "god_link",
    "god_link_group",
    "god_link_position",
    "god_link_layout",
    "god_glink",
    "god_glink_open",
)

BATCH_SIZE = 50


def _resolve_slug(cur, slug_or_name: str) -> str | None:
    """Map wiki title / slug to cards.slug in DB."""
    candidates = [
        slug_or_name,
        slug_or_name.replace(" ", "_"),
        name_to_slug(slug_or_name),
    ]
    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        cur.execute("SELECT slug FROM cards WHERE slug = %s", (cand,))
        row = cur.fetchone()
        if row:
            return row[0]
    cur.execute(
        "SELECT slug FROM cards WHERE lower(name) = lower(%s) LIMIT 1",
        (slug_or_name.replace("_", " "),),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _wiki_title_for_card(row: dict) -> str:
    if row.get("source_url"):
        return url_to_title(row["source_url"])
    return row["slug"].replace("_", " ").replace("%27", "'")


def _collect_cards(conn) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, slug, name, source_url, card_type, card_subtype
            FROM cards
            ORDER BY id
            """
        )
        return [dict(r) for r in cur.fetchall()]


def _fetch_wikitexts(session, titles: list[str]) -> dict[str, str]:
    if not titles:
        return {}
    return fetch_card_wikitexts(session, titles)


def _relation_rows(
    card_id: int,
    card_slug: str,
    links: ParsedCardLinks,
    slug_cache: dict[str, str | None],
    cur,
) -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []

    def _slug_ref(raw: str) -> str | None:
        if raw not in slug_cache:
            slug_cache[raw] = _resolve_slug(cur, raw)
        return slug_cache[raw]

    for raw_slug in links.other_face_slugs:
        resolved = _slug_ref(raw_slug)
        if resolved and resolved != card_slug:
            rows.append((card_id, resolved, "other_face"))

    for raw_slug in links.psychic_super_slugs:
        resolved = _slug_ref(raw_slug)
        if resolved and resolved != card_slug:
            rows.append((card_id, resolved, "psychic_super_link"))

    if links.god_link_group:
        group_slug = name_to_slug(links.god_link_group)
        rows.append((card_id, group_slug, "god_link_group"))
        if links.god_link_position:
            rows.append(
                (card_id, f"{group_slug}:{links.god_link_position}", "god_link_position")
            )

    for side, raw_slug in links.god_glink_slots:
        resolved = _slug_ref(raw_slug)
        if resolved and resolved != card_slug:
            rows.append((card_id, f"{side}:{resolved}", "god_glink"))

    for side in links.god_glink_open_sides:
        rows.append((card_id, side, "god_glink_open"))

    return rows


def _link_god_groups(
    cards: list[dict],
    parsed_by_id: dict[int, ParsedCardLinks],
) -> list[tuple[int, str, str]]:
    """
    Infer per-group layout (2/3/4/6) and fill directional god_glink rows.

    Template Left/Center/Right gods (no inline engtext) get synthesized slots
    for 2- and 3-position layouts. Remaining template groups fall back to pairwise
    god_link relations.
    """
    groups: dict[str, list[tuple[int, str, str, ParsedCardLinks]]] = defaultdict(list)

    for card in cards:
        links = parsed_by_id.get(card["id"])
        if not links:
            continue
        if not links.god_link_group:
            group, pos = god_group_from_name(card["name"])
            if group:
                links.god_link_group = group
                if pos and not links.god_link_position:
                    links.god_link_position = pos
        if links.god_link_group:
            groups[links.god_link_group].append(
                (card["id"], card["slug"], card["name"], links)
            )

    rows: list[tuple[int, str, str]] = []
    for group, members in groups.items():
        member_ids = [cid for cid, _slug, _name, _links in members]
        layout = aggregate_group_layout(
            [(cid, links) for cid, _slug, _name, links in members]
        )
        rows.extend(layout_rows_for_group(group, member_ids, layout))

        synth = synthesize_template_glinks(group, members, layout)
        if synth:
            rows.extend(synth)
            continue

        has_inline = any(
            links.god_glink_slots or links.god_glink_open_sides
            for _cid, _slug, _name, links in members
        )
        if has_inline:
            continue

        if len(members) < 2:
            continue
        for cid, slug, _name, _links in members:
            for _ocid, oslug, _oname, _olinks in members:
                if cid != _ocid:
                    rows.append((cid, oslug, "god_link"))

    return rows


def _upsert_relations(conn, rows: list[tuple[int, str, str]], dry_run: bool) -> int:
    if not rows:
        return 0
    if dry_run:
        logger.info("[dry-run] Would upsert %s card_relations rows", len(rows))
        return len(rows)

    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM card_relations WHERE relation_type = ANY(%s)",
            (list(MANAGED_RELATION_TYPES),),
        )
        psycopg2.extras.execute_batch(
            cur,
            """
            INSERT INTO card_relations (card_id, related_slug, relation_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (card_id, related_slug, relation_type) DO NOTHING
            """,
            rows,
            page_size=500,
        )
    conn.commit()
    return len(rows)


def _resolve_other_face_ids(conn, dry_run: bool) -> int:
    """Set cards.other_face_id from other_face relations (bidirectional)."""
    sql_pairs = """
        SELECT r.card_id, r.related_slug, c1.slug AS from_slug
        FROM card_relations r
        JOIN cards c1 ON c1.id = r.card_id
        WHERE r.relation_type = 'other_face'
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql_pairs)
        pairs = cur.fetchall()

    updates: list[tuple[int, int]] = []
    with conn.cursor() as cur:
        for row in pairs:
            cur.execute("SELECT id FROM cards WHERE slug = %s", (row["related_slug"],))
            target = cur.fetchone()
            if not target:
                continue
            updates.append((row["card_id"], target[0]))
            updates.append((target[0], row["card_id"]))

    if not updates:
        return 0

    # Deduplicate — keep last write per card_id
    merged: dict[int, int] = {}
    for card_id, other_id in updates:
        if card_id != other_id:
            merged[card_id] = other_id

    if dry_run:
        logger.info("[dry-run] Would set other_face_id on %s cards", len(merged))
        return len(merged)

    with conn.cursor() as cur:
        for card_id, other_id in merged.items():
            cur.execute(
                """
                UPDATE cards
                SET other_face_id = %s,
                    is_multiface = TRUE,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (other_id, card_id),
            )
    conn.commit()
    return len(merged)


def _mark_multiface_from_links(conn, dry_run: bool) -> int:
    """Mark is_multiface for God / Psychic Super cards even without other_face_id."""
    sql = """
        UPDATE cards c
        SET is_multiface = TRUE, updated_at = NOW()
        WHERE c.is_multiface IS NOT TRUE
          AND (
            EXISTS (
                SELECT 1 FROM card_relations r
                WHERE r.card_id = c.id
                  AND r.relation_type IN (
                      'psychic_super_link', 'god_link', 'god_link_group'
                  )
            )
            OR c.other_face_id IS NOT NULL
            OR c.card_subtype = 'Psychic'
            OR c.card_type ILIKE 'Dragheart%%'
          )
    """
    if dry_run:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM cards c
                WHERE c.is_multiface IS NOT TRUE
                  AND (
                    EXISTS (
                        SELECT 1 FROM card_relations r
                        WHERE r.card_id = c.id
                          AND r.relation_type IN (
                              'psychic_super_link', 'god_link', 'god_link_group'
                          )
                    )
                    OR c.other_face_id IS NOT NULL
                    OR c.card_subtype = 'Psychic'
                    OR c.card_type ILIKE 'Dragheart%%'
                  )
                """
            )
            count = cur.fetchone()[0]
        logger.info("[dry-run] Would mark is_multiface on %s cards", count)
        return count

    with conn.cursor() as cur:
        cur.execute(sql)
        count = cur.rowcount
    conn.commit()
    return count


def run_link_pass(
    dsn: str,
    *,
    dry_run: bool = False,
    use_api: bool = True,
    limit: int | None = None,
) -> dict[str, int]:
    """
    Fetch wikitext for scraped cards, extract links, write card_relations,
    resolve other_face_id, and set is_multiface.
    """
    conn = psycopg2.connect(dsn)
    session = make_api_session() if use_api else None
    stats = {
        "cards_scanned": 0,
        "relations_written": 0,
        "other_face_linked": 0,
        "multiface_marked": 0,
        "parse_hints": 0,
    }

    try:
        cards = _collect_cards(conn)
        if limit:
            cards = cards[:limit]

        parsed_by_id: dict[int, ParsedCardLinks] = {}
        slug_cache: dict[str, str | None] = {}
        relation_rows: list[tuple[int, str, str]] = []

        for i in range(0, len(cards), BATCH_SIZE):
            batch = cards[i : i + BATCH_SIZE]
            titles = [_wiki_title_for_card(c) for c in batch]
            wikitexts = _fetch_wikitexts(session, titles) if session else {}

            with conn.cursor() as cur:
                for card, title in zip(batch, titles):
                    wt = wikitexts.get(title, "")
                    links = parse_card_links(wt, card["name"])
                    parsed_by_id[card["id"]] = links
                    if links.is_multiface_hint or links.god_link_group:
                        stats["parse_hints"] += 1
                    relation_rows.extend(
                        _relation_rows(
                            card["id"],
                            card["slug"],
                            links,
                            slug_cache,
                            cur,
                        )
                    )
            stats["cards_scanned"] += len(batch)
            logger.info(
                "Parsed links for %s/%s cards",
                min(i + BATCH_SIZE, len(cards)),
                len(cards),
            )

        relation_rows.extend(_link_god_groups(cards, parsed_by_id))
        stats["relations_written"] = _upsert_relations(conn, relation_rows, dry_run)
        stats["other_face_linked"] = _resolve_other_face_ids(conn, dry_run)
        stats["multiface_marked"] = _mark_multiface_from_links(conn, dry_run)

    finally:
        conn.close()

    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv(".env")
    load_dotenv("../.env")

    parser = argparse.ArgumentParser(description="Link multiface / God cards after scrape")
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-api", action="store_true", help="Skip wikitext fetch (no-op)")
    args = parser.parse_args()

    if not args.dsn:
        logger.error("DATABASE_URL / --dsn required")
        return 1

    stats = run_link_pass(
        args.dsn,
        dry_run=args.dry_run,
        use_api=not args.no_api,
        limit=args.limit,
    )
    logger.info(
        "Link pass done: scanned=%s relations=%s other_face=%s multiface=%s hints=%s",
        stats["cards_scanned"],
        stats["relations_written"],
        stats["other_face_linked"],
        stats["multiface_marked"],
        stats["parse_hints"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
