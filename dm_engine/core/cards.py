"""
core/cards.py — Card data structures.

These are STATIC — they represent the card as printed.
They never change during a game. All game-state changes
(tapped, power modified, etc.) live in the zone objects
in state.py, NOT here.

CardDefinition  — immutable card data loaded from DB once at startup
CardEffect      — one parsed ability row from card_effects table
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Any
from .enums import Civilization, CardType, CardSubtype, Keyword, EffectType, TriggerEvent, EffectAction, CDAFormulaType


# ── Effect Row ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CardEffect:
    """
    One row from the card_effects table.
    Frozen (immutable) — these are parsed once from DB and never change.
    """
    card_id:           int
    ability_index:     int           # order of ■ on card (0-based)
    raw_text:          str           # original ■ bullet text

    effect_type:       EffectType
    trigger_event:     TriggerEvent
    effect_action:     EffectAction

    # JSON blobs from DB — stored as dicts
    trigger_condition: dict          # e.g. {"subject": "self", "min_power": 3000}
    effect_target:     dict          # e.g. {"type": "creature", "zone": "battle_zone", "count": 1}
    effect_value:      dict          # e.g. {"amount": 2000} or {"per_card_in": "mana_zone"}

    is_optional:       bool          # player may choose not to use
    is_replacement:    bool          # "instead of X, Y happens"

    active_in_phase:   tuple[str, ...]    # which phases this can fire
    active_in_zone:    tuple[str, ...]    # which zones the source must be in

    parse_confidence:  float         # 0.0–1.0, low = may need RAG fallback

    def is_keyword(self) -> bool:
        return self.effect_type == EffectType.KEYWORD

    def is_triggered(self) -> bool:
        return self.effect_type == EffectType.TRIGGERED

    def is_static(self) -> bool:
        return self.effect_type == EffectType.STATIC

    def is_activated(self) -> bool:
        return self.effect_type == EffectType.ACTIVATED

    def is_replacement_effect(self) -> bool:
        return self.effect_type == EffectType.REPLACEMENT

    def needs_rag_fallback(self) -> bool:
        return self.parse_confidence < 0.70


# ── Card Definition ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CardDefinition:
    """
    Complete static definition of a card as loaded from PostgreSQL.
    One instance per unique card. Shared (by reference) across all game states —
    never copied, never mutated.

    Loaded once at engine startup via CardDatabase.
    """
    id:            int
    slug:          str               # wiki slug, unique key
    name:          str
    cost:          int               # mana cost to play
    power:         Optional[int]     # None for spells
    card_type:     CardType
    card_subtype:  CardSubtype

    civilizations: frozenset[Civilization]   # multi-civ cards have >1
    races:         frozenset[str]            # e.g. {"Dragon", "Armored Dragon"}

    keywords:      frozenset[Keyword]        # detected keywords
    effects:       tuple[CardEffect, ...]    # parsed abilities, in order

    # Evolution requirements (if card_subtype is an evolution type)
    evolution_source_races: frozenset[str]   # what races it can evolve from
    evolution_source_types: frozenset[CardType]

    is_multiface:  bool              # twin pact or similar

    # Characteristic-Defining Ability (CDA) for dynamic power computation
    cda_formula_type: CDAFormulaType = CDAFormulaType.NONE
    cda_multiplier: int = 0               # for MULT formulas (e.g. × 1000)
    cda_fixed_value: int = 0              # for FIXED formula
    cda_zone: str = ""                    # zone key for per-card counting (e.g. "hand", "mana_zone")
    cda_filter_civ: Optional[Civilization] = None  # optional civilization filter for counting

    # Double-faced cards (Psychic, Dragheart, Forbidden, Zerom, etc.)
    # Store the ID of the other face; resolved to full CardDefinition at load time (rule 805.1, 807.1)
    other_face_id: Optional[int] = None

    # King Cell combine (rule 814) — from card_relations
    king_combine_target_slug: Optional[str] = None       # cell → combined creature slug
    king_combine_required_slugs: frozenset[str] = frozenset()  # creature → required cell slugs

    # God / Psychic Super links (rule 804 / 806) — from card_relations after link pass
    god_link_slugs: frozenset[str] = frozenset()
    god_link_group: Optional[str] = None
    god_link_position: Optional[str] = None   # left | center | right (card's own slot)
    god_link_layout_size: Optional[int] = None  # 2 | 3 | 4 | 6 for the god set
    god_glink_slots: tuple[tuple[str, str], ...] = ()  # (side, partner_slug)
    god_glink_open_sides: frozenset[str] = frozenset()
    psychic_super_cell_slugs: frozenset[str] = frozenset()

    def is_creature(self) -> bool:
        return self.card_type == CardType.CREATURE

    def is_spell(self) -> bool:
        return self.card_type == CardType.SPELL

    def is_evolution(self) -> bool:
        return self.card_subtype in (
            CardSubtype.EVOLUTION,
            CardSubtype.NEO_EVOLUTION,
            CardSubtype.SUPER_EVOLUTION,
            CardSubtype.STAR_MAX,
        )

    def is_king_cell(self) -> bool:
        """Rule 814.1: King Cell — cannot be executed alone."""
        return self.card_type == CardType.CELL and self.king_combine_target_slug is not None

    def is_king_creature(self) -> bool:
        """King Creature summoned only by combining King Cells (rule 814.1)."""
        return bool(self.king_combine_required_slugs)

    def effective_cost(self) -> int:
        """Rule 814.1a: King Cell cost is 0 when referenced."""
        if self.is_king_cell():
            return 0
        return self.cost

    def has_keyword(self, kw: Keyword) -> bool:
        return kw in self.keywords

    def has_shield_trigger(self) -> bool:
        return self.has_keyword(Keyword.SHIELD_TRIGGER)

    def has_speed_attacker(self) -> bool:
        return self.has_keyword(Keyword.SPEED_ATTACKER)

    def has_double_breaker(self) -> bool:
        return self.has_keyword(Keyword.DOUBLE_BREAKER)

    def has_triple_breaker(self) -> bool:
        return self.has_keyword(Keyword.TRIPLE_BREAKER)

    def has_world_breaker(self) -> bool:
        return self.has_keyword(Keyword.WORLD_BREAKER)

    def shields_broken(self) -> int:
        """How many shields this breaks when attacking unblocked."""
        if self.has_world_breaker():
            return 999   # engine handles "all shields"
        if self.has_triple_breaker():
            return 3
        if self.has_double_breaker():
            return 2
        return 1

    def get_effects_by_trigger(self, event: TriggerEvent) -> list[CardEffect]:
        return [e for e in self.effects if e.trigger_event == event]

    def get_static_effects(self) -> list[CardEffect]:
        return [e for e in self.effects if e.is_static()]

    def get_activated_effects(self) -> list[CardEffect]:
        return [e for e in self.effects if e.is_activated()]

    def get_cost_modifiers(self) -> list[CardEffect]:
        return [e for e in self.effects if e.effect_type == EffectType.COST_MOD]

    def __repr__(self) -> str:
        cost_str  = f"({self.cost})"
        power_str = f"/{self.power}" if self.power else ""
        civs = "/".join(c.value[0] for c in sorted(self.civilizations, key=lambda c: c.value))
        return f"<Card {self.name!r} {cost_str}{power_str} [{civs}]>"

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, CardDefinition):
            return self.id == other.id
        return NotImplemented


# ── Deck Definition ───────────────────────────────────────────────────────────

@dataclass
class DeckDefinition:
    """
    A player's deck as submitted before the game starts.
    This is the KNOWN composition — the player always knows what cards
    are in their deck, just not in what order after shuffling.
    """
    name:  str
    owner: str   # player name / identifier

    # card_id → count (e.g. {1: 4, 2: 3, 5: 4, ...})
    card_counts: dict[int, int]

    # Psychic/Dragheart cards placed in Hyperspatial Zone at game start (rule 805.4, 807.4)
    # card_id → count (e.g. {100: 1, 101: 2, ...})
    # Max 8 total cards (rule 407.1 / MAX_HYPERSPATIAL)
    hyperspatial_counts: dict[int, int] = field(default_factory=dict)

    # Resolved definitions (populated by CardDatabase.resolve_deck)
    card_definitions: dict[int, CardDefinition] = field(default_factory=dict)

    def total_cards(self) -> int:
        return sum(self.card_counts.values())

    def is_valid(self) -> bool:
        """Basic deck legality check (rule 100, rule 407.1)."""
        # Main deck must be exactly 40 cards (rule 100.1)
        if self.total_cards() != MAX_DECK_SIZE:
            return False
        if any(count <= 0 for count in self.card_counts.values()):
            return False
        if any(count > MAX_COPIES_PER_CARD for count in self.card_counts.values()):
            return False

        # Hyperspatial zone max 8 cards (rule 407.1 / MAX_HYPERSPATIAL)
        hyperspatial_total = sum(self.hyperspatial_counts.values())
        if hyperspatial_total > 8:
            return False
        if any(count <= 0 for count in self.hyperspatial_counts.values()):
            return False
        if any(count > MAX_COPIES_PER_CARD for count in self.hyperspatial_counts.values()):
            return False

        # Hyperspatial cards must be Psychic or Dragheart (rule 407)
        from .enums import CardSubtype
        for card_id in self.hyperspatial_counts.keys():
            defn = self.card_definitions.get(card_id)
            if defn is not None:
                allowed_subtypes = (CardSubtype.PSYCHIC, CardSubtype.PSYCHIC_SUPER, CardSubtype.DRAGHEART)
                if defn.card_subtype not in allowed_subtypes:
                    return False

        return True

    def all_card_ids(self) -> list[int]:
        """Expand to a full list of card IDs (with duplicates)."""
        result = []
        for card_id, count in self.card_counts.items():
            result.extend([card_id] * count)
        return result

    def civilizations_present(self) -> frozenset[Civilization]:
        civs = set()
        for card_id, defn in self.card_definitions.items():
            civs.update(defn.civilizations)
        return frozenset(civs)

    def summary(self) -> str:
        lines = [f"Deck: {self.name} ({self.total_cards()} cards)"]
        for card_id, count in sorted(self.card_counts.items()):
            name = self.card_definitions[card_id].name if card_id in self.card_definitions else f"ID:{card_id}"
            lines.append(f"  {count}x {name}")
        return "\n".join(lines)


# ── Constants imported for convenience ────────────────────────────────────────
from .enums import MAX_DECK_SIZE, MAX_COPIES_PER_CARD
