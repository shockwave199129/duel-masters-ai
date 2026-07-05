"""
db/card_database.py — Loads card definitions from PostgreSQL into memory.

Cards are loaded ONCE at engine startup and cached. All game state objects
reference CardDefinition instances by pointer — never copied, always shared.
This keeps memory usage low and deepcopy fast (only game-state objects are
copied, never card definitions).

Usage:
    db = CardDatabase(dsn="postgresql://user:pass@localhost/dm_db")
    db.load()                               # load all cards from DB

    defn = db.get(card_id)                  # get one card
    deck = db.resolve_deck(deck_definition) # fill in card definitions in a deck
"""

from __future__ import annotations
import json
import logging
import re
from typing import Any, Optional

import psycopg2
import psycopg2.extras

from core.enums import (
    Civilization, CardType, CardSubtype, Keyword,
    EffectType, TriggerEvent, EffectAction, Zone,
)
from core.cards import CardDefinition, CardEffect, DeckDefinition

logger = logging.getLogger(__name__)


# ── String → Enum helpers ──────────────────────────────────────────────────────

def _civ(s: str) -> Optional[Civilization]:
    mapping = {c.value.lower(): c for c in Civilization}
    return mapping.get(s.lower())

def _card_type(s: str) -> CardType:
    mapping = {c.value.lower(): c for c in CardType}
    return mapping.get(s.lower(), CardType.CREATURE)

def _card_subtype(s: Optional[str]) -> CardSubtype:
    if not s:
        return CardSubtype.NONE
    mapping = {c.value.lower(): c for c in CardSubtype}
    return mapping.get(s.lower(), CardSubtype.NONE)


def _parse_type_and_subtype(
    raw_card_type: Optional[str],
    raw_card_subtype: Optional[str],
) -> tuple[CardType, CardSubtype]:
    """
    Derive (CardType, CardSubtype) from the raw DB strings.

    The DB encodes Dragheart cards with composite card_type strings
    ("Dragheart Creature", "Dragheart Weapon", "Dragheart Fortress") and
    a NULL card_subtype, so the base helpers miss the DRAGHEART subtype.

    Psychic Super Creatures are stored with card_type="Super Creature" and
    card_subtype="Psychic", which the base helper maps to PSYCHIC instead
    of PSYCHIC_SUPER.

    Rules 805–808 require the engine to know these distinctions.
    """
    ct_raw = (raw_card_type or "Creature").strip()
    cs_raw = (raw_card_subtype or "").strip()
    ct_lower = ct_raw.lower()

    # ── Dragheart composite card_type (no card_subtype in DB) ──────────────────
    # DB values: "Dragheart Creature" | "Dragheart Weapon" | "Dragheart Fortress"
    if ct_lower == "dragheart creature":
        return CardType.CREATURE, CardSubtype.DRAGHEART
    if ct_lower == "dragheart weapon":
        return CardType.WEAPON, CardSubtype.DRAGHEART
    if ct_lower == "dragheart fortress":
        return CardType.FORTRESS, CardSubtype.DRAGHEART

    # ── Psychic Super Creature ─────────────────────────────────────────────────
    # DB: card_type="Super Creature", card_subtype="Psychic"
    if ct_lower == "super creature" and cs_raw.lower() == "psychic":
        return CardType.CREATURE, CardSubtype.PSYCHIC_SUPER

    # ── Psychic Creature (evolution variant) ──────────────────────────────────
    # DB: card_type="Psychic Creature", card_subtype="Evolution"
    # These are Psychic evolution creatures — keep CREATURE type, set PSYCHIC subtype.
    if ct_lower == "psychic creature":
        return CardType.CREATURE, CardSubtype.PSYCHIC

    # ── King Cell / King Creature (rule 814) ──────────────────────────────────
    if ct_lower == "king cell":
        return CardType.CELL, CardSubtype.NONE
    if ct_lower == "king creature":
        return CardType.CREATURE, CardSubtype.NONE

    # ── Default: use base helpers ─────────────────────────────────────────────
    return _card_type(ct_raw), _card_subtype(cs_raw or None)

