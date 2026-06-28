"""engine/cards/effect_actions/keywords.py — Keyword grant and utility effects."""
from __future__ import annotations

import random

from engine.zone_mover import _new_uid
from core.state import GameState, PendingTrigger
from core.zones import Creature, HandCard, ManaCard, ShieldCard

# ── Shared helpers ──────────────────────────────────────────────────────────

def _effect_value(trigger: PendingTrigger) -> dict:
    return dict(trigger.effect.effect_value)



def _find_creature(state: GameState, uid: str) -> tuple[int, Creature] | None:
    if not uid:
        return None
    return state.find_creature_anywhere(uid)



def _find_mana(state: GameState, player: int, uid: str) -> ManaCard | None:
    if not uid:
        return None
    return state.players[player].find_mana(uid)


def _find_shield(state: GameState, player: int, uid: str) -> ShieldCard | None:
    if not uid:
        return None
    return state.players[player].find_shield(uid)


def _move_card_to_hand(state: GameState, player: int, definition: CardDefinition, uid: str | None = None) -> HandCard:
    card = HandCard(definition=definition, uid=uid or _new_uid())
    state.players[player].hand.append(card)
    return card


def _trigger_data(trigger: PendingTrigger) -> dict:
    return dict(trigger.trigger_data)



# ── Effect action implementations ──────────────────────────────────────────

def _do_give_keyword(state: GameState, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    target_uid = data.get("target_uid")
    keyword = effect.get("keyword") or data.get("keyword")
    if not keyword:
        return
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    creature.temp_flags[str(keyword)] = True



def _do_tap(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    uid = data.get("mana_uid") or data.get("target_uid")
    mana = _find_mana(state, controller, uid)
    if mana:
        mana.tap()
        return
    found = _find_creature(state, uid)
    if found:
        _, creature = found
        creature.tap()




def _do_untap(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    uid = data.get("mana_uid") or data.get("target_uid")
    mana = _find_mana(state, controller, uid)
    if mana:
        mana.untap()
        return
    found = _find_creature(state, uid)
    if found:
        _, creature = found
        creature.untap()



def _do_shuffle(state: GameState, controller: int) -> None:
    random.shuffle(state.players[controller].deck)



def _do_reveal(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    found = _find_shield(state, controller, target_uid)
    if found:
        found.reveal()
        return
    found_creature = _find_creature(state, target_uid)
    if found_creature:
        _, creature = found_creature
        creature.temp_flags["revealed"] = True



def _do_look_at_top(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    effect = _effect_value(trigger)
    amount = int(effect.get("amount", 1) or 1)
    move_to_hand = bool(effect.get("move_to_hand", False))
    if amount <= 0:
        return
    deck = state.players[controller].deck
    cards = deck[:amount]
    if move_to_hand:
        del deck[:amount]
        for definition in cards:
            _move_card_to_hand(state, controller, definition)
