"""engine/effect_executor.py — execute parsed card effects incrementally."""

from __future__ import annotations

import random

from core.cards import CardDefinition
from core.enums import EffectAction
from core.state import GameState, PendingTrigger
from core.zones import Creature, HandCard, ManaCard, ShieldCard, PowerModifier
from engine.sba_checker import check_state_based_actions
from engine.zone_mover import (
    _new_uid,
    combine_king_cells,
    draw_card,
    dragsolve_dragheart,
    awaken_psychic_creature,
    link_psychic_cells,
    move_battle_to_graveyard,
    move_battle_to_hyperspatial,
    move_hand_to_battle,
    move_hand_to_graveyard,
    move_hand_to_mana,
    move_hand_to_shield,
    move_shield_to_standby,
    tap_mana_for_payment,
)


def execute_pending_trigger(state: GameState, trigger: PendingTrigger) -> GameState:
    """Execute one pending trigger and run SBAs after it resolves."""
    s = state.copy()
    effect = trigger.effect
    action = effect.effect_action
    controller = trigger.controller

    if action == EffectAction.DRAW:
        _do_draw(s, controller, trigger)
    elif action == EffectAction.DESTROY:
        _do_destroy(s, controller, trigger)
    elif action == EffectAction.RETURN_TO_HAND:
        _do_return_to_hand(s, controller, trigger)
    elif action == EffectAction.SEARCH_DECK:
        _do_search_deck(s, controller, trigger)
    elif action == EffectAction.PUT_TO_MANA:
        _do_put_to_mana(s, controller, trigger)
    elif action == EffectAction.SUMMON_FREE:
        _do_summon_free(s, controller, trigger)
    elif action == EffectAction.PUT_TO_BATTLE_ZONE:
        _do_put_to_battle_zone(s, controller, trigger)
    elif action == EffectAction.PUT_TO_SHIELD:
        _do_put_to_shield(s, controller, trigger)
    elif action == EffectAction.ADD_TO_HAND:
        _do_add_to_hand(s, controller, trigger)
    elif action == EffectAction.DISCARD:
        _do_discard(s, controller, trigger)
    elif action == EffectAction.TAP:
        _do_tap(s, controller, trigger)
    elif action == EffectAction.UNTAP:
        _do_untap(s, controller, trigger)
    elif action == EffectAction.POWER_MODIFY:
        _do_power_modify(s, controller, trigger)
    elif action == EffectAction.POWER_FIX:
        _do_power_fix(s, controller, trigger)
    elif action == EffectAction.CANNOT_ATTACK:
        _set_creature_flag(s, trigger, "cannot_attack")
    elif action == EffectAction.CANNOT_BE_BLOCKED:
        _set_creature_flag(s, trigger, "cannot_be_blocked")
    elif action == EffectAction.CANNOT_BE_DESTROYED:
        _set_creature_flag(s, trigger, "cannot_be_destroyed")
    elif action == EffectAction.WIN_BATTLE:
        _set_creature_flag(s, trigger, "win_battle")
    elif action == EffectAction.BREAK_SHIELD:
        _do_break_shield(s, controller, trigger)
    elif action == EffectAction.LOOK_AT_TOP:
        _do_look_at_top(s, controller, trigger)
    elif action == EffectAction.SHUFFLE:
        _do_shuffle(s, controller)
    elif action == EffectAction.COST_REDUCE:
        _store_temp_value(s, trigger, "cost_reduce")
    elif action == EffectAction.COST_INCREASE:
        _store_temp_value(s, trigger, "cost_increase")
    elif action == EffectAction.GIVE_KEYWORD:
        _do_give_keyword(s, trigger)
    elif action == EffectAction.BANISH_TO_ABYSS:
        _do_banish_to_abyss(s, controller, trigger)
    elif action == EffectAction.MOVE_ZONE:
        _do_move_zone(s, controller, trigger)
    elif action == EffectAction.REVEAL:
        _do_reveal(s, controller, trigger)
    elif action == EffectAction.GR_SUMMON:
        _do_gr_summon(s, controller, trigger)
    elif action == EffectAction.COPY_EFFECT:
        _store_temp_value(s, trigger, "copy_effect")
    elif action == EffectAction.ATTACH_SEAL:
        _do_attach_seal(s, controller, trigger)
    elif action == EffectAction.REMOVE_SEAL:
        _do_remove_seal(s, controller, trigger)
    elif action == EffectAction.GACHINKO_JUDGE:
        _store_temp_value(s, trigger, "gachinko_judge")
    elif action == EffectAction.HYPERIZE:
        _do_hyperize(s, controller, trigger)
    elif action == EffectAction.AWAKEN:
        _do_awaken(s, controller, trigger)
    elif action == EffectAction.AWAKEN_LINK:
        _do_awaken_link(s, controller, trigger)
    elif action == EffectAction.DRAGSOLVE:
        _do_dragsolve(s, controller, trigger)
    elif action == EffectAction.LINK_RELEASE:
        _do_link_release(s, controller, trigger)
    elif action == EffectAction.DRAGON_EVASION:
        _do_dragon_evasion(s, controller, trigger)
    elif action == EffectAction.DRAGON_SOUL_EVASION:
        _do_dragon_soul_evasion(s, controller, trigger)
    elif action == EffectAction.PSYCHIC_RELEASE:
        _do_psychic_release(s, controller, trigger)
    elif action == EffectAction.COMBINE:
        _do_combine(s, controller, trigger)
    elif action == EffectAction.EXTRA_EX_LIFE:
        _store_temp_value(s, trigger, "extra_ex_life")
    # EffectAction.NONE and unknown values intentionally no-op.

    return check_state_based_actions(s)


