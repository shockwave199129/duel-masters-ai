"""engine/cards/effect_actions/zone_ops.py — Card movement and zone operation effects."""
from __future__ import annotations

from core.state import GameState, PendingTrigger
from core.zones import Creature, HandCard, ManaCard, ShieldCard
from engine.zone_mover import (
    _new_uid,
    move_battle_to_graveyard,
    move_hand_to_graveyard,
)

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


def _remove_deck_card(state: GameState, player: int, card_id: int) -> CardDefinition | None:
    deck = state.players[player].deck
    for idx, card in enumerate(deck):
        if card.id == card_id:
            return deck.pop(idx)
    return None


def _trigger_data(trigger: PendingTrigger) -> dict:
    return dict(trigger.trigger_data)



# ── Effect action implementations ──────────────────────────────────────────

def _do_return_to_hand(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    target_zone = data.get("target_zone", "battle_zone")

    if target_zone == "battle_zone":
        found = _find_creature(state, target_uid)
        if not found:
            return
        player_idx, creature = found
        state.players[player_idx].battle_zone.remove(creature)
        state.global_effects.remove_by_source(creature.uid)
        _move_card_to_hand(state, player_idx, creature.definition, creature.uid)
        return

    if target_zone == "shield_zone":
        shield = _find_shield(state, controller, target_uid)
        if not shield:
            return
        state.players[controller].shield_zone.remove(shield)
        _move_card_to_hand(state, controller, shield.definition, shield.uid)
        return

    if target_zone == "mana_zone":
        mana = _find_mana(state, controller, target_uid)
        if not mana:
            return
        state.players[controller].mana_zone.remove(mana)
        _move_card_to_hand(state, controller, mana.definition, mana.uid)
        return




def _do_discard(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    card_uid = data.get("card_uid")
    if card_uid:
        move_hand_to_graveyard(state, controller, card_uid, reason="effect")




def _do_destroy(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Destroy target creature(s). 
    
    Rule 606.2: If an effect targets multiple creatures and some targets
    become invalid, the effect still resolves on all remaining valid targets.
    "Do everything you can" (Rule 101.3).
    """
    data = _trigger_data(trigger)
    
    # Single target (backward compatibility)
    target_uid = data.get("target_uid")
    if target_uid:
        found = _find_creature(state, target_uid)
        if not found:
            return
        player_idx, creature = found
        if not creature.can_be_destroyed():
            return
        move_battle_to_graveyard(state, player_idx, creature.uid, reason="effect")
        return
    
    # Multiple targets (partial execution, Rule 606.2)
    target_uids = data.get("target_uids", [])
    destroyed_any = False
    for uid in target_uids:
        found = _find_creature(state, uid)
        if not found:
            continue  # Skip invalid targets (Rule 606.2: partial execution)
        player_idx, creature = found
        if not creature.can_be_destroyed():
            continue  # Skip creatures that can't be destroyed
        move_battle_to_graveyard(state, player_idx, creature.uid, reason="effect")
        destroyed_any = True
    
    # Even if no creatures were destroyed, the effect still resolved
    # (Rule 101.3: do everything that can be done)




def _do_banish_to_abyss(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    for zone_name in ("battle_zone", "hand", "mana_zone", "shield_zone"):
        zone = getattr(state.players[controller], zone_name)
        for card in list(zone):
            if getattr(card, "uid", None) != target_uid:
                continue
            zone.remove(card)
            state.players[controller].abyss_zone.append(card.definition)
            if zone_name == "battle_zone":
                state.global_effects.remove_by_source(card.uid)
            return




def _do_search_deck(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    card_ids = data.get("card_ids") or effect.get("card_ids") or []
    if isinstance(card_ids, int):
        card_ids = [card_ids]
    if not card_ids:
        card_id = data.get("card_id") or effect.get("card_id")
        if card_id is not None:
            card_ids = [card_id]
    for card_id in card_ids:
        found = _remove_deck_card(state, controller, int(card_id))
        if found is not None:
            _move_card_to_hand(state, controller, found)
