"""engine/effect_executor.py — execute parsed card effects incrementally."""

from __future__ import annotations

import random

from core.cards import CardDefinition, is_hyper_mode
from core.enums import EffectAction
from core.state import AwaitedChoice, GameState, PendingTrigger
from core.zones import Creature, HandCard, ManaCard, ShieldCard, PowerModifier
from engine.sba_checker import check_state_based_actions
from engine.zone_mover import (
    _new_uid,
    combine_king_cells,
    draw_card,
    dragsolve_dragheart,
    awaken_psychic_creature,
    flip_twinpact,
    flip_forbidden,
    link_psychic_cells,
    move_battle_to_graveyard,
    move_battle_to_hyperspatial,
    move_hand_to_battle,
    move_zerom_to_battle,
    move_hand_to_graveyard,
    move_hand_to_mana,
    move_hand_to_shield,
    move_shield_to_standby,
    move_ultra_gr_to_battle,
    swap_hyper_mode,
    tap_mana_for_payment,
)


def _effect_needs_choice(effect: CardEffect, trigger_data: dict) -> str | None:
    """
    Determine whether an effect requires player input before it can resolve.

    Returns the choice_type string if a choice is needed, or None if the
    effect can auto-resolve.

    Choice detection rules:
      - is_optional effects → "yes_no" (use it or not)
      - effect_target with target selection → "select_target"
      - effect_value with "requires_choice": true → "yes_no"
      - effect_value with card selection → "select_card"
      - effect_value with mana re-selection → "select_mana"
    """
    # Optional effects ask "do you want to use this?"
    if effect.is_optional:
        return "yes_no"

    # Explicit requires_choice flag in effect_value
    ev = effect.effect_value or {}
    if ev.get("requires_choice") is True:
        return "yes_no"

    # Target selection needed
    et = effect.effect_target or {}
    if et.get("type") in ("creature", "shield", "card", "player"):
        return "select_target"

    # Card selection needed (e.g. "choose a card from hand")
    if "card_uid" in ev and ev.get("from_zone"):
        return "select_card"

    # Mana re-selection needed
    if "select_mana" in ev:
        return "select_mana"

    return None


