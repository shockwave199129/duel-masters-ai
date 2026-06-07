"""
core/initializer.py — Creates the initial GameState from two DeckDefinitions.

Handles:
  - Shuffling both decks randomly
  - Dealing 5 shields face-down
  - Drawing opening hand of 5 cards
  - Deciding who goes first (random or specified)
  - Setting up deck_composition on each PlayerState (public info)

Usage:
    from core.initializer import initialize_game
    state = initialize_game(deck_p0, deck_p1)
"""

from __future__ import annotations
import random
from typing import Optional

from .enums import Phase
from .cards import CardDefinition, DeckDefinition
from .zones import HandCard, ShieldCard
from .player_state import PlayerState
from .state import GameState, TurnInfo
from .global_effects import GlobalEffectRegistry
from .enums import MAX_SHIELDS, STARTING_HAND_SIZE


def initialize_game(
    deck_p0:      DeckDefinition,
    deck_p1:      DeckDefinition,
    first_player: Optional[int] = None,   # None = random
    seed:         Optional[int] = None,   # for reproducible tests
    game_id:      str = "",
) -> GameState:
    """
    Create the initial GameState for a new game.

    Args:
        deck_p0:      DeckDefinition for Player 0 (must be resolved)
        deck_p1:      DeckDefinition for Player 1 (must be resolved)
        first_player: 0 or 1, or None for random coin flip
        seed:         random seed for reproducibility in tests
        game_id:      identifier for logging

    Returns:
        GameState ready for the first turn (UNTAP phase, player 0 or 1)
    """
    _validate_deck(deck_p0)
    _validate_deck(deck_p1)
    if first_player is not None and first_player not in (0, 1):
        raise ValueError("first_player must be 0, 1, or None")

    if seed is not None:
        random.seed(seed)

    # ── Decide first player ────────────────────────────────────────────────────
    if first_player is None:
        first_player = random.randint(0, 1)

    # ── Build both players ─────────────────────────────────────────────────────
    p0 = _build_player(0, "Player 0", deck_p0, seed=seed)
    p1 = _build_player(1, "Player 1", deck_p1, seed=(seed + 1) if seed is not None else None)

    # ── Build GameState ────────────────────────────────────────────────────────
    state = GameState(
        players=(p0, p1),
        turn_info=TurnInfo(
            turn_number=1,
            active_player=first_player,
            phase=Phase.START_OF_TURN,
            first_player=first_player,
        ),
        global_effects=GlobalEffectRegistry(),
        game_id=game_id,
    )

    return state


def _validate_deck(deck_def: DeckDefinition) -> None:
    """Validate baseline deck construction before game setup."""
    if not deck_def.is_valid():
        raise ValueError(
            f"DeckDefinition '{deck_def.name}' is illegal: expected exactly 40 cards "
            "and no more than 4 copies of a card unless a card effect says otherwise."
        )


def _build_player(
    player_index: int,
    name:         str,
    deck_def:     DeckDefinition,
    seed:         Optional[int] = None,
) -> PlayerState:
    """Build a PlayerState from a DeckDefinition."""
    if seed is not None:
        random.seed(seed)

    # ── Expand deck to ordered list of CardDefinitions ─────────────────────────
    all_cards: list[CardDefinition] = []
    for card_id, count in deck_def.card_counts.items():
        defn = deck_def.card_definitions.get(card_id)
        if defn is None:
            raise ValueError(
                f"Card ID {card_id} not resolved in DeckDefinition '{deck_def.name}'. "
                f"Call CardDatabase.resolve_deck() before initializing."
            )
        all_cards.extend([defn] * count)

    # ── Shuffle ────────────────────────────────────────────────────────────────
    random.shuffle(all_cards)

    # ── Deal shields (top 5 cards → shield zone) ──────────────────────────────
    shield_zone: list[ShieldCard] = []
    for _ in range(MAX_SHIELDS):
        card = all_cards.pop(0)
        shield_zone.append(ShieldCard(definition=card))
    # Shields are face-down — neither player sees their contents initially

    # ── Draw opening hand (next 5 cards → hand) ───────────────────────────────
    hand: list[HandCard] = []
    for _ in range(STARTING_HAND_SIZE):
        card = all_cards.pop(0)
        hand.append(HandCard(definition=card))

    # ── Remaining cards form the deck (order hidden from player) ───────────────
    deck = all_cards   # ordered list, top = index 0

    # ── Build PlayerState ──────────────────────────────────────────────────────
    player = PlayerState(
        player_index=player_index,
        player_name=name,
        hand=hand,
        deck=deck,
        mana_zone=[],
        battle_zone=[],
        shield_zone=shield_zone,
        graveyard=[],
        abyss_zone=[],
        # deck_composition is the PUBLIC info — player always knows this
        deck_composition=dict(deck_def.card_counts),
    )

    return player
