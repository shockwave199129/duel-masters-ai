"""
engine/shield_break_window.py — Batch shield break declaration window.

Rules 112.3a, 113.6, 509.5a-e: declare all S-Trigger/G-Strike/S-Back
responses, move shields to hand, then resolve declared effects in any order.
"""

from __future__ import annotations

from typing import Optional

from core.cards import CardDefinition
from core.enums import Keyword
from core.state import GameState, ShieldBreakWindow
from core.zones import GraveyardCard, HandCard, ShieldCard
from engine.zone_mover import move_standby_shield_to_hand


def open_shield_break_window(state: GameState, player: int, shield: ShieldCard) -> None:
    """Add a broken shield to the active batch window."""
    win = state.effect_stack.shield_break_window
    if win is None or win.defending_player != player:
        win = ShieldBreakWindow(defending_player=player)
        state.effect_stack.shield_break_window = win
    win.standby_shields.append(shield)
    state.effect_stack.add_shield_trigger(player, shield)
    _sync_queue_from_window(state)


def _sync_queue_from_window(state: GameState) -> None:
    """Keep legacy shield_trigger_queue aligned with the batch window."""
    win = state.effect_stack.shield_break_window
    if win is None:
        return
    state.effect_stack.shield_trigger_queue = [
        (win.defending_player, shield) for shield in win.standby_shields
    ]


def find_standby_shield(win: ShieldBreakWindow, uid: str) -> Optional[ShieldCard]:
    for shield in win.standby_shields:
        if shield.uid == uid:
            return shield
    return None


def finish_declarations(state: GameState) -> None:
    """509.5d: move shields to hand and open Sabaki Z timing."""
    win = state.effect_stack.shield_break_window
    if win is None:
        return

    player = win.defending_player
    s_back_discards = {shield_uid for _, shield_uid in win.declared_s_backs}

    for shield in list(win.standby_shields):
        uid = shield.uid
        if uid in s_back_discards:
            state.players[player].graveyard.insert(
                0,
                GraveyardCard(
                    definition=shield.definition,
                    uid=shield.uid,
                    died_from="s_back_discard",
                    died_on_turn=state.turn_number,
                ),
            )
            win.standby_shields.remove(shield)
            continue

        hand_card = move_standby_shield_to_hand(state, player, uid)
        win.standby_shields.remove(shield)
        if hand_card.definition.has_emblem_of_judgment():
            win.emblems_added.append(hand_card.uid)

    _build_pending_resolutions(state)
    win.phase = "resolve"
    _sync_queue_from_window(state)


def _build_pending_resolutions(state: GameState) -> None:
    win = state.effect_stack.shield_break_window
    if win is None:
        return

    win.pending_resolutions.clear()
    for uid in sorted(win.declared_s_triggers):
        win.pending_resolutions.append(("s_trigger", uid, ""))
    for uid in sorted(win.declared_g_strikes):
        win.pending_resolutions.append(("g_strike", uid, ""))
    for hand_uid, shield_uid in win.declared_s_backs:
        win.pending_resolutions.append(("s_back", hand_uid, shield_uid))


def close_window_if_done(state: GameState) -> None:
    win = state.effect_stack.shield_break_window
    if win is None or win.phase != "resolve":
        return
    for kind, primary, secondary in win.pending_resolutions:
        key = f"{kind}:{primary}:{secondary}"
        if key not in win.resolved_keys:
            return
    state.effect_stack.shield_break_window = None
    state.effect_stack.shield_trigger_queue.clear()


def execute_free_from_hand(
    state: GameState,
    player: int,
    card_uid: str,
    *,
    reason: str,
) -> None:
    """Free execution of a card from hand (S-Trigger after hand add, Sabaki Z, etc.)."""
    from engine.zone_mover import move_hand_to_battle, move_hand_to_graveyard

    hand_card = state.players[player].find_in_hand(card_uid)
    if hand_card is None:
        raise ValueError(f"Card {card_uid} not found in hand for free execution")

    if hand_card.definition.is_creature():
        move_hand_to_battle(state, player, card_uid)
    else:
        move_hand_to_graveyard(state, player, card_uid, reason=reason)


def discard_hand_card(state: GameState, player: int, card_uid: str, *, reason: str) -> None:
    hand_card = state.players[player].find_in_hand(card_uid)
    if hand_card is None:
        raise ValueError(f"Card {card_uid} not found in hand for discard")
    state.players[player].hand = [c for c in state.players[player].hand if c.uid != card_uid]
    state.players[player].graveyard.insert(
        0,
        GraveyardCard(
            definition=hand_card.definition,
            uid=hand_card.uid,
            died_from=reason,
            died_on_turn=state.turn_number,
        ),
    )
