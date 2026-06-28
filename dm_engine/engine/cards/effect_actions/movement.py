"""engine/cards/effect_actions/movement.py — Card draw, summon, and zone placement effects."""
from __future__ import annotations

from core.state import GameState, PendingTrigger
from core.cards import CardDefinition
from core.zones import Creature, HandCard, ManaCard, ShieldCard
from engine.zone_mover import (
    _new_uid,
    draw_card,
    move_hand_to_battle,
    move_hand_to_mana,
    move_hand_to_shield,
)

# ── Shared helpers ──────────────────────────────────────────────────────────

def _effect_value(trigger: PendingTrigger) -> dict:
    return dict(trigger.effect.effect_value)



def _move_card_to_hand(state: GameState, player: int, definition: CardDefinition, uid: str | None = None) -> HandCard:
    card = HandCard(definition=definition, uid=uid or _new_uid())
    state.players[player].hand.append(card)
    return card


def _remove_deck_card(state: GameState, player: int, card_id: int) -> CardDefinition | None:
    deck = state.players[player].deck
    for idx, card in enumerate(deck):
        if card.id == card_id:
            return deck.pop(idx)
    return None


def _trigger_data(trigger: PendingTrigger) -> dict:
    return dict(trigger.trigger_data)



# ── Effect action implementations ──────────────────────────────────────────

def _do_draw(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    amount = int(_effect_value(trigger).get("amount", 1) or 1)
    for _ in range(max(0, amount)):
        draw_card(state, controller)




def _do_add_to_hand(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    definition = data.get("card_definition") or data.get("definition") or _effect_value(trigger).get("card_definition")
    if isinstance(definition, CardDefinition):
        _move_card_to_hand(state, controller, definition)
        return
    card_id = data.get("card_id") or _effect_value(trigger).get("card_id")
    if card_id is not None:
        found = _remove_deck_card(state, controller, int(card_id))
        if found is not None:
            _move_card_to_hand(state, controller, found)




def _do_summon_free(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    card_uid = data.get("card_uid")
    if card_uid:
        move_hand_to_battle(state, controller, card_uid, evolution_base_uid=data.get("evolution_base_uid"))




def _do_put_to_battle_zone(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    _do_summon_free(state, controller, trigger)




def _do_put_to_shield(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    card_uid = data.get("card_uid")
    if card_uid:
        move_hand_to_shield(state, controller, card_uid)




def _do_put_to_mana(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    card_uid = data.get("card_uid")
    if card_uid:
        move_hand_to_mana(state, controller, card_uid)




def _do_move_zone(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    from_zone = data.get("from_zone") or effect.get("from_zone")
    to_zone = data.get("to_zone") or effect.get("to_zone")
    target_uid = data.get("target_uid")
    if not from_zone or not to_zone or not target_uid:
        return
    player = state.players[controller]
    source_list = getattr(player, from_zone, None)
    dest_list = getattr(player, to_zone, None)
    if source_list is None or dest_list is None:
        return
    for card in list(source_list):
        if getattr(card, "uid", None) != target_uid:
            continue
        source_list.remove(card)
        if isinstance(card, Creature):
            state.global_effects.remove_by_source(card.uid)
        if to_zone == "hand":
            _move_card_to_hand(state, controller, card.definition, card.uid)
        elif to_zone == "graveyard":
            state.players[controller].graveyard.insert(0, card)
        elif to_zone == "mana_zone":
            mana = ManaCard.from_charge(card.definition)
            mana.uid = card.uid
            state.players[controller].mana_zone.append(mana)
        elif to_zone == "shield_zone":
            state.players[controller].shield_zone.append(ShieldCard(definition=card.definition, uid=card.uid))
        elif to_zone == "battle_zone" and isinstance(card, HandCard):
            state.players[controller].battle_zone.append(
                Creature(
                    definition=card.definition,
                    uid=card.uid,
                    controller=controller,
                    owner=controller,
                    entered_turn=state.turn_number,
                    has_summoning_sickness=True,
                )
            )
        return