def execute_pending_trigger(state: GameState, trigger: PendingTrigger) -> GameState:
    """Execute one pending trigger and run SBAs after it resolves.

    If the effect requires a player choice (target selection, yes/no, etc.),
    an AwaitedChoice is set on the effect stack and the trigger is NOT
    resolved — the caller must stop processing further triggers.
    
    Rule 101.4d: Set currently_resolving_effect flag during execution so that
    any triggers fired during this effect's resolution are queued but not
    immediately executed (they enter "standby" state).
    """
    s = state.copy()
    
    # Mark that we're starting to resolve an effect (rule 101.4d)
    s.currently_resolving_effect = True
    
    effect = trigger.effect
    controller = trigger.controller

    # Check if this effect needs a player choice before resolving
    choice_type = _effect_needs_choice(effect, trigger.trigger_data)
    if choice_type is not None:
        valid_options: list = []
        min_choices = 1

        if choice_type == "yes_no":
            valid_options = [True, False]
        elif choice_type == "select_target":
            et = effect.effect_target or {}
            zone = et.get("zone", "battle_zone")
            valid_options = _collect_target_options(s, controller, zone, et)
        elif choice_type == "select_card":
            ev = effect.effect_value or {}
            from_zone = ev.get("from_zone", "hand")
            valid_options = _collect_card_options(s, controller, from_zone)
        elif choice_type == "select_mana":
            # For mana selection, options are generated dynamically by
            # _generate_choice_actions based on current mana zone state.
            valid_options = ["mana_combo"]  # placeholder; action_generator expands

        s.effect_stack.set_choice(AwaitedChoice(
            choice_type=choice_type,
            player=controller,
            effect=effect,
            source_uid=trigger.source_uid,
            valid_options=valid_options,
            min_choices=min_choices,
            prompt=effect.raw_text or f"Choose for {effect.effect_action.value}",
        ))
        # Paused — do not execute or pop further triggers
        # Note: currently_resolving_effect is still True, preventing interruptions
        return s

    action = effect.effect_action

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
    elif action == EffectAction.ZEROM_RITUAL:
        _do_zerom_ritual(s, controller, trigger)
    elif action == EffectAction.ZEROM_FLIP:
        _do_zerom_ritual(s, controller, trigger)
    elif action == EffectAction.TWINPACT_FLIP:
        _do_twinpact_flip(s, trigger)
    elif action == EffectAction.FORBIDDEN_FLIP:
        _do_forbidden_flip(s, trigger)
    # ── Forbidden Release (Rule 809) ──────────────────────────────────────────
    elif action == EffectAction.FORBIDDEN_RELEASE:
        _do_forbidden_release(s, controller, trigger)
    # ── NEO Evolution (Rule 802) ──────────────────────────────────────────────
    elif action == EffectAction.NEO_EVOLVE:
        _do_neo_evolve(s, controller, trigger)
    # ── Win/Loss by card effect (Rule 104.2c) ────────────────────────────────
    elif action == EffectAction.WIN_CONDITION:
        _do_win_by_effect(s, controller, trigger)
    elif action == EffectAction.LOSE_CONDITION:
        _do_lose_by_effect(s, controller, trigger)
    # ── Zone operations (Tier 3 / TODO 10) ──────────────────────────────────────
    elif action == EffectAction.EVOLVE:
        pass  # TODO: implement evolution mechanic (rules 701.15, 801)
    elif action == EffectAction.CROSS_GEAR:
        pass  # TODO: implement Cross Gear attachment (rules 701.17, 303)
    elif action == EffectAction.GOD_LINK:
        pass  # TODO: implement God link (rules 701.18, 804)
    elif action == EffectAction.FORTIFY:
        pass  # TODO: implement fortify (rules 701.19, 304)
    elif action == EffectAction.DEPLOY_FIELD:
        pass  # TODO: implement Field deployment (rules 701.27, 308)
    elif action == EffectAction.SWAP_ZONES:
        pass  # TODO: implement zone swap (rule 701.26)
    elif action == EffectAction.TURN_UPSIDE_DOWN:
        pass  # TODO: implement Field flip (rule 701.28)
    elif action == EffectAction.FORBIDDEN_EXPLOSION:
        pass  # TODO: implement Final Forbidden flip (rule 701.29)
    # ── Defensive / Offensive (Tier 3 / TODO 11) ────────────────────────────────
    elif action == EffectAction.PROTECTION:
        pass  # TODO: implement protection effect
    elif action == EffectAction.GAIN_CONTROL:
        pass  # TODO: implement gain control effect
    # ── Field state (Tier 3 / TODO 12) ──────────────────────────────────────────
    elif action == EffectAction.ZEROM_BIRTH:
        pass  # TODO: implement Zerom birth (rule 701.31)
    elif action == EffectAction.SHIELDIFY:
        pass  # TODO: implement shieldify (rule 701.32)
    # ── Mandatory actions (Tier 3 / TODO 13) ────────────────────────────────────
    elif action == EffectAction.MUST_ATTACK:
        pass  # TODO: implement must-attack effect
    elif action == EffectAction.MUST_BLOCK:
        pass  # TODO: implement must-block effect
    elif action == EffectAction.CANNOT_BLOCK:
        pass  # TODO: implement cannot-block effect
    # EffectAction.NONE and unknown values intentionally no-op.

    # Clear the "resolving effect" flag and run SBAs (rule 101.4d)
    s.currently_resolving_effect = False
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


def _collect_target_options(
    state: GameState, player: int, zone: str, et: dict
) -> list[str]:
    """Collect valid target uids for a select_target choice from the given zone."""
    targets: list[str] = []
    if zone == "battle_zone":
        for p in state.players:
            for creature in p.battle_zone:
                targets.append(creature.uid)
    elif zone == "shield_zone":
        for p in state.players:
            for shield in p.shield_zone:
                targets.append(shield.uid)
    elif zone == "mana_zone":
        for p in state.players:
            for mana in p.mana_zone:
                targets.append(mana.uid)
    elif zone == "hand":
        p = state.players[player]
        for card in p.hand:
            targets.append(card.uid)
    return targets


def _collect_card_options(state: GameState, player: int, from_zone: str) -> list[str]:
    """Collect valid card uids for a select_card choice from the given zone."""
    p = state.players[player]
    if from_zone == "hand":
        return [c.uid for c in p.hand]
    elif from_zone == "deck":
        return [c.uid for c in p.deck]
    elif from_zone == "graveyard":
        return [c.uid for c in p.graveyard]
    return []


def _move_card_to_hand(state: GameState, player: int, definition: CardDefinition, uid: str | None = None) -> HandCard:
    card = HandCard(definition=definition, uid=uid or _new_uid())
    state.players[player].hand.append(card)
    return card


