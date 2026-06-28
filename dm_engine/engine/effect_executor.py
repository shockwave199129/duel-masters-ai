"""engine/effect_executor.py — execute parsed card effects incrementally."""

from __future__ import annotations

import logging
import random

from core.cards import CardDefinition, CardEffect, is_hyper_mode
from core.enums import EffectAction, CardSubtype
from core.state import AwaitedChoice, GameState, PendingTrigger
from core.zones import Creature, HandCard, ManaCard, ShieldCard, PowerModifier
from engine.god_manager import GodManager
from engine.sba_checker import check_state_based_actions
from engine.zone_mover import (
    _new_uid,
    combine_king_cells,
    cross_gear_to_creature,
    draw_card,
    dragsolve_dragheart,
    awaken_psychic_creature,
    flip_twinpact,
    flip_forbidden,
    fortify_shield_with_castle,
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

logger = logging.getLogger(__name__)



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

    # ── RAG fallback for low-confidence parses (rule knowledge) ─────────────
    # If the LLM parser was uncertain, try to find a rule citation from the
    # ChromaDB knowledge base. This attaches metadata to the action's audit
    # trail so downstream consumers can flag low-confidence resolutions.
    if effect.needs_rag_fallback():
        logger.info(
            "RAG fallback: Card %s ability %d has low confidence (%.2f); "
            "no RAG DB configured — skipping rule lookup.",
            effect.card_id, effect.ability_index, effect.parse_confidence,
        )

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
        _do_evolve(s, controller, trigger)
    elif action == EffectAction.CROSS_GEAR:
        _do_cross_gear(s, controller, trigger)
    elif action == EffectAction.GOD_LINK:
        _do_god_link(s, controller, trigger)
    elif action == EffectAction.FORTIFY:
        _do_fortify(s, controller, trigger)
    elif action == EffectAction.DEPLOY_FIELD:
        _do_deploy_field(s, controller, trigger)
    elif action == EffectAction.SWAP_ZONES:
        _do_swap_zones(s, controller, trigger)
    elif action == EffectAction.TURN_UPSIDE_DOWN:
        _do_turn_upside_down(s, controller, trigger)
    elif action == EffectAction.FORBIDDEN_EXPLOSION:
        _do_forbidden_explosion(s, controller, trigger)
    # ── Defensive / Offensive (Tier 3 / TODO 11) ────────────────────────────────
    elif action == EffectAction.PROTECTION:
        _do_protection(s, controller, trigger)
    elif action == EffectAction.GAIN_CONTROL:
        _do_gain_control(s, controller, trigger)
    # ── Field state (Tier 3 / TODO 12) ──────────────────────────────────────────
    elif action == EffectAction.ZEROM_BIRTH:
        _do_zerom_birth(s, controller, trigger)
    elif action == EffectAction.SHIELDIFY:
        _do_shieldify(s, controller, trigger)
    # ── Mandatory actions (Tier 3 / TODO 13) ────────────────────────────────────
    elif action == EffectAction.MUST_ATTACK:
        _do_must_attack(s, controller, trigger)
    elif action == EffectAction.MUST_BLOCK:
        _do_must_block(s, controller, trigger)
    elif action == EffectAction.CANNOT_BLOCK:
        _do_cannot_block(s, controller, trigger)
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


# ── Re-exports from engine.cards.effect_actions subpackage ────────────────
from engine.cards.effect_actions import (  # noqa: E402
    _do_add_to_hand,
    _do_attach_seal,
    _do_awaken,
    _do_awaken_link,
    _do_banish_to_abyss,
    _do_break_shield,
    _do_cannot_block,
    _do_combine,
    _do_cross_gear,
    _do_deploy_field,
    _do_destroy,
    _do_discard,
    _do_dragon_evasion,
    _do_dragon_soul_evasion,
    _do_draw,
    _do_evolve,
    _do_forbidden_explosion,
    _do_forbidden_flip,
    _do_forbidden_release,
    _do_fortify,
    _do_gain_control,
    _do_give_keyword,
    _do_god_link,
    _do_gr_summon,
    _do_hyperize,
    _do_link_release,
    _do_look_at_top,
    _do_lose_by_effect,
    _do_move_zone,
    _do_must_attack,
    _do_must_block,
    _do_neo_evolve,
    _do_power_fix,
    _do_power_modify,
    _do_protection,
    _do_psychic_release,
    _do_put_to_battle_zone,
    _do_put_to_mana,
    _do_put_to_shield,
    _do_remove_seal,
    _do_reveal,
    _do_return_to_hand,
    _do_search_deck,
    _do_shieldify,
    _do_shuffle,
    _store_temp_value,
    _do_summon_free,
    _do_swap_zones,
    _do_tap,
    _do_turn_upside_down,
    _do_twinpact_flip,
    _do_untap,
    _do_win_by_effect,
    _do_zerom_birth,
    _do_zerom_ritual,
    _set_creature_flag,
)
