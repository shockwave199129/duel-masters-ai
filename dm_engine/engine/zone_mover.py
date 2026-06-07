"""
engine/zone_mover.py — centralized card movement helpers.

These helpers mutate the GameState object they receive. Callers that expose a
public API should copy the state before using them, then return the copy.
"""

from __future__ import annotations

from typing import Optional

from core.enums import ManaUsage
from core.state import GameState
from core.zones import Creature, GraveyardCard, HandCard, ManaCard, ShieldCard


def tap_mana_for_payment(state: GameState, player: int, mana_used: tuple[ManaUsage, ...]) -> None:
    """Tap the exact mana cards selected to pay a cost."""
    p_state = state.players[player]
    for usage in mana_used:
        mana = p_state.find_mana(usage.mana_uid)
        if mana is None:
            raise ValueError(f"Mana card {usage.mana_uid} not found for player {player}")
        if mana.is_tapped:
            raise ValueError(f"Mana card {usage.mana_uid} is already tapped")
        mana.tap()


def move_hand_to_mana(state: GameState, player: int, card_uid: str) -> ManaCard:
    """Move a hand card into mana, applying tapped entry for multi-civ cards."""
    hand_card = _remove_from_hand(state, player, card_uid)
    mana = ManaCard.from_charge(hand_card.definition)
    state.players[player].mana_zone.append(mana)
    state.players[player].has_charged_mana_this_turn = True
    return mana


def move_hand_to_battle(
    state: GameState,
    player: int,
    card_uid: str,
    *,
    evolution_base_uid: Optional[str] = None,
) -> Creature:
    """Move a creature from hand to battle, or evolve an existing base."""
    hand_card = _remove_from_hand(state, player, card_uid)
    p_state = state.players[player]

    if evolution_base_uid:
        base = p_state.find_creature(evolution_base_uid)
        if base is None:
            raise ValueError(f"Evolution base {evolution_base_uid} not found")

        # Rule 801.2/801.3: evolution keeps the same creature state and does
        # not suffer summoning sickness.
        base.evolution_base.insert(0, base.definition)
        base.definition = hand_card.definition
        base.has_summoning_sickness = False
        return base

    creature = Creature(
        definition=hand_card.definition,
        controller=player,
        owner=player,
        entered_turn=state.turn_number,
        has_summoning_sickness=True,
    )
    p_state.battle_zone.append(creature)
    return creature


def move_hand_to_graveyard(
    state: GameState,
    player: int,
    card_uid: str,
    *,
    reason: str = "discarded",
) -> GraveyardCard:
    """Move a card from hand to the graveyard."""
    hand_card = _remove_from_hand(state, player, card_uid)
    graveyard_card = GraveyardCard(
        definition=hand_card.definition,
        uid=hand_card.uid,
        died_from=reason,
        died_on_turn=state.turn_number,
    )
    state.players[player].graveyard.insert(0, graveyard_card)
    return graveyard_card


def move_battle_to_graveyard(
    state: GameState,
    player: int,
    creature_uid: str,
    *,
    reason: str = "destroyed",
) -> GraveyardCard:
    """Move a battle-zone card to the graveyard."""
    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Battle-zone card {creature_uid} not found")
    state.players[player].battle_zone.remove(creature)
    state.global_effects.remove_by_source(creature.uid)
    graveyard_card = GraveyardCard(
        definition=creature.definition,
        uid=creature.uid,
        died_from=reason,
        died_on_turn=state.turn_number,
    )
    state.players[player].graveyard.insert(0, graveyard_card)
    return graveyard_card


def cross_gear_to_creature(
    state: GameState,
    player: int,
    gear_uid: str,
    target_uid: str,
) -> None:
    """Attach a generated Cross Gear to a creature."""
    p_state = state.players[player]
    gear = p_state.find_creature(gear_uid)
    target = p_state.find_creature(target_uid)
    if gear is None:
        raise ValueError(f"Cross Gear {gear_uid} not found")
    if target is None:
        raise ValueError(f"Cross Gear target {target_uid} not found")
    p_state.battle_zone.remove(gear)
    target.attached_cards.append(gear.definition)


def fortify_shield_with_castle(
    state: GameState,
    player: int,
    castle_uid: str,
    shield_uid: str,
) -> ShieldCard:
    """Move a Castle from hand underneath one of its owner's shields."""
    hand_card = _remove_from_hand(state, player, castle_uid)
    shield = state.players[player].find_shield(shield_uid)
    if shield is None:
        raise ValueError(f"Shield {shield_uid} not found")
    shield.fortified_castles.append(hand_card.definition)
    return shield


def draw_card(state: GameState, player: int) -> Optional[HandCard]:
    """Draw the top card of the deck into hand. Empty deck draws do nothing."""
    p_state = state.players[player]
    if not p_state.deck:
        return None
    defn = p_state.deck.pop(0)
    hand_card = HandCard(definition=defn)
    p_state.hand.append(hand_card)
    p_state.has_drawn_this_turn = True
    return hand_card


def move_shield_to_standby(state: GameState, player: int, shield_index: int) -> ShieldCard:
    """Remove one shield from shield zone and queue it for trigger declaration."""
    p_state = state.players[player]
    if shield_index < 0 or shield_index >= len(p_state.shield_zone):
        raise ValueError(f"Invalid shield index {shield_index}")
    shield = p_state.shield_zone.pop(shield_index)
    shield.reveal()
    state.effect_stack.add_shield_trigger(player, shield)
    return shield


def move_standby_shield_to_hand(state: GameState, player: int, shield_uid: str) -> HandCard:
    """Move a queued standby shield to its owner's hand."""
    for idx, (queued_player, shield) in enumerate(state.effect_stack.shield_trigger_queue):
        if queued_player == player and shield.uid == shield_uid:
            state.effect_stack.shield_trigger_queue.pop(idx)
            hand_card = HandCard(definition=shield.definition, uid=shield.uid)
            state.players[player].hand.append(hand_card)
            return hand_card
    raise ValueError(f"Standby shield {shield_uid} not found")


def _remove_from_hand(state: GameState, player: int, card_uid: str) -> HandCard:
    p_state = state.players[player]
    hand_card = p_state.find_in_hand(card_uid)
    if hand_card is None:
        raise ValueError(f"Hand card {card_uid} not found for player {player}")
    p_state.hand.remove(hand_card)
    return hand_card