def _keyword(s: str) -> Optional[Keyword]:
    mapping = {k.value.lower(): k for k in Keyword}
    return mapping.get(s.lower().replace(" ", "_"))

def _effect_type(s: str) -> EffectType:
    mapping = {e.value: e for e in EffectType}
    return mapping.get(s, EffectType.TRIGGERED)

def _trigger_event(s: str) -> TriggerEvent:
    mapping = {e.value: e for e in TriggerEvent}
    return mapping.get(s, TriggerEvent.NONE)

def _effect_action(s: str) -> EffectAction:
    mapping = {e.value: e for e in EffectAction}
    return mapping.get(s, EffectAction.NONE)

def _parse_json(val) -> dict:
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {}
    return {}


def _parse_int_prefix(value) -> Optional[int]:
    """Parse DB numeric fields that may contain wiki text like '5000+'."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    match = re.match(r"^-?\d+", text)
    if not match:
        return None
    return int(match.group(0))


# ── Zone validation ────────────────────────────────────────────────────────────

VALID_ZONE_VALUES = {z.value for z in Zone}
# "any" is a special sentinel used by the parser for CDAs (rule 110.4a) —
# it means "this ability functions in all zones". The engine does not have
# a Zone.ANY enum; we accept it here and map to the full zone set at runtime.
VALID_ZONE_VALUES.add("any")
ZONE_ALIASES = {
    "deck_zone": "deck",
    "hand_zone": "hand",
    "handzone": "hand",
    "mana_zone": "mana_zone",
    "manazone": "mana_zone",
    "battle_zone": "battle_zone",
    "battlezone": "battle_zone",
    "shield_zone": "shield_zone",
    "shieldzone": "shield_zone",
    "shield": "shield_zone",
    "graveyard_zone": "graveyard",
    "graveyardzone": "graveyard",
    "abyss_zone": "abyss_zone",
    "banished": "abyss_zone",
    "hyperspatial_zone": "hyperspatial",
    "stack": "battle_zone",
    "ultra_gr_zone": "ultra_gr",
    "ultra_graveyard": "ultra_gr",
    "tamaseed_zone": "battle_zone",
    "field_zone": "battle_zone",
    "super_gacharange": "ultra_gr",
    "super_gachaarange": "ultra_gr",
}


def _validate_zones(zone_list: list[str]) -> tuple[str, ...]:
    """Validate and normalize a list of zone strings against Zone enum.

    Unknown zones are logged and replaced with 'battle_zone' (the default).
    The special value 'any' is preserved for characteristic-defining abilities.
    """
    if not zone_list:
        return ("battle_zone",)
    validated = []
    for z in zone_list:
        z = str(z).strip()
        z = z.replace(" ", "_")
        z = z.lower()
        z = ZONE_ALIASES.get(z, z)
        if z in VALID_ZONE_VALUES:
            validated.append(z)
        else:
            logger.warning(
                "Unknown zone '%s' in active_in_zone; falling back to 'battle_zone'",
                z,
            )
            validated.append("battle_zone")
    return tuple(validated)


# ── CardDatabase ───────────────────────────────────────────────────────────────

class CardDatabase:
    """
    Loads all card definitions from PostgreSQL once at startup.
    Provides fast O(1) lookups by card_id or slug.

    Thread-safe for reads after load() completes.
    """

    def __init__(self, dsn: str):
        self._dsn   = dsn
        self._by_id:   dict[int, CardDefinition] = {}
        self._by_slug: dict[str, CardDefinition] = {}
        self._loaded = False

    # ── Load ───────────────────────────────────────────────────────────────────

    def load(self, card_ids: Optional[list[int]] = None) -> int:
        """
        Load card definitions from DB.

        Args:
            card_ids: if provided, load only these card IDs (for testing
                      with a small card pool). None = load all cards.
        Returns:
            Number of cards loaded.
        """
        conn = psycopg2.connect(self._dsn)
        try:
            effects_by_card = self._load_effects(conn, card_ids)
            keywords_by_card = self._load_keywords(conn, card_ids)
            count = self._load_cards(conn, card_ids, effects_by_card, keywords_by_card)
            self._loaded = True
            logger.info(f"CardDatabase loaded {count} cards")
            return count
        finally:
            conn.close()

    def _load_effects(
        self,
        conn,
        card_ids: Optional[list[int]]
    ) -> dict[int, list[CardEffect]]:
        """Load all card_effects rows grouped by card_id."""
        effects: dict[int, list[CardEffect]] = {}

        query = """
            SELECT
                card_id, ability_index, raw_text,
                effect_type, trigger_event, trigger_condition,
                effect_action, effect_target, effect_value,
                is_optional, is_replacement,
                active_in_phase, active_in_zone,
                parse_confidence
            FROM card_effects
        """
        params = []
        if card_ids:
            query += " WHERE card_id = ANY(%s)"
            params = [card_ids]
        query += " ORDER BY card_id, ability_index"

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            for row in cur.fetchall():
                cid = row["card_id"]
                raw_effect_type = row["effect_type"] or "triggered"
                raw_is_replacement = bool(row["is_replacement"])
                parsed_effect_type = _effect_type(raw_effect_type)

                # Cross-validate is_replacement boolean vs effect_type enum
                # The engine uses effect_type == EffectType.REPLACEMENT for dispatch.
                # The is_replacement column is an LLM training signal; they should agree.
                is_replacement_by_type = parsed_effect_type == EffectType.REPLACEMENT
                if raw_is_replacement != is_replacement_by_type:
                    logger.warning(
                        "Card %d ability %d: is_replacement=%s disagrees with effect_type=%s (%s); "
                        "engine uses effect_type for dispatch",
                        cid,
                        row["ability_index"] or 0,
                        raw_is_replacement,
                        raw_effect_type,
                        parsed_effect_type.value,
                    )

                effect = CardEffect(
                    card_id=cid,
                    ability_index=row["ability_index"] or 0,
                    raw_text=row["raw_text"] or "",
                    effect_type=parsed_effect_type,
                    trigger_event=_trigger_event(row["trigger_event"] or "none"),
                    effect_action=_effect_action(row["effect_action"] or "none"),
                    trigger_condition=_parse_json(row["trigger_condition"]),
                    effect_target=_parse_json(row["effect_target"]),
                    effect_value=_parse_json(row["effect_value"]),
                    is_optional=bool(row["is_optional"]),
                    is_replacement=raw_is_replacement,
                    active_in_phase=tuple(row["active_in_phase"] or ["any"]),
                    active_in_zone=_validate_zones(list(row["active_in_zone"] or ["battle_zone"])),
                    parse_confidence=float(row["parse_confidence"] or 0.5),
                )
                effects.setdefault(cid, []).append(effect)

        return effects

    def _load_keywords(
        self,
        conn,
        card_ids: Optional[list[int]]
    ) -> dict[int, list[Keyword]]:
        """Load card_keywords rows — pre-detected keywords per card."""
        keywords: dict[int, list[Keyword]] = {}

        query = "SELECT card_id, keyword FROM card_keywords"
        params = []
        if card_ids:
            query += " WHERE card_id = ANY(%s)"
            params = [card_ids]

        with conn.cursor() as cur:
            cur.execute(query, params)
            for card_id, kw_str in cur.fetchall():
                kw = _keyword(kw_str)
                if kw:
                    keywords.setdefault(card_id, []).append(kw)

        return keywords

    def _load_cards(
        self,
        conn,
        card_ids: Optional[list[int]],
        effects_by_card: dict[int, list[CardEffect]],
        keywords_by_card: dict[int, list[Keyword]],
    ) -> int:
        """Load cards table + civilization + race rows."""

        # Load civilizations per card
        civs_by_card: dict[int, list[Civilization]] = {}
        civ_query = "SELECT card_id, civilization FROM card_civilizations"
        civ_params = []
        if card_ids:
            civ_query += " WHERE card_id = ANY(%s)"
            civ_params = [card_ids]
        with conn.cursor() as cur:
            cur.execute(civ_query, civ_params)
            for card_id, civ_str in cur.fetchall():
                civ = _civ(civ_str)
                if civ:
                    civs_by_card.setdefault(card_id, []).append(civ)

        # Load races per card
        races_by_card: dict[int, list[str]] = {}
        race_query = "SELECT card_id, race FROM card_races"
        race_params = []
        if card_ids:
            race_query += " WHERE card_id = ANY(%s)"
            race_params = [card_ids]
        with conn.cursor() as cur:
            cur.execute(race_query, race_params)
            for card_id, race in cur.fetchall():
                if race:
                    races_by_card.setdefault(card_id, []).append(race)

        # Load evolution requirements from card_relations
        evo_by_card: dict[int, dict] = {}
        evo_query = """
            SELECT card_id, related_slug, relation_type
            FROM card_relations
            WHERE relation_type IN ('evolution_source', 'neo_evolution_source')
        """
        evo_params = []
        if card_ids:
            evo_query += " AND card_id = ANY(%s)"
            evo_params = [card_ids]
        with conn.cursor() as cur:
            cur.execute(evo_query, evo_params)
            for card_id, related_slug, rel_type in cur.fetchall():
                evo_by_card.setdefault(card_id, {"races": set(), "types": set()})
                # related_slug is a race name for evolution sources
                evo_by_card[card_id]["races"].add(related_slug)

        # Load King Cell combine relations (rule 814)
        king_target_by_card: dict[int, str] = {}
        king_requires_by_card: dict[int, set[str]] = {}
        king_query = """
            SELECT card_id, related_slug, relation_type
            FROM card_relations
            WHERE relation_type IN ('king_combine', 'king_combine_requires')
        """
        king_params: list = []
        if card_ids:
            king_query += " AND card_id = ANY(%s)"
            king_params = [card_ids]
        with conn.cursor() as cur:
            cur.execute(king_query, king_params)
            for card_id, related_slug, rel_type in cur.fetchall():
                if rel_type == "king_combine":
                    king_target_by_card[card_id] = related_slug
                elif rel_type == "king_combine_requires":
                    king_requires_by_card.setdefault(card_id, set()).add(related_slug)

        # Load God / Psychic Super link relations (post link-cards pass)
        god_links_by_card: dict[int, set[str]] = {}
        god_group_by_card: dict[int, str] = {}
        god_position_by_card: dict[int, str] = {}
        god_layout_by_card: dict[int, int] = {}
        god_glink_slots_by_card: dict[int, list[tuple[str, str]]] = {}
        god_glink_open_by_card: dict[int, set[str]] = {}
        psychic_super_by_card: dict[int, set[str]] = {}
        link_query = """
            SELECT card_id, related_slug, relation_type
            FROM card_relations
            WHERE relation_type IN (
                'god_link', 'god_link_group', 'god_link_position', 'god_link_layout',
                'god_glink', 'god_glink_open', 'psychic_super_link'
            )
        """
        link_params: list = []
        if card_ids:
            link_query += " AND card_id = ANY(%s)"
            link_params = [card_ids]
        with conn.cursor() as cur:
            cur.execute(link_query, link_params)
            for card_id, related_slug, rel_type in cur.fetchall():
                if rel_type == "god_link":
                    god_links_by_card.setdefault(card_id, set()).add(related_slug)
                elif rel_type == "god_link_group":
                    god_group_by_card[card_id] = related_slug.replace("_", " ")
                elif rel_type == "god_link_position":
                    if ":" in related_slug:
                        god_position_by_card[card_id] = related_slug.rsplit(":", 1)[-1]
                elif rel_type == "god_link_layout":
                    if ":" in related_slug:
                        try:
                            god_layout_by_card[card_id] = int(
                                related_slug.rsplit(":", 1)[-1]
                            )
                        except ValueError:
                            pass
                elif rel_type == "god_glink":
                    if ":" in related_slug:
                        side, partner = related_slug.split(":", 1)
                        god_glink_slots_by_card.setdefault(card_id, []).append(
                            (side, partner)
                        )
                elif rel_type == "god_glink_open":
                    god_glink_open_by_card.setdefault(card_id, set()).add(related_slug)
                elif rel_type == "psychic_super_link":
                    psychic_super_by_card.setdefault(card_id, set()).add(related_slug)

        # Load main cards table
        card_query = """
            SELECT id, slug, name, cost, power, card_type, card_subtype,
                   is_multiface, other_face_id
            FROM cards
        """
        card_params = []
        if card_ids:
            card_query += " WHERE id = ANY(%s)"
            card_params = [card_ids]

        count = 0
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(card_query, card_params)
            for row in cur.fetchall():
                cid = row["id"]

                if (row["card_type"] or "").strip().lower() == "unknown":
                    continue

                ctype, csubtype = _parse_type_and_subtype(
                    row["card_type"], row["card_subtype"]
                )
                defn = CardDefinition(
                    id=cid,
                    slug=row["slug"] or "",
                    name=row["name"] or f"Card {cid}",
                    cost=row["cost"] or 0,
                    power=_parse_int_prefix(row["power"]),
                    card_type=ctype,
                    card_subtype=csubtype,
                    civilizations=frozenset(civs_by_card.get(cid, [])),
                    races=frozenset(races_by_card.get(cid, [])),
                    keywords=frozenset(keywords_by_card.get(cid, [])),
                    effects=tuple(effects_by_card.get(cid, [])),
                    evolution_source_races=frozenset(
                        evo_by_card.get(cid, {}).get("races", set())
                    ),
                    evolution_source_types=frozenset(),
                    is_multiface=bool(row["is_multiface"]),
                    other_face_id=row["other_face_id"],
                    king_combine_target_slug=king_target_by_card.get(cid),
                    king_combine_required_slugs=frozenset(
                        king_requires_by_card.get(cid, set())
                    ),
                    god_link_slugs=frozenset(god_links_by_card.get(cid, set())),
                    god_link_group=god_group_by_card.get(cid),
                    god_link_position=god_position_by_card.get(cid),
                    god_link_layout_size=god_layout_by_card.get(cid),
                    god_glink_slots=tuple(god_glink_slots_by_card.get(cid, [])),
                    god_glink_open_sides=frozenset(
                        god_glink_open_by_card.get(cid, set())
                    ),
                    psychic_super_cell_slugs=frozenset(
                        psychic_super_by_card.get(cid, set())
                    ),
                )

                self._by_id[cid] = defn
                self._by_slug[defn.slug] = defn
                count += 1

        return count

    # ── Lookups ────────────────────────────────────────────────────────────────

    def get(self, card_id: int) -> Optional[CardDefinition]:
        return self._by_id.get(card_id)

    def get_by_slug(self, slug: str) -> Optional[CardDefinition]:
        return self._by_slug.get(slug)

    def require(self, card_id: int) -> CardDefinition:
        defn = self._by_id.get(card_id)
        if defn is None:
            raise KeyError(f"Card ID {card_id} not found in CardDatabase")
        return defn

    def all_cards(self) -> list[CardDefinition]:
        return list(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    def is_loaded(self) -> bool:
        return self._loaded

    # ── Deck resolution ────────────────────────────────────────────────────────

    def resolve_deck(self, deck: DeckDefinition) -> DeckDefinition:
        """
        Fill in card_definitions on a DeckDefinition.
        Must be called before initialize_game().
        """
        missing = []
        for card_id in deck.card_counts:
            defn = self._by_id.get(card_id)
            if defn is None:
                missing.append(card_id)
            else:
                deck.card_definitions[card_id] = defn

        if missing:
            raise ValueError(
                f"Deck '{deck.name}' references card IDs not in database: {missing}"
            )

        return deck

    # ── Convenience: build deck from name→count dict ───────────────────────────

    def build_deck(
        self,
        name: str,
        owner: str,
        cards: dict[str, int],   # card name or slug → count
    ) -> DeckDefinition:
        """
        Build a DeckDefinition from a {card_name: count} dict.
        Looks up cards by name or slug.
        Useful for tests and quick deck construction.
        """
        # Build slug→defn index if not already done
        name_index = {d.name.lower(): d for d in self._by_id.values()}
        slug_index  = self._by_slug

        card_counts: dict[int, int] = {}
        for card_key, count in cards.items():
            defn = (
                slug_index.get(card_key)
                or name_index.get(card_key.lower())
            )
            if defn is None:
                raise ValueError(f"Card not found: '{card_key}'")
            card_counts[defn.id] = count

        deck = DeckDefinition(
            name=name,
            owner=owner,
            card_counts=card_counts,
        )
        return self.resolve_deck(deck)

    # ── Training deck persistence ──────────────────────────────────────────────

    def upsert_training_deck(
        self,
        spec,
        *,
        source: str = "manual",
        is_active: bool = True,
    ) -> int:
        """Insert or update a training deck and return its database ID."""
        main_deck = spec.main_deck
        if not main_deck.is_valid():
            raise ValueError(f"Training deck '{main_deck.name}' is not a legal 40-card deck")

        deck_id: int
        conn = psycopg2.connect(self._dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO training_decks (
                        name, owner, source, is_active,
                        hyperspatial, ultra_gr, start_battle_zone, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, NOW())
                    ON CONFLICT (name) DO UPDATE SET
                        owner = EXCLUDED.owner,
                        source = EXCLUDED.source,
                        is_active = EXCLUDED.is_active,
                        hyperspatial = EXCLUDED.hyperspatial,
                        ultra_gr = EXCLUDED.ultra_gr,
                        start_battle_zone = EXCLUDED.start_battle_zone,
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (
                        main_deck.name,
                        main_deck.owner,
                        source,
                        is_active,
                        json.dumps(_zone_counts(spec.hyperspatial)),
                        json.dumps(_zone_counts(spec.ultra_gr)),
                        json.dumps([card.id for card in spec.start_battle_zone]),
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("Training deck upsert did not return an ID")
                deck_id = int(row[0])
                cur.execute("DELETE FROM training_deck_cards WHERE deck_id = %s", (deck_id,))
                cur.executemany(
                    """
                    INSERT INTO training_deck_cards (deck_id, card_id, count)
                    VALUES (%s, %s, %s)
                    """,
                    [
                        (deck_id, int(card_id), int(count))
                        for card_id, count in sorted(main_deck.card_counts.items())
                    ],
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return deck_id

    def list_training_deck_ids(self, *, source: str | None = None, active_only: bool = True) -> list[int]:
        """Return IDs for persisted training decks."""
        clauses: list[str] = []
        params: list[Any] = []
        if active_only:
            clauses.append("is_active = TRUE")
        if source is not None:
            clauses.append("source = %s")
            params.append(source)

        query = "SELECT id FROM training_decks"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"

        conn = psycopg2.connect(self._dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [int(row[0]) for row in cur.fetchall()]
        finally:
            conn.close()

    def load_training_deck(self, deck_id: int):
        """Load one persisted training deck as a resolved PrebuiltDeckSpec."""
        from decks.prebuilt import PrebuiltDeckSpec

        conn = psycopg2.connect(self._dsn)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, name, owner, hyperspatial, ultra_gr, start_battle_zone
                    FROM training_decks
                    WHERE id = %s
                    """,
                    (deck_id,),
                )
                deck_row = cur.fetchone()
                if deck_row is None:
                    raise KeyError(f"Training deck ID {deck_id} not found")

                cur.execute(
                    """
                    SELECT card_id, count
                    FROM training_deck_cards
                    WHERE deck_id = %s
                    ORDER BY card_id
                    """,
                    (deck_id,),
                )
                card_counts = {
                    int(row["card_id"]): int(row["count"])
                    for row in cur.fetchall()
                }
        finally:
            conn.close()

        deck = DeckDefinition(
            name=str(deck_row["name"]),
            owner=str(deck_row["owner"] or ""),
            card_counts=card_counts,
        )
        self.resolve_deck(deck)
        if not deck.is_valid():
            raise ValueError(f"Training deck '{deck.name}' is not a legal 40-card deck")

        return PrebuiltDeckSpec(
            main_deck=deck,
            hyperspatial=tuple(self.require(card_id) for card_id in _expand_zone_counts(deck_row["hyperspatial"])),
            ultra_gr=tuple(self.require(card_id) for card_id in _expand_zone_counts(deck_row["ultra_gr"])),
            start_battle_zone=tuple(self.require(card_id) for card_id in _expand_zone_list(deck_row["start_battle_zone"])),
        )

    def get_training_deck_card_ids(self, deck_id: int) -> set[int]:
        """Return every card ID referenced by a persisted training deck."""
        conn = psycopg2.connect(self._dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT card_id
                    FROM training_deck_cards
                    WHERE deck_id = %s
                    """,
                    (deck_id,),
                )
                ids = {int(row[0]) for row in cur.fetchall()}
                cur.execute(
                    """
                    SELECT hyperspatial, ultra_gr, start_battle_zone
                    FROM training_decks
                    WHERE id = %s
                    """,
                    (deck_id,),
                )
                deck_row = cur.fetchone()
                if deck_row is None:
                    raise KeyError(f"Training deck ID {deck_id} not found")
                ids.update(_expand_zone_counts(deck_row[0]))
                ids.update(_expand_zone_counts(deck_row[1]))
                ids.update(_expand_zone_list(deck_row[2]))
                return ids
        finally:
            conn.close()

    def sample_training_decks(
        self,
        rng,
        *,
        count: int = 2,
        source: str | None = None,
        allow_mirror: bool = False,
    ) -> list[tuple[int, Any]]:
        """Sample resolved training decks for self-play."""
        deck_ids = self.list_training_deck_ids(source=source, active_only=True)
        if not deck_ids:
            raise ValueError("No active training decks found in the database")
        if not allow_mirror and len(deck_ids) < count:
            raise ValueError(
                f"Need at least {count} active training decks; found {len(deck_ids)}"
            )

        selected_ids = (
            [rng.choice(deck_ids) for _ in range(count)]
            if allow_mirror
            else rng.sample(deck_ids, count)
        )
        return [(deck_id, self.load_training_deck(deck_id)) for deck_id in selected_ids]


def _zone_counts(cards) -> dict[str, int]:
    counts: dict[str, int] = {}
    for card in cards:
        card_id = str(card.id)
        counts[card_id] = counts.get(card_id, 0) + 1
    return counts


def _json_value(value):
    if isinstance(value, str):
        return json.loads(value)
    if value is None:
        return {}
    return value


def _expand_zone_counts(value) -> list[int]:
    data = _json_value(value)
    if not isinstance(data, dict):
        raise ValueError("Stored deck zone count must be a JSON object")
    result: list[int] = []
    for card_id, count in data.items():
        result.extend([int(card_id)] * int(count))
    return result


def _expand_zone_list(value) -> list[int]:
    data = _json_value(value)
    if not isinstance(data, list):
        raise ValueError("Stored start_battle_zone must be a JSON array")
    return [int(card_id) for card_id in data]
