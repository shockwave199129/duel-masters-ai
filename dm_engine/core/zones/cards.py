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
from enum import Enum, auto
from typing import Optional
from uuid import uuid4

from ..enums import Civilization, Keyword, CardSubtype, CDAFormulaType, EffectAction, INFINITY
from ..cards import CardDefinition, CardEffect


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


# ── Hyperspatial Card ──────────────────────────────────────────────────────────

@dataclass
class HyperspatialCard:
    """
    A card in the Hyperspatial Zone (Psychic, Psychic Super, Dragheart).

    This can be either:
    - A Psychic or Psychic Super Creature (face 0 or 1, treated as Creature)
    - A Dragheart in Weapon or Fortress face (face 0, NOT a creature despite having stats)

    Face-up and visible to both players (rule 407.2).

    Stores the card definition, face, and uid. When summoned, converted to a Creature.

    Rule 805.4 / 807.4: Psychic/Dragheart cards in hyperspatial zone have
    summoning sickness (has_summoning_sickness=True initially).
    """
    definition: CardDefinition
    face:       int = 0  # 0=lower-cost face, 1=awakened/creature face
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
        return f"<Hyperspatial:{self.definition.name}[face={self.face},uid={self.uid}]>"


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
    is_face_up:  bool = False    # 701.32a face-down (default) / 701.32b face-up shield
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
        if self.is_face_up:
            return f"<Shield:{self.definition.name}[FACE-UP]>"
        return f"<Shield:???[{self.uid}]>"



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
    treat_as_hand_discard: bool = False  # Rule 509.5c: S-Back discards count as hand discard

    @property
    def id(self) -> int:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    def __repr__(self) -> str:
        return f"<GY:{self.definition.name}[{self.uid}] via:{self.died_from}>"
