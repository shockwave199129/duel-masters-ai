"""
search/determinize.py - a possible world that matches what one player knows.

Every card the player cannot know is gathered, per owner, shuffled, and dealt
back into the same places. Zone sizes and everything the player can see stay as
they are.

The unseen cards are the owner's deck list minus the cards in view, so this
assumes the player knows the opponent's deck list.

What the player is taken to know:
  - their own hand, every public zone and face-up shields
  - hidden cards that were public earlier and kept their uid, such as a creature
    returned to a hand
  - cards named by the choice or the waiting effects they are dealing with
  - their own broken shields while they decide what to use
Other things a person could remember are forgotten, such as which cards were put
on the bottom of a deck. Forgetting never gives the player extra information.
"""

from __future__ import annotations

import random

from core.card_scripts import OPPONENT_HAND_REVEALED
from core.continuous_rules import controls
from core.state import GameState


class _Slot:
    """One place holding a card the player cannot know."""

    def __init__(self, holder, key):
        self.holder, self.key = holder, key

    def get(self):
        if isinstance(self.holder, list):
            return self.holder[self.key]
        return getattr(self.holder, self.key)

    def set(self, definition) -> None:
        if isinstance(self.holder, list):
            self.holder[self.key] = definition
        else:
            setattr(self.holder, self.key, definition)


def _windows(state: GameState):
    window = state.effect_stack.shield_break_window
    while window is not None:
        yield window
        window = window.parent


def _named_cards(state: GameState, observer: int) -> tuple[set, set]:
    """uids, and positions in the observer's deck, that must stay as they are."""
    uids: set = set()
    deck_positions: set = set()
    choice = state.effect_stack.awaited_choice
    if choice is not None and choice.player == observer:
        for option, card_id in choice.option_cards.items():
            if not card_id:
                continue
            if isinstance(option, str) and option.startswith("deck:"):
                deck_positions.add(int(option.split(":", 1)[1]))
            else:
                uids.add(option)
    waiting = list(state.effect_stack.pending_triggers)
    if choice is not None and choice.trigger is not None:
        waiting.append(choice.trigger)
    for trigger in waiting:
        uids.add(trigger.source_uid)
        uids.update(v for v in trigger.trigger_data.values() if isinstance(v, str))
    for window in _windows(state):
        # Declared responses have been shown to both players
        uids.update(window.declared_s_triggers, window.declared_g_strikes,
                    window.emblems_added)
        for pair in window.declared_s_backs:
            uids.update(pair)
        for _, primary, secondary in window.pending_resolutions:
            uids.update((primary, secondary))
    return uids, deck_positions


def hidden_slots(state: GameState, observer: int, player: int) -> list[_Slot]:
    """The places of ``player``'s cards that ``observer`` cannot know."""
    p_state = state.players[player]
    named, deck_positions = _named_cards(state, observer)
    known = state.public_uids | named
    slots: list[_Slot] = []

    if player != observer and not controls(state, observer, OPPONENT_HAND_REVEALED):
        slots += [_Slot(card, "definition") for card in p_state.hand if card.uid not in known]

    shields = [s for s in p_state.shield_zone if not (s.is_face_up or s.is_revealed)]
    for window in _windows(state):
        # The owner looks at a broken shield to decide whether to use it
        if window.defending_player == player and player != observer:
            shields += window.standby_shields
    seen_ids = set()
    for shield in shields:
        if shield.uid not in known and id(shield) not in seen_ids:
            seen_ids.add(id(shield))
            slots.append(_Slot(shield, "definition"))

    for index in range(len(p_state.deck)):
        if not (player == observer and index in deck_positions):
            slots.append(_Slot(p_state.deck, index))

    # Seals are face-down cards taken from the top of a deck
    for p in state.players:
        for creature in p.battle_zone:
            if creature.owner == player:
                slots += [_Slot(creature.seals, i) for i in range(len(creature.seals))]
    return slots


def _canonical(definition) -> tuple:
    return (definition.id, definition.name, definition.cost, str(definition.card_type))


def sample_world(state: GameState, observer: int, rng: random.Random) -> GameState:
    """A copy of ``state`` with everything ``observer`` cannot know dealt again."""
    world = state.copy()
    world.rng_salt = f"world-{rng.getrandbits(48):012x}"
    for player in range(len(world.players)):
        slots = hidden_slots(world, observer, player)
        # Sorted first, so the result depends only on which cards are unseen and
        # never on where they really are
        pool = sorted((slot.get() for slot in slots), key=_canonical)
        rng.shuffle(pool)
        for slot, definition in zip(slots, pool):
            slot.set(definition)
        gr_zone = world.players[player].ultra_gr_zone  # face-down, order unknown
        gr_zone.sort(key=_canonical)
        rng.shuffle(gr_zone)
    return world