def _trigger_data(trigger: PendingTrigger) -> dict:
    return dict(trigger.trigger_data)


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


def _remove_deck_card(state: GameState, player: int, card_id: int) -> CardDefinition | None:
    deck = state.players[player].deck
    for idx, card in enumerate(deck):
        if card.id == card_id:
            return deck.pop(idx)
    return None


def _source_uid(trigger: PendingTrigger) -> str:
    return trigger.source_uid


def _move_card_to_hand(state: GameState, player: int, definition: CardDefinition, uid: str | None = None) -> HandCard:
    card = HandCard(definition=definition, uid=uid or _new_uid())
    state.players[player].hand.append(card)
    return card


def _do_draw(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    amount = int(_effect_value(trigger).get("amount", 1) or 1)
    for _ in range(max(0, amount)):
        draw_card(state, controller)


def _do_destroy(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    target_uid = _trigger_data(trigger).get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    player_idx, creature = found
    if not creature.can_be_destroyed():
        return
    move_battle_to_graveyard(state, player_idx, creature.uid, reason="effect")


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


def _do_put_to_mana(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    card_uid = data.get("card_uid")
    if card_uid:
        move_hand_to_mana(state, controller, card_uid)


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


def _do_discard(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    card_uid = data.get("card_uid")
    if card_uid:
        move_hand_to_graveyard(state, controller, card_uid, reason="effect")


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


def _do_power_modify(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    amount = int(effect.get("amount", 0) or 0)
    if amount == 0:
        return
    creature.power_modifiers.append(
        PowerModifier(
            source_uid=_source_uid(trigger),
            amount=amount,
            duration=effect.get("duration", "while_in_play"),
        )
    )


def _do_power_fix(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    fixed = effect.get("fixed_value")
    if fixed is None:
        fixed = effect.get("amount")
    if fixed is None:
        return
    creature.temp_flags["_power_fix"] = int(fixed)


def _set_creature_flag(state: GameState, trigger: PendingTrigger, flag: str) -> None:
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    creature.temp_flags[flag] = True


def _do_break_shield(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    target_player = data.get("target_player", 1 - controller)
    shield_uid = data.get("shield_uid")
    shield_index = data.get("shield_index")
    if shield_index is None and shield_uid is not None:
        shield = state.players[target_player].find_shield(shield_uid)
        if shield is None:
            return
        shield_index = next((i for i, s in enumerate(state.players[target_player].shield_zone) if s.uid == shield.uid), None)
    if shield_index is None:
        return
    if 0 <= shield_index < len(state.players[target_player].shield_zone):
        move_shield_to_standby(state, target_player, shield_index)


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


def _do_shuffle(state: GameState, controller: int) -> None:
    random.shuffle(state.players[controller].deck)


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


def _do_gr_summon(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    card_uid = data.get("card_uid")
    if card_uid:
        move_hand_to_battle(state, controller, card_uid)
        return
    definition = data.get("card_definition")
    if isinstance(definition, CardDefinition):
        _move_card_to_hand(state, controller, definition)


def _do_attach_seal(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    seal_def = data.get("seal_definition")
    found = _find_creature(state, target_uid)
    if not found or not isinstance(seal_def, CardDefinition):
        return
    _, creature = found
    creature.seals.append(seal_def)


def _do_remove_seal(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    if creature.seals:
        creature.seals.pop(0)


def _do_hyperize(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    found = _find_creature(state, data.get("creature_uid") or data.get("target_uid"))
    if not found:
        return
    _, creature = found
    creature.hyper_mode_released = True


def _do_awaken(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    awakened = data.get("awakened_face_definition")
    if not isinstance(awakened, CardDefinition):
        return
    found = _find_creature(state, data.get("target_uid") or data.get("creature_uid"))
    if not found:
        return
    _, creature = found
    awaken_psychic_creature(state, controller, creature.uid, awakened)


def _do_awaken_link(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    cell_uids = list(data.get("cell_uids") or [])
    super_def = data.get("super_creature_definition")
    if not isinstance(super_def, CardDefinition) or not cell_uids:
        return
    link_psychic_cells(state, controller, cell_uids, super_def, primary_uid=data.get("primary_uid"))


def _do_dragsolve(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    face_def = data.get("creature_face_definition")
    if not isinstance(face_def, CardDefinition):
        return
    found = _find_creature(state, data.get("target_uid") or data.get("creature_uid"))
    if not found:
        return
    _, creature = found
    dragsolve_dragheart(state, controller, creature.uid, face_def)


def _do_link_release(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid") or data.get("creature_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    creature.temp_flags["link_release"] = True


def _do_dragon_evasion(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    found = _find_creature(state, data.get("target_uid") or data.get("creature_uid"))
    if not found:
        return
    player_idx, creature = found
    move_battle_to_hyperspatial(state, player_idx, creature.uid, reason="dragon_evasion")


def _do_dragon_soul_evasion(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    found = _find_creature(state, data.get("target_uid") or data.get("creature_uid"))
    if not found:
        return
    player_idx, creature = found
    move_battle_to_hyperspatial(state, player_idx, creature.uid, reason="dragon_soul_evasion")


def _do_psychic_release(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    found = _find_creature(state, data.get("target_uid") or data.get("creature_uid"))
    if not found:
        return
    player_idx, creature = found
    move_battle_to_hyperspatial(state, player_idx, creature.uid, reason="psychic_release")


def _do_combine(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    king_def = data.get("king_creature_definition")
    cell_uids = list(data.get("cell_uids") or [])
    if not isinstance(king_def, CardDefinition) or not cell_uids:
        return
    combine_king_cells(state, controller, king_def, cell_uids)


def _store_temp_value(state: GameState, trigger: PendingTrigger, key: str) -> None:
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    creature.temp_flags[key] = True