def _do_draw(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    amount = int(_effect_value(trigger).get("amount", 1) or 1)
    for _ in range(max(0, amount)):
        draw_card(state, controller)


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
    source_card_id = data.get("source_card_id")

    # Check if the card is in the Ultra GR zone
    if source_card_id is not None:
        ultra_gr_def = state.find_in_ultra_gr(controller, source_card_id)
        if ultra_gr_def is not None:
            move_ultra_gr_to_battle(state, controller, ultra_gr_def)
            return

    # Otherwise, summon from hand (existing behavior)
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
    if creature.hyper_mode_released:
        # Already released — nothing to do
        return
    if not is_hyper_mode(creature.definition):
        # Not a Hyper Mode creature — just set flag as fallback
        creature.hyper_mode_released = True
        return
    # Remove old static effects before swapping definition
    creature.remove_static_effects(state)
    # Perform the card definition swap
    swap_hyper_mode(creature)
    # Re-apply static effects from the released face
    creature.apply_static_effects(state)


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


def _do_zerom_ritual(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle Zerom ritual cast: remove the Zerom from its current zone,
    create a creature with _zerom_flipped flag, and place it in the battle zone.
    (Rule 812)
    """
    data = _trigger_data(trigger)
    source_uid = data.get("target_uid") or trigger.source_uid
    if not source_uid:
        return

    # Find and remove the Zerom card from its current zone
    p_state = state.players[controller]
    hand_card = p_state.find_in_hand(source_uid)
    if hand_card is not None:
        p_state.hand.remove(hand_card)
        card_def = hand_card.definition
    else:
        # Check mana zone (in case Zerom was charged)
        mana_card = p_state.find_mana(source_uid)
        if mana_card is not None:
            p_state.mana_zone.remove(mana_card)
            card_def = mana_card.definition
        else:
            return  # card not found in any playable zone

    # Use the creature face definition if available, otherwise the card itself
    creature_def = card_def  # Zerom card def IS the creature face
    move_zerom_to_battle(state, controller, creature_def)


def _store_temp_value(state: GameState, trigger: PendingTrigger, key: str) -> None:
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    creature.temp_flags[key] = True


# ── Twinpact / Forbidden flip handlers (Phase 5A) ─────────────────────────────

def _do_twinpact_flip(state: GameState, trigger: PendingTrigger) -> None:
    """
    Handle TWINPACT_FLIP effect: flip a Twinpact creature to its other face.

    The trigger data should contain the target creature's uid.
    """
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid") or data.get("creature_uid")
    if not target_uid:
        return
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    flip_twinpact(creature)


def _do_forbidden_flip(state: GameState, trigger: PendingTrigger) -> None:
    """
    Handle FORBIDDEN_FLIP effect: flip a Forbidden card's face when leaving battle zone.

    The trigger data should contain the target creature's uid.
    """
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid") or data.get("creature_uid")
    if not target_uid:
        return
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    flip_forbidden(creature)


def _do_win_by_effect(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle WIN_CONDITION effect: the controller wins the game (Rule 104.2c).
    
    Rule 104.2c: If a player meets both a win and lose condition simultaneously,
    the player wins.
    """
    state.game_result = ("win", controller)


def _do_lose_by_effect(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle LOSE_CONDITION effect: the controller loses the game (Rule 104.2c).
    """
    opponent = 1 - controller
    state.game_result = ("win", opponent)


def _do_forbidden_release(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle FORBIDDEN_RELEASE effect (Rule 809).
    
    A Forbidden card in hand is flipped and summoned to the battle zone.
    The trigger data should contain the hand card's uid.
    """
    data = _trigger_data(trigger)
    hand_uid = data.get("hand_uid") or data.get("source_uid")
    if not hand_uid:
        return
    
    # Find the card in hand
    p_state = state.players[controller]
    hand_card = None
    for hc in p_state.hand:
        if hc.uid == hand_uid:
            hand_card = hc
            break
    if hand_card is None:
        return
    
    # Remove from hand, flip, and place in battle zone
    p_state.hand.remove(hand_card)
    creature = move_hand_to_battle(
        state, controller, hand_card.uid, hand_card.definition.id,
        is_forbidden_release=True,
    )
    if creature:
        flip_forbidden(creature)


def _do_neo_evolve(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle NEO_EVOLVE effect (Rule 802).
    
    A NEO creature in the battle zone activates its evolution ability
    to place a new evolution stack entry (evolve in place).
    """
    data = _trigger_data(trigger)
    creature_uid = data.get("source_uid") or data.get("creature_uid")
    if not creature_uid:
        return
    
    found = _find_creature(state, creature_uid)
    if not found:
        return
    _, creature = found
    
    # Add a new evolution stack entry from hand
    p_state = state.players[controller]
    evolve_card_uid = data.get("evolve_card_uid")
    if evolve_card_uid:
        for hc in p_state.hand:
            if hc.uid == evolve_card_uid:
                p_state.hand.remove(hc)
                creature.evolution_stack.append(hc.definition)
                break
