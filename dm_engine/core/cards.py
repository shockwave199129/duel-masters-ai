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
from .enums import Civilization, CardType, CardSubtype, Keyword, EffectType, TriggerEvent, EffectAction


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

    # Resolved definitions (populated by CardDatabase.resolve_deck)
    card_definitions: dict[int, CardDefinition] = field(default_factory=dict)

    def total_cards(self) -> int:
        return sum(self.card_counts.values())

    def is_valid(self) -> bool:
        """Basic deck legality check."""
        if self.total_cards() != MAX_DECK_SIZE:
            return False
        if any(count <= 0 for count in self.card_counts.values()):
            return False
        if any(count > MAX_COPIES_PER_CARD for count in self.card_counts.values()):
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
