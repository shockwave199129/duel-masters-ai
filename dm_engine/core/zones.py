"""
core/zones.py — Zone objects that hold cards during a game.

These are the STATEFUL wrappers around CardDefinitions.
A CardDefinition never changes. A zone object tracks everything
that changes during play: tapped status, power modifications,
which shields are revealed, etc.

Key design: every zone object is part of GameState and gets
deepcopied when MCTS branches. Keep them lean.

Objects defined here:
  ManaCard      — a card in the mana zone (tracks tapped state)
  ShieldCard    — a card in the shield zone (tracks revealed state)
  Creature      — a card in the battle zone (tracks all in-play state)
  HandCard      — thin wrapper (mostly just the definition + instance_id)
  GraveyardCard — card + info about how it got there
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from uuid import uuid4

from .enums import Civilization, Keyword, Zone
from .cards import CardDefinition


def _new_uid() -> str:
    """Unique instance ID — every card copy on the field has one."""
    return str(uuid4())[:8]


# ── Hand Card ─────────────────────────────────────────────────────────────────

@dataclass
class HandCard:
    """
    A card in a player's hand.
    Very thin — hand cards have no in-game state beyond existing.
    The uid lets the engine uniquely reference this specific copy
    even if the player has 2 copies of the same card.
    """
    definition: CardDefinition
    uid:        str = field(default_factory=_new_uid)

    @property
    def id(self) -> int:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def cost(self) -> int:
        return self.definition.cost

    @property
    def civilizations(self) -> frozenset[Civilization]:
        return self.definition.civilizations

    def __repr__(self) -> str:
        return f"<Hand:{self.definition.name}[{self.uid}]>"


# ── Mana Card ─────────────────────────────────────────────────────────────────

@dataclass
class ManaCard:
    """
    A card in the mana zone.
    Tracks whether it's tapped (used this turn).
    The card is face-up — opponent can see civilization and card name.

    Rule 405.1: Multi-colored cards are placed TAPPED when charged to mana.
    Use ManaCard.from_charge(definition) to create with correct initial tap state.
    """
    definition: CardDefinition
    uid:        str  = field(default_factory=_new_uid)
    is_tapped:  bool = False

    @classmethod
    def from_charge(cls, definition: "CardDefinition") -> "ManaCard":
        """
        Create a ManaCard as if just charged from hand.
        Rule 405.1: multi-colored cards enter mana zone tapped.
        """
        is_multi = len(definition.civilizations) > 1
        return cls(definition=definition, is_tapped=is_multi)

    @property
    def id(self) -> int:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def civilizations(self) -> frozenset[Civilization]:
        return self.definition.civilizations

    def tap(self) -> None:
        self.is_tapped = True

    def untap(self) -> None:
        self.is_tapped = False

    def provides_civilization(self, civ: Civilization) -> bool:
        return civ in self.definition.civilizations

    def __repr__(self) -> str:
        tapped = "⟳" if self.is_tapped else "○"
        civs = "/".join(c.value[0] for c in self.definition.civilizations)
        return f"<Mana:{self.definition.name}[{civs}]{tapped}>"


# ── Shield Card ───────────────────────────────────────────────────────────────

@dataclass
class ShieldCard:
    """
    A card in the shield zone.

    VISIBILITY RULES (critical for information hiding):
    - is_revealed = False → neither player knows which card it is
      (though the owner knows it's IN their deck — deck composition is known)
    - is_revealed = True  → both players see it (after being broken)
    - The ENGINE always knows (needed for Shield Trigger detection)
    - The OBSERVATION for the opponent's shields is always hidden
    - The OBSERVATION for own shields is also hidden (you don't see your own shields)
    """
    definition:  CardDefinition
    uid:         str  = field(default_factory=_new_uid)
    is_revealed: bool = False    # True only during the break resolution window
    fortified_castles: list[CardDefinition] = field(default_factory=list)

    @property
    def id(self) -> int:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    def has_shield_trigger(self) -> bool:
        return self.definition.has_shield_trigger()

    def reveal(self) -> None:
        """Called when shield is broken — temporarily visible."""
        self.is_revealed = True

    def conceal(self) -> None:
        """Called if shield is returned face-down (some effects)."""
        self.is_revealed = False

    def __repr__(self) -> str:
        if self.is_revealed:
            return f"<Shield:{self.definition.name}[REVEALED]>"
        return f"<Shield:???[{self.uid}]>"


# ── Power Modifier ────────────────────────────────────────────────────────────

@dataclass
class PowerModifier:
    """
    A single power modification on a creature.
    Tracks source so it can be removed when source leaves play,
    and duration so it expires at the right time.
    """
    source_uid:    str            # uid of the card that granted this
    amount:        int            # positive = buff, negative = debuff
    duration:      str            # "permanent" | "until_end_of_turn" | "while_in_play"
    is_per_card:   bool = False   # True for "Power Attacker +1000 per fire card"
    per_card_zone: Optional[str] = None   # zone to count (e.g. "mana_zone")
    per_card_civ:  Optional[Civilization] = None  # civilization filter

    def __repr__(self) -> str:
        sign = "+" if self.amount >= 0 else ""
        return f"<PowerMod:{sign}{self.amount} [{self.duration}] from:{self.source_uid}>"


# ── Creature (Battle Zone Card) ───────────────────────────────────────────────

@dataclass
class Creature:
    """
    A creature card in the battle zone.
    This is the most complex zone object — tracks all in-play state.

    IMPORTANT: current_power is NOT stored here. It is always computed
    fresh from base_power + active modifiers. This prevents stale values.
    """
    definition:          CardDefinition
    uid:                 str  = field(default_factory=_new_uid)

    # Tap state
    is_tapped:           bool = False

    # Summoning sickness — can't attack until next turn (unless Speed Attacker)
    has_summoning_sickness: bool = True
    entered_turn:        int  = 0     # which turn it entered play

    # Power modifications active on this creature
    power_modifiers:     list[PowerModifier] = field(default_factory=list)

    # Evolution stack — cards underneath this creature
    # Index 0 = directly underneath, last = bottom of stack
    evolution_base:      list[CardDefinition] = field(default_factory=list)

    # Attached cards (cross gear, aura effects)
    attached_cards:      list[CardDefinition] = field(default_factory=list)

    # Temporary boolean flags set by effects
    # e.g. "cannot_attack", "cannot_be_blocked", "cannot_be_destroyed"
    temp_flags:          dict[str, bool] = field(default_factory=dict)

    # Tracks whether this creature attacked this turn (for once-per-turn checks)
    has_attacked_this_turn: bool = False

    # For "on_block" tracking
    is_blocking:         bool = False
    blocking_uid:        Optional[str] = None   # uid of creature it's blocking

    # Hyper Mode (rule 816)
    hyper_mode_released: bool = False

    # Sealed state (rule 116.2) — creature with seal is "ignored"
    # ignored = cannot attack/block, no abilities, cannot be chosen, doesn't tap/untap
    seals:               list = field(default_factory=list)  # list[CardDefinition] face-down

    @property
    def is_ignored(self) -> bool:
        """Rule 116.2: a creature with any seal attached is ignored."""
        return len(self.seals) > 0

    # God linking (rule 804) — component cards of a linked God
    linked_cards:        list = field(default_factory=list)  # list[CardDefinition]

    # Controller — usually the owner but can change with some effects
    controller:          int = 0   # player index (0 or 1)
    owner:               int = 0   # who owns the card (for "return to owner's hand")

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def id(self) -> int:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def base_power(self) -> int:
        return self.definition.power or 0

    @property
    def civilizations(self) -> frozenset[Civilization]:
        return self.definition.civilizations

    @property
    def races(self) -> frozenset[str]:
        """Effective races — includes races from evolution base if relevant."""
        return self.definition.races

    def compute_power(self, game_state_ref=None) -> int:
        """
        Always call this to get current power. Never cache.
        game_state_ref needed for per-card modifiers (Power Attacker).
        """
        total = self.base_power
        for mod in self.power_modifiers:
            if mod.is_per_card and game_state_ref is not None:
                count = game_state_ref.count_cards_in_zone(
                    player=self.controller,
                    zone=mod.per_card_zone,
                    civilization=mod.per_card_civ
                )
                total += mod.amount * count
            else:
                total += mod.amount
        return total

    # ── Keyword checks (delegate to definition + temp flags) ──────────────────

    def has_keyword(self, kw: Keyword) -> bool:
        return self.definition.has_keyword(kw) or self.temp_flags.get(kw.value, False)

    def can_attack(self) -> bool:
        if self.is_ignored:          # rule 116.2: ignored creatures cannot attack
            return False
        if self.temp_flags.get("cannot_attack", False):
            return False
        if self.is_tapped:
            return False
        if self.has_summoning_sickness and not self.has_keyword(Keyword.SPEED_ATTACKER):
            return False
        return True

    def can_attack_players(self) -> bool:
        return not self.temp_flags.get("cannot_attack_players", False)

    def can_be_blocked(self) -> bool:
        return not (
            self.has_keyword(Keyword.CANNOT_BE_BLOCKED)
            or self.temp_flags.get("cannot_be_blocked", False)
        )

    def can_be_destroyed(self) -> bool:
        return not self.temp_flags.get("cannot_be_destroyed", False)

    def is_blocker(self) -> bool:
        """Rule 116.2: ignored creatures cannot block."""
        return self.has_keyword(Keyword.BLOCKER) and not self.is_ignored

    def is_guardman(self) -> bool:
        return self.has_keyword(Keyword.GUARDMAN) and not self.is_ignored

    def shields_broken_on_attack(self) -> int:
        return self.definition.shields_broken()

    def set_flag(self, flag: str, value: bool = True) -> None:
        self.temp_flags[flag] = value

    def clear_flag(self, flag: str) -> None:
        self.temp_flags.pop(flag, None)

    def clear_eot_flags(self) -> None:
        """Clear all temporary flags that expire end of turn."""
        eot_flags = {"cannot_attack", "cannot_be_blocked", "cannot_be_destroyed",
                     "cannot_attack_players", "power_attacker_active"}
        for flag in list(self.temp_flags):
            if flag in eot_flags:
                del self.temp_flags[flag]

    def remove_eot_power_modifiers(self) -> None:
        """Remove power modifiers that expire at end of turn."""
        self.power_modifiers = [
            m for m in self.power_modifiers
            if m.duration != "until_end_of_turn"
        ]

    def tap(self) -> None:
        self.is_tapped = True

    def untap(self) -> None:
        self.is_tapped = False

    def clear_summoning_sickness(self) -> None:
        self.has_summoning_sickness = False

    def __repr__(self) -> str:
        state = []
        if self.is_tapped: state.append("tapped")
        if self.has_summoning_sickness: state.append("sick")
        if self.temp_flags: state.append(str(self.temp_flags))
        state_str = f" ({', '.join(state)})" if state else ""
        return f"<Creature:{self.definition.name}[{self.uid}] {self.base_power}{state_str}>"


# ── Graveyard Card ────────────────────────────────────────────────────────────

@dataclass
class GraveyardCard:
    """
    A card in the graveyard.
    Graveyard order matters (newest first = index 0).
    Tracks how it got there — relevant for some trigger conditions.
    """
    definition:      CardDefinition
    uid:             str = field(default_factory=_new_uid)
    died_from:       str = "unknown"   # "battle" | "spell" | "effect" | "discarded"
    died_on_turn:    int = 0

    @property
    def id(self) -> int:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    def __repr__(self) -> str:
        return f"<GY:{self.definition.name}[{self.uid}] via:{self.died_from}>"
