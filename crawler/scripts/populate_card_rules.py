"""
populate_card_rules.py — Backfill card_rulings and dm_card_rule_links.

card_rulings:
    Fetches {Card Name}/Rulings wiki subpages via the MediaWiki API and stores
    Q&A entries. Most cards have no rulings page; those are skipped.

dm_card_rule_links:
    Links cards to formal dm_rules rows using:
      - card_keywords → dm_keywords.full_rule_ref
      - card type / subtype heuristics (special-card sections 300–822)
      - card_effects effect_type (triggered, activated, replacement, …)

Usage:
    python -m scripts.populate_card_rules --dsn $DATABASE_URL
    python main.py populate-card-rules
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

_SCRIPT_DIR = os.path.dirname(__file__)
_CRAWLER_DIR = os.path.dirname(_SCRIPT_DIR)
if _CRAWLER_DIR not in sys.path:
    sys.path.insert(0, _CRAWLER_DIR)

from scripts.api_client import make_api_session, url_to_title  # noqa: E402
from scripts.api_scraper import fetch_card_wikitexts  # noqa: E402

logger = logging.getLogger(__name__)

BATCH_SIZE = 50

# card_keywords text that does not match dm_keywords.name — direct rule_number links.
CARD_KEYWORD_TO_RULE: dict[str, str] = {
    "shield trigger": "112.3a",
    "speed attacker": "101.2",
    "evolution": "801.1",
    "psychic": "805.1",
    "draghearts": "807.1",
    "fortress": "306.1",
    "gacharange summon": "811.1",
    "gravity zero": "112.3e",
    "orega aura": "310.1",
    "d2 field": "308.1",
    "revolution change": "603.2e",
    "ninja strike": "112.3c",
    "sympathy": "110.4e",
    "madness": "110.4b",
    "mach fighter": "507.1a",
    "saver": "507.1a",
    "invasion": "701.1",
    "hunting": "701.1",
    "labyrinth": "701.1",
    "metamorph": "701.1",
    "revolution 0": "603.2e",
    "camping": "701.1",
    "veil": "701.1",
    "siegfried": "701.1",
    "super eternal": "701.1",
    "world breaker": "509.2b",
    "triple breaker": "509.2b",
    "double breaker": "509.2b",
    "power attacker": "701.1",
    "slayer": "701.1",
    "blocker": "701.12a",
}

# Longest match first — card_type substring → anchor rule_number.
CARD_TYPE_TO_RULE: list[tuple[str, str]] = [
    ("Dragheart Super Creature", "808.1"),
    ("Star Max Evolution Creature", "815.1"),
    ("Star Evolution Dragheart Super Creature", "813.1"),
    ("Star Evolution Creature", "813.1"),
    ("G-Neo Psychic Creature", "803.1"),
    ("G-Neo Dream Creature", "803.1"),
    ("G-Neo Creature", "803.1"),
    ("Neo Dream Creature", "802.1"),
    ("Neo Gacharange Creature", "802.1"),
    ("Neo Creature", "802.1"),
    ("Psychic Super Creature", "806.1"),
    ("Psychic Creature", "805.1"),
    ("Dragheart Weapon", "305.1"),
    ("Dragheart Fortress", "306.1"),
    ("Dragheart Tamaseed", "807.1"),
    ("Dragheart Creature", "807.1"),
    ("Final Forbidden Creature", "809.1"),
    ("Final Forbidden Field", "809.1"),
    ("Forbidden Impulse", "809.1"),
    ("Forbidden Creature", "809.1"),
    ("Forbidden Field", "809.1"),
    ("Duelmate Super Creature", "821.1"),
    ("Duelmate Spell", "820.1"),
    ("Duelmate Creature", "820.1"),
    ("Gacharange Creature", "811.1"),
    ("King Cell", "814.1"),
    ("King Creature", "814.1"),
    ("Ceremony of Zeron", "812.1"),
    ("Zeron Nebula", "812.1"),
    ("Zeron Creature", "812.1"),
    ("Tamaseed", "315.1"),
    ("Cross Gear", "303.1"),
    ("Orega Aura", "310.1"),
    ("D2 Field", "308.1"),
    ("DM Field", "308.1"),
    ("Dragonic Field", "308.1"),
    ("Historic Field", "308.1"),
    ("Happiness Field", "308.1"),
    ("Wonder Field", "308.1"),
    ("Lunatic Field", "308.1"),
    ("Moonless Night Field", "308.1"),
    ("Faerie Field", "308.1"),
    ("T2 Field", "308.1"),
    ("Field", "308.1"),
    ("Galaxy Castle", "304.1"),
    ("Castle", "304.1"),
    ("Rule Plus", "314.1"),
    ("Mono Artifact", "312.1"),
    ("Land", "313.1"),
    ("Duelist", "317.1"),
    ("Dream Creature", "817.1"),
    ("Super Creature", "806.1"),
    ("Spell", "302.1"),
    ("Creature", "301.1"),
]

CARD_SUBTYPE_TO_RULE: dict[str, str] = {
    "evolution": "801.1",
    "psychic": "805.1",
}

EFFECT_TYPE_TO_RULE: dict[str, str] = {
    "triggered": "603.1",
    "activated": "602.1",
    "replacement": "609.1",
    "spell": "302.1",
    "cost_mod": "112.2a",
}


@dataclass
class PopulateCounts:
    cards_scanned: int = 0
    ruling_pages_found: int = 0
    rulings_inserted: int = 0
    links_inserted: int = 0
    cards_with_links: int = 0
    cards_with_rulings: int = 0


def _strip_wiki(text: str) -> str:
    text = re.sub(r"\[\[([^|\]]+\|)?([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"'''?", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_rulings_from_wikitext(wikitext: str) -> list[str]:
    """Parse Q/A bullets from a /Rulings subpage wikitext."""
    if not wikitext:
        return []

    rulings: list[str] = []
    pending_q: str | None = None

    for line in wikitext.splitlines():
        stripped = line.strip()
        if not stripped.startswith("*"):
            continue

        depth = len(stripped) - len(stripped.lstrip("*"))
        content = _strip_wiki(stripped.lstrip("*").strip())
        if not content or content.startswith("{{"):
            continue

        upper = content.upper()
        if upper.startswith("Q:"):
            pending_q = content[2:].strip()
        elif upper.startswith("A:") and pending_q:
            answer = content[2:].strip()
            rulings.append(f"Q: {pending_q} A: {answer}")
            pending_q = None
        elif depth == 1 and len(content) > 15 and not upper.startswith(("Q:", "A:")):
            rulings.append(content)

    return rulings


def _load_cards(cur, limit: int | None) -> list[dict]:
    query = """
        SELECT id, name, source_url, card_type, card_subtype
        FROM cards
        WHERE source_url IS NOT NULL AND btrim(source_url) <> ''
        ORDER BY id
    """
    if limit:
        query += f" LIMIT {int(limit)}"
    cur.execute(query)
    return list(cur.fetchall())


def _load_keyword_rule_map(cur) -> dict[str, str]:
    cur.execute(
        """
        SELECT lower(name) AS keyword, full_rule_ref
        FROM dm_keywords
        WHERE full_rule_ref IS NOT NULL
        """
    )
    mapping = {row["keyword"]: row["full_rule_ref"] for row in cur.fetchall()}
    for keyword, rule_number in CARD_KEYWORD_TO_RULE.items():
        mapping.setdefault(keyword, rule_number)
    return mapping


def _load_valid_rules(cur) -> set[str]:
    cur.execute("SELECT rule_number FROM dm_rules")
    return {row["rule_number"] for row in cur.fetchall()}


def _rule_for_card_type(card_type: str | None) -> str | None:
    if not card_type:
        return None
    for needle, rule_number in CARD_TYPE_TO_RULE:
        if needle.lower() in card_type.lower():
            return rule_number
    return None


def _collect_rule_links_for_card(
    card: dict,
    keyword_rule_map: dict[str, str],
    card_keywords: set[str],
    effect_types: set[str],
    valid_rules: set[str],
) -> set[tuple[str, str, str]]:
    """Return (rule_number, link_type, notes) tuples for one card."""
    links: set[tuple[str, str, str]] = set()

    for keyword in card_keywords:
        rule_number = keyword_rule_map.get(keyword.lower())
        if rule_number and rule_number in valid_rules:
            links.add((rule_number, "governed_by", f"keyword:{keyword}"))

    type_rule = _rule_for_card_type(card.get("card_type"))
    if type_rule and type_rule in valid_rules:
        links.add((type_rule, "governed_by", f"card_type:{card.get('card_type')}"))

    subtype = (card.get("card_subtype") or "").lower()
    subtype_rule = CARD_SUBTYPE_TO_RULE.get(subtype)
    if subtype_rule and subtype_rule in valid_rules:
        links.add((subtype_rule, "governed_by", f"card_subtype:{subtype}"))

    for effect_type in effect_types:
        rule_number = EFFECT_TYPE_TO_RULE.get(effect_type)
        if rule_number and rule_number in valid_rules:
            links.add((rule_number, "governed_by", f"effect_type:{effect_type}"))

    return links


def populate_card_rulings(
    conn,
    session,
    cards: list[dict],
    *,
    dry_run: bool,
    counts: PopulateCounts,
) -> None:
    for i in range(0, len(cards), BATCH_SIZE):
        batch = cards[i : i + BATCH_SIZE]
        titles: list[str] = []
        title_to_card: dict[str, dict] = {}

        for card in batch:
            try:
                wiki_title = url_to_title(card["source_url"])
            except (ValueError, KeyError):
                logger.debug("Skipping card without wiki title: %s", card.get("name"))
                continue
            ruling_title = f"{wiki_title}/Rulings"
            titles.append(ruling_title)
            title_to_card[ruling_title] = card

        wikitexts = fetch_card_wikitexts(session, titles)

        with conn.cursor() as cur:
            for ruling_title, wikitext in wikitexts.items():
                card = title_to_card[ruling_title]
                rulings = parse_rulings_from_wikitext(wikitext)
                if not rulings:
                    continue

                counts.ruling_pages_found += 1
                inserted_for_card = 0

                for ruling_text in rulings:
                    if dry_run:
                        inserted_for_card += 1
                        continue

                    cur.execute(
                        """
                        INSERT INTO card_rulings (card_id, ruling_text, source)
                        SELECT %s, %s, %s
                        WHERE NOT EXISTS (
                            SELECT 1 FROM card_rulings
                            WHERE card_id = %s AND ruling_text = %s
                        )
                        """,
                        (
                            card["id"],
                            ruling_text,
                            card["source_url"],
                            card["id"],
                            ruling_text,
                        ),
                    )
                    if cur.rowcount:
                        inserted_for_card += 1
                        counts.rulings_inserted += 1

                if inserted_for_card:
                    counts.cards_with_rulings += 1

        if not dry_run:
            conn.commit()

        logger.info(
            "Rulings batch %s-%s: %s pages with rulings so far",
            i + 1,
            min(i + BATCH_SIZE, len(cards)),
            counts.ruling_pages_found,
        )


def populate_dm_card_rule_links(
    conn,
    cards: list[dict],
    *,
    dry_run: bool,
    counts: PopulateCounts,
) -> None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        keyword_rule_map = _load_keyword_rule_map(cur)
        valid_rules = _load_valid_rules(cur)

        cur.execute("SELECT card_id, keyword FROM card_keywords")
        keywords_by_card: dict[int, set[str]] = {}
        for row in cur.fetchall():
            keywords_by_card.setdefault(row["card_id"], set()).add(row["keyword"].lower())

        cur.execute(
            """
            SELECT card_id, array_agg(DISTINCT effect_type) AS effect_types
            FROM card_effects
            WHERE effect_type IS NOT NULL AND btrim(effect_type) <> ''
            GROUP BY card_id
            """
        )
        effects_by_card = {
            row["card_id"]: {et for et in row["effect_types"] if et}
            for row in cur.fetchall()
        }

        cards_with_links: set[int] = set()

        for card in cards:
            card_id = card["id"]
            links = _collect_rule_links_for_card(
                card,
                keyword_rule_map,
                keywords_by_card.get(card_id, set()),
                effects_by_card.get(card_id, set()),
                valid_rules,
            )
            if not links:
                continue

            cards_with_links.add(card_id)

            for rule_number, link_type, notes in links:
                if dry_run:
                    counts.links_inserted += 1
                    continue

                cur.execute(
                    """
                    INSERT INTO dm_card_rule_links (card_id, rule_number, link_type, notes)
                    SELECT %s, %s, %s, %s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM dm_card_rule_links
                        WHERE card_id = %s
                          AND rule_number = %s
                          AND link_type = %s
                    )
                    """,
                    (
                        card_id,
                        rule_number,
                        link_type,
                        notes,
                        card_id,
                        rule_number,
                        link_type,
                    ),
                )
                if cur.rowcount:
                    counts.links_inserted += 1

        counts.cards_with_links = len(cards_with_links)

    if not dry_run:
        conn.commit()


def populate_card_rules(
    dsn: str,
    *,
    skip_rulings: bool = False,
    skip_links: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
    clear: bool = False,
) -> PopulateCounts:
    counts = PopulateCounts()
    conn = psycopg2.connect(dsn)
    session = make_api_session()

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cards = _load_cards(cur, limit)

        counts.cards_scanned = len(cards)

        if clear and not dry_run:
            with conn.cursor() as cur:
                if not skip_rulings:
                    cur.execute("DELETE FROM card_rulings")
                if not skip_links:
                    cur.execute("DELETE FROM dm_card_rule_links")
            conn.commit()
            logger.info("Cleared target tables before populate")

        if not skip_rulings:
            populate_card_rulings(conn, session, cards, dry_run=dry_run, counts=counts)

        if not skip_links:
            populate_dm_card_rule_links(conn, cards, dry_run=dry_run, counts=counts)

    finally:
        conn.close()

    return counts


def main(argv: list[str] | None = None) -> int:
    load_dotenv(".env")
    load_dotenv("../.env")

    parser = argparse.ArgumentParser(
        description="Backfill card_rulings and dm_card_rule_links from scraped card data",
    )
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--limit", type=int, default=None, help="Process only N cards (testing)")
    parser.add_argument("--skip-rulings", action="store_true")
    parser.add_argument("--skip-links", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Count work without writing")
    parser.add_argument("--clear", action="store_true", help="Delete existing rows first")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not args.dsn:
        parser.error("--dsn required (or set DATABASE_URL)")

    counts = populate_card_rules(
        args.dsn,
        skip_rulings=args.skip_rulings,
        skip_links=args.skip_links,
        limit=args.limit,
        dry_run=args.dry_run,
        clear=args.clear,
    )

    print(
        f"Done: cards_scanned={counts.cards_scanned} "
        f"ruling_pages={counts.ruling_pages_found} "
        f"cards_with_rulings={counts.cards_with_rulings} "
        f"rulings_inserted={counts.rulings_inserted} "
        f"cards_with_links={counts.cards_with_links} "
        f"links_inserted={counts.links_inserted}"
        + (" (dry-run)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
