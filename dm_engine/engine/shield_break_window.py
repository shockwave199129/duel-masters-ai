"""
engine/shield_break_window.py — Batch shield break declaration window.

Rules 112.3a, 113.6, 509.5a-e: declare all S-Trigger/G-Strike/S-Back
responses, move shields to hand, then resolve declared effects in any order.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from core.card_scripts import SUPER_SHIELD_TRIGGER_KEY, TWINPACT_SPELLS, scripted_effects
from core.continuous_rules import queue_ability
from core.enums import CardType, EffectType, TriggerEvent
from core.state import PendingTrigger
from core.state import GameState, ShieldBreakWindow
from core.zones import GraveyardCard, ResolvingCard, ShieldCard
from engine.zone_mover import move_standby_shield_to_hand


def open_shield_break_window(state: GameState, player: int, shield: ShieldCard) -> None:
    """
    Add a broken shield to the batch being declared.

    Shields broken while an earlier batch is resolving form a new batch that is
    dealt with first; the earlier one waits (rule 101.4g).
    """
    win = state.effect_stack.shield_break_window
    if win is None or win.defending_player != player or win.phase != "declare":
        win = ShieldBreakWindow(defending_player=player, parent=win)
        state.effect_stack.shield_break_window = win
    win.standby_shields.append(shield)
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
    state.effect_stack.shield_break_window = win.parent
    state.effect_stack.shield_trigger_queue.clear()
    if win.parent is not None:
        _sync_queue_from_window(state)
        close_window_if_done(state)


def queue_spell_effects(
    state: GameState, player: int, card_uid: str, definition, *, paid: bool = False,
    destination: str = "graveyard", super_ready: bool = False,
    owner: int | None = None, card_definition=None,
) -> None:
    """
    A spell has been cast. The caller has taken the card out of its zone.

    Its effects resolve before anything else that is waiting (rule 601.1g), and
    only then does the card go to ``destination`` (rule 605.2f).
    """
    from engine.trigger_registry import fire_trigger
    from engine.entry_rules import free_play

    p_state = state.players[player]
    p_state.spells_cast_this_turn += 1
    if not paid:
        free_play(state, player)
    fire_trigger(state, TriggerEvent.ON_CAST, {
        "source_uid": card_uid,
        "source_card_id": definition.id,
        "controller": player,
        "zone": "graveyard",
    }, card_uid)

    effects = [
        PendingTrigger(effect=effect, source_uid=card_uid,
                       source_card_id=definition.id, controller=player)
        for effect in definition.effects if effect.effect_type == EffectType.SPELL
    ]
    if super_ready:
        effects += _scripted_triggers(definition, SUPER_SHIELD_TRIGGER_KEY, card_uid, player)
    # A Twinpact card is cast as one side but remains the whole card
    p_state.resolving.append(ResolvingCard(card_definition or definition, card_uid,
                                           destination, owner))
    if not effects:
        finish_spell(state, player, card_uid)
        return
    effects.append(rules_trigger("finish_spell", card_uid, definition.id, player))
    state.effect_stack.add_immediate(effects)


def spell_side(definition):
    """The spell half of a Twinpact card, as a spell, or None if it has none."""
    side = TWINPACT_SPELLS.get(definition.slug)
    if side is None:
        return None
    effects = tuple(
        replace(effect, effect_type=EffectType.SPELL, trigger_event=TriggerEvent.ON_CAST)
        for effect in definition.effects
        if effect.effect_value.get("script", "").endswith("#twin_spell")
    )
    return replace(definition, card_type=CardType.SPELL, cost=side[0],
                   civilizations=side[1], effects=effects)


def finish_spell(state: GameState, player: int, card_uid: str) -> None:
    """The last step of resolving a spell: the card reaches its destination."""
    p_state = state.players[player]
    card = next((c for c in p_state.resolving if c.uid == card_uid), None)
    if card is None:
        return
    p_state.resolving.remove(card)
    if card.owner is not None:
        p_state = state.players[card.owner]
    if card.destination == "deck_bottom":
        p_state.deck.append(card.definition)
    else:
        p_state.graveyard.insert(0, GraveyardCard(
            definition=card.definition, uid=card.uid,
            died_from="cast", died_on_turn=state.turn_number,
        ))


def rules_trigger(key: str, source_uid: str, card_id: int, player: int) -> PendingTrigger:
    """An effect the rules create, written as a script."""
    effect = next(
        e for e in scripted_effects("_rules", card_id)
        if e.effect_value["script"].endswith(f"#{key}")
    )
    return PendingTrigger(effect=effect, source_uid=source_uid,
                          source_card_id=card_id, controller=player)


def _scripted_triggers(definition, key: str, source_uid: str, player: int) -> list[PendingTrigger]:
    return [
        PendingTrigger(effect=effect, source_uid=source_uid, source_card_id=definition.id,
                       controller=player)
        for effect in definition.effects
        if effect.effect_value.get("script", "").endswith(f"#{key}")
    ]


def execute_free_from_hand(
    state: GameState,
    player: int,
    card_uid: str,
    *,
    reason: str,
) -> None:
    """Free execution of a card from hand (S-Trigger after hand add, Sabaki Z, etc.)."""
    from engine.zone_mover import move_hand_to_battle, move_hand_to_field, move_hand_to_graveyard

    hand_card = state.players[player].find_in_hand(card_uid)
    if hand_card is None:
        raise ValueError(f"Card {card_uid} not found in hand for free execution")

    definition = hand_card.definition
    # Super Shield Trigger: the extra ability needs this to have been the last shield
    super_ready = reason == "shield_trigger" and not state.players[player].shield_zone
    if definition.is_spell():
        state.players[player].hand.remove(hand_card)
        queue_spell_effects(state, player, card_uid, definition, super_ready=super_ready)
        return
    if definition.is_creature():
        move_hand_to_battle(state, player, card_uid)
    elif definition.card_type == CardType.FIELD:
        move_hand_to_field(state, player, card_uid)
    else:
        move_hand_to_graveyard(state, player, card_uid, reason=reason)
    if super_ready:
        offer_super_shield_trigger(state, player, card_uid, definition)


def offer_super_shield_trigger(state: GameState, player: int, card_uid: str, definition) -> None:
    """Queue a card's Super Shield Trigger ability, after its normal effects."""
    queue_ability(state, definition, SUPER_SHIELD_TRIGGER_KEY, source_uid=card_uid,
                  controller=player, data={})


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
