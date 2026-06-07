"""
core/observation.py — Information visibility layer.

GameState always has full information (the engine needs it).
This module builds what each player is ALLOWED to see.

The observation is what gets passed to:
  - The neural network (encoded as a float vector)
  - The bot's decision making
  - The UI display for each player

Visibility rules:
═══════════════════════════════════════════════════════════════

OWN INFORMATION:
  hand          ✓ full — you see all your own hand cards
  mana_zone     ✓ full — you see all your mana cards (face-up)
  battle_zone   ✓ full — you see all your own creatures
  shield_zone   ✗ hidden — you don't see your own shields
                  ✓ partial — you know your deck composition, so
                              you can deduce what MIGHT be there
  deck          ✗ order hidden — you don't know draw order
                ✓ composition known — you know 4x A, 3x B, etc.
                ✓ remaining known — subtract cards already seen

OPPONENT INFORMATION:
  hand          ✗ hidden — you don't see opponent's hand cards
                ✓ count known — you see how many cards they have
  mana_zone     ✓ full — opponent's mana is face-up and visible
  battle_zone   ✓ full — all creatures visible to both players
  shield_zone   ✗ hidden — you don't see opponent's shields
                ✗ composition unknown — you don't know their deck
                ✓ count known — you see how many shields remain
  deck          ✗ fully hidden — count known but not composition
                  (unless revealed by an effect)
  graveyard     ✓ full — graveyard is always public

GLOBAL STATE:
  current phase     ✓ both players always see
  turn number       ✓ both players always see
  whose turn        ✓ both players always see
  global effects    ✓ both players always see
  attack in progress ✓ both players always see
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from .enums import Civilization, Phase
from .cards import CardDefinition
from .zones import ManaCard, Creature, GraveyardCard
from .player_state import PlayerState
from .state import GameState, AttackContext
from .global_effects import GlobalEffect


# ── Observed Card ─────────────────────────────────────────────────────────────

@dataclass
class ObservedCard:
    """
    A card as seen by an observer. May be fully known or partially hidden.
    """
    is_known:       bool             # False = "there's a card here but I can't see it"
    card_id:        Optional[int]    # None if not known
    name:           Optional[str]    # None if not known
    cost:           Optional[int]    # None if not known
    civilizations:  Optional[frozenset[Civilization]]  # None if not known

    @classmethod
    def known(cls, defn: CardDefinition) -> "ObservedCard":
        return cls(
            is_known=True,
            card_id=defn.id,
            name=defn.name,
            cost=defn.cost,
            civilizations=defn.civilizations,
        )

    @classmethod
    def hidden(cls) -> "ObservedCard":
        return cls(is_known=False, card_id=None, name=None, cost=None, civilizations=None)

    def __repr__(self) -> str:
        if self.is_known:
            return f"<ObsCard:{self.name}>"
        return "<ObsCard:???>"


# ── Observed Creature ─────────────────────────────────────────────────────────

@dataclass
class ObservedCreature:
    """
    A creature in the battle zone as seen by an observer.
    Battle zone is always fully visible to both players.
    """
    uid:                    str
    card_id:                int
    name:                   str
    civilizations:          frozenset[Civilization]
    races:                  frozenset[str]
    base_power:             int
    current_power:          int           # after modifiers
    is_tapped:              bool
    has_summoning_sickness: bool
    keywords:               list[str]     # visible keyword names
    controller:             int
    temp_flags:             dict[str, bool]

    @classmethod
    def from_creature(cls, creature: Creature, game_state: GameState) -> "ObservedCreature":
        return cls(
            uid=creature.uid,
            card_id=creature.id,
            name=creature.name,
            civilizations=creature.civilizations,
            races=creature.races,
            base_power=creature.base_power,
            current_power=creature.compute_power(game_state),
            is_tapped=creature.is_tapped,
            has_summoning_sickness=creature.has_summoning_sickness,
            keywords=[kw.value for kw in creature.definition.keywords],
            controller=creature.controller,
            temp_flags=dict(creature.temp_flags),
        )

    def __repr__(self) -> str:
        tap = "⟳" if self.is_tapped else "○"
        return f"<ObsCreature:{tap}{self.name}[{self.current_power}]>"


# ── Observed Mana Card ────────────────────────────────────────────────────────

@dataclass
class ObservedManaCard:
    """
    A card in the mana zone. Always fully visible.
    """
    uid:           str
    card_id:       int
    name:          str
    civilizations: frozenset[Civilization]
    is_tapped:     bool

    @classmethod
    def from_mana(cls, mana: ManaCard) -> "ObservedManaCard":
        return cls(
            uid=mana.uid,
            card_id=mana.id,
            name=mana.name,
            civilizations=mana.civilizations,
            is_tapped=mana.is_tapped,
        )


# ── Observed Player ───────────────────────────────────────────────────────────

@dataclass
class ObservedPlayerState:
    """
    What one player observes about ONE player's state (self or opponent).
    """
    player_index:    int
    is_self:         bool      # True if this is the observer's own state

    # ── Own info (full) / Opponent info (partial) ─────────────────────────────

    # Hand: full if self, only count if opponent
    hand_cards:      list[ObservedCard]   # empty list if opponent (use hand_count)
    hand_count:      int

    # Mana: always fully visible
    mana_zone:       list[ObservedManaCard]
    mana_count:      int
    available_mana:  int
    available_civs:  frozenset[Civilization]

    # Battle zone: always fully visible
    battle_zone:     list[ObservedCreature]

    # Shield zone: count only (hidden to both)
    shield_count:    int

    # Graveyard: always fully visible
    graveyard:       list[ObservedCard]

    # Deck: count always known; composition only if self
    deck_size:       int
    own_deck_composition:   Optional[dict[int, int]]   # None if opponent
    # Cards deduced remaining in deck (only meaningful for self)
    own_cards_remaining:    Optional[dict[int, int]]   # None if opponent

    # Status
    is_eliminated:   bool

    @classmethod
    def build(
        cls,
        player_state: PlayerState,
        observer_player: int,
        game_state: GameState,
    ) -> "ObservedPlayerState":
        """
        Build the observed state of player_state from observer_player's perspective.
        """
        is_self = (player_state.player_index == observer_player)

        # Hand
        if is_self:
            hand_cards = [ObservedCard.known(c.definition) for c in player_state.hand]
        else:
            # Opponent hand: only count visible, not contents
            hand_cards = [ObservedCard.hidden() for _ in player_state.hand]

        # Mana — always visible
        mana_zone = [ObservedManaCard.from_mana(m) for m in player_state.mana_zone]

        # Battle zone — always visible
        battle_zone = [
            ObservedCreature.from_creature(c, game_state)
            for c in player_state.battle_zone
        ]

        # Graveyard — always visible
        graveyard = [
            ObservedCard.known(c.definition) for c in player_state.graveyard
        ]

        # Deck composition — only own deck
        if is_self:
            deck_comp = dict(player_state.deck_composition)
            cards_remaining = player_state.cards_remaining_in_deck_by_id()
        else:
            deck_comp = None
            cards_remaining = None

        return cls(
            player_index=player_state.player_index,
            is_self=is_self,
            hand_cards=hand_cards,
            hand_count=player_state.hand_count,
            mana_zone=mana_zone,
            mana_count=player_state.mana_count,
            available_mana=player_state.available_mana,
            available_civs=player_state.available_civilizations(),
            battle_zone=battle_zone,
            shield_count=player_state.shield_count,
            graveyard=graveyard,
            deck_size=player_state.deck_size,
            own_deck_composition=deck_comp,
            own_cards_remaining=cards_remaining,
            is_eliminated=player_state.is_eliminated,
        )


# ── Full Observation ──────────────────────────────────────────────────────────

@dataclass
class Observation:
    """
    Complete view of the game from one player's perspective.
    This is what the neural network and bot receive.

    Always built fresh from GameState — never stored in GameState itself.
    """
    observer_player:   int
    turn_number:       int
    active_player:     int
    current_phase:     Phase
    is_my_turn:        bool

    # self_state always uses full visibility
    self_state:        ObservedPlayerState
    # opponent_state uses restricted visibility
    opponent_state:    ObservedPlayerState

    # Global effects (both players always see)
    active_global_effects:  list[str]   # human-readable descriptions
    own_restrictions:       list[str]   # restrictions on observer specifically
    opp_restrictions:       list[str]   # restrictions on opponent specifically

    # Attack in progress (fully visible to both)
    attack_in_progress:     bool
    attack_context:         Optional[AttackContext]   # None if no attack

    # Pending choice (if it's this player's choice to make)
    awaited_choice_type:    Optional[str]   # None if no choice pending for this player
    valid_choice_options:   list            # options for the pending choice

    @classmethod
    def build(cls, game_state: GameState, observer: int) -> "Observation":
        """Build a complete observation for the given player."""
        my_player  = game_state.players[observer]
        opp_player = game_state.players[1 - observer]

        self_state = ObservedPlayerState.build(my_player, observer, game_state)
        opp_state  = ObservedPlayerState.build(opp_player, observer, game_state)

        # Global effect descriptions
        own_restrictions = game_state.global_effects.active_restrictions_for_player(observer)
        opp_restrictions = game_state.global_effects.active_restrictions_for_player(1 - observer)

        # Pending choice for this player
        awaited = game_state.effect_stack.awaited_choice
        if awaited and awaited.player == observer:
            choice_type = awaited.choice_type
            valid_opts  = awaited.valid_options
        else:
            choice_type = None
            valid_opts  = []

        return cls(
            observer_player=observer,
            turn_number=game_state.turn_number,
            active_player=game_state.active_player,
            current_phase=game_state.current_phase,
            is_my_turn=(game_state.active_player == observer),
            self_state=self_state,
            opponent_state=opp_state,
            active_global_effects=own_restrictions + opp_restrictions,
            own_restrictions=own_restrictions,
            opp_restrictions=opp_restrictions,
            attack_in_progress=game_state.is_in_attack(),
            attack_context=game_state.attack_context,
            awaited_choice_type=choice_type,
            valid_choice_options=valid_opts,
        )

    def display(self) -> str:
        """Human-readable game view for this player."""
        me  = self.self_state
        opp = self.opponent_state

        lines = [
            f"═══ YOUR VIEW (Player {self.observer_player}) ═══",
            f"Turn {self.turn_number} | Phase: {self.current_phase}",
            f"{'YOUR TURN' if self.is_my_turn else 'OPPONENT TURN'}",
            "",
            f"── OPPONENT (P{opp.player_index}) ──────────────",
            f"  Hand    : {opp.hand_count} cards (hidden)",
            f"  Shields : {'🛡' * opp.shield_count} ({opp.shield_count})",
            f"  Deck    : {opp.deck_size} cards",
            f"  Mana    : {opp.mana_count} ({opp.available_mana} untapped)",
        ]
        if opp.battle_zone:
            lines.append(f"  Battle  :")
            for c in opp.battle_zone:
                tap = "⟳" if c.is_tapped else "○"
                kws = ", ".join(c.keywords) if c.keywords else ""
                lines.append(f"    {tap} {c.name} [{c.current_power}] {kws}")
        else:
            lines.append(f"  Battle  : empty")

        lines += [
            "",
            f"── YOU (P{me.player_index}) ────────────────────",
            f"  Shields : {'🛡' * me.shield_count} ({me.shield_count}, contents hidden)",
            f"  Deck    : {me.deck_size} cards (order hidden)",
            f"  Mana    : {me.mana_count} ({me.available_mana} untapped)",
            f"    Civs  : {', '.join(c.value for c in me.available_civs)}",
        ]

        if me.mana_zone:
            mana_str = ", ".join(
                f"{'⟳' if m.is_tapped else '○'}{'/'.join(c.value[0] for c in m.civilizations)}"
                for m in me.mana_zone
            )
            lines.append(f"    Cards : {mana_str}")

        if me.battle_zone:
            lines.append(f"  Battle  :")
            for c in me.battle_zone:
                tap = "⟳" if c.is_tapped else "○"
                sick = " (sick)" if c.has_summoning_sickness else ""
                kws = ", ".join(c.keywords) if c.keywords else ""
                lines.append(f"    {tap} {c.name} [{c.current_power}]{sick} {kws}")
        else:
            lines.append(f"  Battle  : empty")

        lines.append(f"  Hand    : {me.hand_count} cards")
        for card in me.hand_cards:
            if card.is_known:
                civs = "/".join(c.value[0] for c in (card.civilizations or []))
                lines.append(f"    • {card.name} (cost:{card.cost}) [{civs}]")

        if self.own_restrictions:
            lines.append("")
            lines.append("  ⚠ RESTRICTIONS ON YOU:")
            for r in self.own_restrictions:
                lines.append(f"    • {r}")

        if self.attack_in_progress and self.attack_context:
            lines.append("")
            lines.append(f"  ⚔ ATTACK: {self.attack_context}")

        if self.awaited_choice_type:
            lines.append("")
            lines.append(f"  ❓ CHOICE REQUIRED: {self.awaited_choice_type}")
            lines.append(f"     Options: {len(self.valid_choice_options)}")

        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"<Observation P{self.observer_player} "
            f"T{self.turn_number} {self.current_phase} "
            f"my_turn={self.is_my_turn}>"
        )
