"""engine/special_cards/shieldify.py — Shieldify effect (701.32, 113.1)."""
from __future__ import annotations

import random
from typing import Optional

from core.cards import CardDefinition
from core.enums import MAX_SHIELDS
from core.state import GameState
from core.zones import ShieldCard, HandCard
from core.zones.cards import _new_uid


def shield_zone_has_room(player_state) -> bool:
    """Rule 113.1: shield zone holds at most MAX_SHIELDS shields."""
    return len(player_state.shield_zone) < MAX_SHIELDS


def make_shield_card(
    definition: CardDefinition,
    *,
    uid: str | None = None,
    face_up: bool = False,
) -> ShieldCard:
    """
    Create a shield card (701.32a face-down, 701.32b face-up).
    """
    return ShieldCard(
        definition=definition,
        uid=uid or _new_uid(),
        is_face_up=face_up,
    )


def add_shield_card(
    state: GameState,
    target_player: int,
    definition: CardDefinition,
    *,
    uid: str | None = None,
    face_up: bool = False,
) -> Optional[ShieldCard]:
    """
    Add one shield if room remains (101.3 partial resolution when full).
    """
    p_state = state.players[target_player]
    if not shield_zone_has_room(p_state):
        return None
    shield = make_shield_card(definition, uid=uid, face_up=face_up)
    p_state.shield_zone.append(shield)
    return shield


def _take_from_hand(
    p_state,
    card_uid: str | None,
) -> tuple[CardDefinition, str] | None:
    if card_uid:
        hand_card = p_state.find_in_hand(card_uid)
        if hand_card is None:
            return None
        p_state.hand.remove(hand_card)
        return hand_card.definition, hand_card.uid

    if not p_state.hand:
        return None
    hand_card = random.choice(p_state.hand)
    p_state.hand.remove(hand_card)
    return hand_card.definition, hand_card.uid


def _take_from_deck(
    p_state,
    card_uid: str | None,
) -> CardDefinition | None:
    if not p_state.deck:
        return None

    if card_uid is not None:
        for index, definition in enumerate(p_state.deck):
            if str(definition.id) == str(card_uid):
                return p_state.deck.pop(index)
        return None

    return p_state.deck.pop(0)


def perform_shieldify(
    state: GameState,
    controller: int,
    *,
    from_zone: str = "hand",
    target_player: int | None = None,
    card_uid: str | None = None,
    card_uids: list[str] | None = None,
    face_up: bool = False,
    count: int = 1,
) -> int:
    """
    Rule 701.32: turn card(s) from hand or deck into shield(s).

    Returns the number of shields successfully added. Stops at MAX_SHIELDS
    per target player (101.3 — do everything you can).
    """
    target = target_player if target_player is not None else controller
    p_state = state.players[controller]
    added = 0

    uids_to_process: list[str | None]
    if card_uids:
        uids_to_process = list(card_uids)
    elif card_uid:
        uids_to_process = [card_uid]
    else:
        uids_to_process = [None] * max(1, count)

    for uid in uids_to_process:
        if not shield_zone_has_room(state.players[target]):
            break

        if from_zone == "hand":
            taken = _take_from_hand(p_state, uid)
            if taken is None:
                continue
            definition, hand_uid = taken
            if add_shield_card(
                state, target, definition, uid=hand_uid, face_up=face_up,
            ):
                added += 1
        elif from_zone == "deck":
            definition = _take_from_deck(p_state, uid)
            if definition is None:
                continue
            if add_shield_card(state, target, definition, face_up=face_up):
                added += 1
        else:
            break

    return added
