"""
core/player_state.py — Complete state for one player.

Holds all zones for one player. Part of GameState.

Information visibility is NOT enforced here — PlayerState always
has full information (the engine needs it). Visibility enforcement
happens in observation.py when building the view each player sees.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from .enums import Civilization, Zone
from .cards import CardDefinition
from .zones import HandCard, ManaCard, ShieldCard, Creature, GraveyardCard


@dataclass
class PlayerState:
    """
    Complete state of one player's side of the field.

    The engine always has FULL visibility into this.
    What each player can observe is controlled separately in observation.py.

    Zone ordering conventions:
      hand:        unordered (player sees all their own hand cards)
      deck:        ordered (index 0 = top). Players know COMPOSITION not ORDER.
      mana_zone:   unordered (all visible)
      battle_zone: unordered (all visible to both players)
      shield_zone: ordered by position (5 positions, face-down)
      graveyard:   ordered (index 0 = most recently sent)
    """
    player_index:    int         # 0 or 1
    player_name:     str

    # ── Zones ─────────────────────────────────────────────────────────────────
    hand:            list[HandCard]       = field(default_factory=list)
    deck:            list[CardDefinition] = field(default_factory=list)  # engine sees order
    mana_zone:       list[ManaCard]       = field(default_factory=list)
    battle_zone:     list[Creature]       = field(default_factory=list)
    shield_zone:     list[ShieldCard]     = field(default_factory=list)
    graveyard:       list[GraveyardCard]  = field(default_factory=list)
    abyss_zone:      list[CardDefinition] = field(default_factory=list)  # banished cards

    # Castles waiting to be moved to graveyard after fortified shields leave.
    detached_castles: list[CardDefinition] = field(default_factory=list)

    # ── Extra zones (rule 407, 408) ───────────────────────────────────────────
    # Hyperspatial zone: Psychic creatures, Draghearts, Duel Mates waiting to be
    # summoned. Face-up and visible to both players (rule 407.2).
    hyperspatial_zone: list["Creature"] = field(default_factory=list)

    # Ultra GR zone: GR creatures. Face-DOWN — contents hidden until summoned
    # (rule 408.1). Both players know the count but not which cards.
    ultra_gr_zone:     list[CardDefinition] = field(default_factory=list)

    # ── Deck knowledge (public info) ──────────────────────────────────────────
    # The player knows their deck composition. This never changes after game start.
    # {card_id → count} — e.g. {1: 4, 5: 3, 12: 4}
    deck_composition: dict[int, int]      = field(default_factory=dict)

    # ── Tracking flags ────────────────────────────────────────────────────────
    has_charged_mana_this_turn: bool = False
    has_drawn_this_turn:        bool = False
    shields_broken_this_game:   int  = 0   # total shields broken against this player
    is_eliminated:              bool = False
    elimination_reason:         str  = ""  # "no_shields_direct_attack" | "deck_out" | "effect"

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def hand_count(self) -> int:
        return len(self.hand)

    @property
    def deck_size(self) -> int:
        return len(self.deck)

    @property
    def mana_count(self) -> int:
        return len(self.mana_zone)

    @property
    def shield_count(self) -> int:
        return len(self.shield_zone)

    @property
    def battle_zone_count(self) -> int:
        return len(self.battle_zone)

    @property
    def hyperspatial_count(self) -> int:
        return len(self.hyperspatial_zone)

    @property
    def ultra_gr_count(self) -> int:
        return len(self.ultra_gr_zone)

    @property
    def available_mana(self) -> int:
        """Count of untapped mana cards."""
        return sum(1 for m in self.mana_zone if not m.is_tapped)

    @property
    def tapped_mana(self) -> int:
        return sum(1 for m in self.mana_zone if m.is_tapped)

    # ── Civilization availability ──────────────────────────────────────────────

    def available_civilizations(self) -> frozenset[Civilization]:
        """
        Which civilizations are available from untapped mana.
        Used for cost-legality checks.
        """
        civs = set()
        for mana in self.mana_zone:
            if not mana.is_tapped:
                civs.update(mana.civilizations)
        return frozenset(civs)

    def mana_civilization_counts(self) -> dict[Civilization, int]:
        """Count of mana cards providing each civilization (untapped only)."""
        counts: dict[Civilization, int] = {}
        for mana in self.mana_zone:
            if not mana.is_tapped:
                for civ in mana.civilizations:
                    counts[civ] = counts.get(civ, 0) + 1
        return counts

    def all_mana_civilizations(self) -> frozenset[Civilization]:
        """All civilizations in mana zone (tapped + untapped). For display."""
        civs = set()
        for mana in self.mana_zone:
            civs.update(mana.civilizations)
        return frozenset(civs)

    # ── Zone lookups ──────────────────────────────────────────────────────────

    def find_in_hand(self, uid: str) -> Optional[HandCard]:
        return next((c for c in self.hand if c.uid == uid), None)

    def find_in_hand_by_id(self, card_id: int) -> Optional[HandCard]:
        return next((c for c in self.hand if c.id == card_id), None)

    def find_creature(self, uid: str) -> Optional[Creature]:
        return next((c for c in self.battle_zone if c.uid == uid), None)

    def find_mana(self, uid: str) -> Optional[ManaCard]:
        return next((m for m in self.mana_zone if m.uid == uid), None)

    def find_shield(self, uid: str) -> Optional[ShieldCard]:
        return next((s for s in self.shield_zone if s.uid == uid), None)

    def get_creatures_by_race(self, race: str) -> list[Creature]:
        return [c for c in self.battle_zone if race in c.races]

    def get_creatures_by_civilization(self, civ: Civilization) -> list[Creature]:
        return [c for c in self.battle_zone if civ in c.civilizations]

    def get_untapped_creatures(self) -> list[Creature]:
        return [c for c in self.battle_zone if not c.is_tapped]

    def get_attackable_creatures(self) -> list[Creature]:
        return [c for c in self.battle_zone if c.can_attack()]

    def get_blocker_creatures(self) -> list[Creature]:
        return [c for c in self.battle_zone if c.is_blocker() and not c.is_tapped]

    def get_guardman_creatures(self) -> list[Creature]:
        return [c for c in self.battle_zone if c.is_guardman() and not c.is_tapped]

    # ── Mana counting for specific civilizations ───────────────────────────────

    def count_mana_of_civilization(self, civ: Civilization) -> int:
        """Count ALL mana cards (tapped + untapped) of a civilization."""
        return sum(1 for m in self.mana_zone if civ in m.civilizations)

    def count_cards_in_zone(self, zone: Zone, civilization: Optional[Civilization] = None) -> int:
        """Generic count for effect evaluation (Power Attacker, etc.)."""
        if zone == Zone.MANA_ZONE:
            cards = self.mana_zone
            if civilization:
                return sum(1 for m in cards if civilization in m.civilizations)
            return len(cards)
        elif zone == Zone.BATTLE_ZONE:
            cards = self.battle_zone
            if civilization:
                return sum(1 for c in cards if civilization in c.civilizations)
            return len(cards)
        elif zone == Zone.HAND:
            return len(self.hand)
        elif zone == Zone.GRAVEYARD:
            return len(self.graveyard)
        elif zone == Zone.SHIELD_ZONE:
            return len(self.shield_zone)
        elif zone == Zone.DECK:
            return len(self.deck)
        elif zone == Zone.HYPERSPATIAL:
            return len(self.hyperspatial_zone)
        elif zone == Zone.ULTRA_GR:
            return len(self.ultra_gr_zone)
        elif zone == Zone.ABYSS_ZONE:
            return len(self.abyss_zone)
        return 0

    # ── Turn reset ────────────────────────────────────────────────────────────

    def reset_turn_flags(self) -> None:
        """Called at start of each turn for this player."""
        self.has_charged_mana_this_turn = False
        self.has_drawn_this_turn = False
        for creature in self.battle_zone:
            creature.has_attacked_this_turn = False
            creature.is_blocking = False
            creature.blocking_uid = None

    def untap_all(self) -> None:
        """Untap all mana and creatures at start of turn."""
        for mana in self.mana_zone:
            mana.untap()
        for creature in self.battle_zone:
            creature.untap()

    def clear_summoning_sickness(self) -> None:
        """Clear summoning sickness on every creature in the battle zone."""
        for creature in self.battle_zone:
            creature.clear_summoning_sickness()

    def expire_eot_effects(self) -> None:
        """Clean up until-end-of-turn effects on all creatures."""
        for creature in self.battle_zone:
            creature.remove_eot_power_modifiers()
            creature.clear_eot_flags()

    # ── Deck composition tracking ─────────────────────────────────────────────

    def cards_remaining_in_deck_by_id(self, include_hidden_shields: bool = False) -> dict[int, int]:
        """
        What's left in the deck by card id.
        Player can infer this: started with deck_composition, subtract
        cards seen in hand/mana/graveyard/battle zone.

        This is "known deduction" — not full hidden info, not full info.
        Hidden shields are not subtracted for player observations because
        players cannot look at their own shields.
        """
        remaining = dict(self.deck_composition)
        for card in self.hand:
            remaining[card.id] = max(0, remaining.get(card.id, 0) - 1)
        for card in self.mana_zone:
            remaining[card.id] = max(0, remaining.get(card.id, 0) - 1)
        for card in self.battle_zone:
            remaining[card.id] = max(0, remaining.get(card.id, 0) - 1)
        for card in self.graveyard:
            remaining[card.id] = max(0, remaining.get(card.id, 0) - 1)
        if include_hidden_shields:
            for card in self.shield_zone:
                remaining[card.id] = max(0, remaining.get(card.id, 0) - 1)
        return {k: v for k, v in remaining.items() if v > 0}

    # ── Eliminate ─────────────────────────────────────────────────────────────

    def eliminate(self, reason: str) -> None:
        self.is_eliminated = True
        self.elimination_reason = reason

    # ── Debug display ─────────────────────────────────────────────────────────

    def display(self, show_deck_order: bool = False) -> str:
        lines = [
            f"━━━ Player {self.player_index} ({self.player_name}) ━━━",
            f"  Shields   : {'🛡' * self.shield_count} ({self.shield_count})",
            f"  Hand      : {self.hand_count} cards",
            f"  Mana      : {self.mana_count} total, {self.available_mana} untapped",
            f"    Civs    : {', '.join(c.value for c in self.available_civilizations())}",
            f"  Battle    : {self.battle_zone_count} creatures",
        ]
        for c in self.battle_zone:
            pw = c.compute_power()
            tap = "⟳" if c.is_tapped else "○"
            sick = " (sick)" if c.has_summoning_sickness else ""
            lines.append(f"    {tap} {c.name} [{pw}]{sick}")
        lines.append(f"  Graveyard : {len(self.graveyard)} cards")
        lines.append(f"  Deck      : {self.deck_size} remaining")
        if show_deck_order:
            lines.append(f"    Top 3   : {[c.name for c in self.deck[:3]]}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"<PlayerState P{self.player_index} "
            f"hand={self.hand_count} mana={self.mana_count} "
            f"bz={self.battle_zone_count} shields={self.shield_count} "
            f"deck={self.deck_size}>"
        )
