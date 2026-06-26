"""
engine/action_executor.py — apply one Action to a copied GameState.
"""

from __future__ import annotations

from core.actions import Action, actions_equal
from core.enums import ActionType, EffectType, Phase
from core.state import AttackContext, GameState, PendingTrigger
from engine.trigger_resolver import resolve_pending_triggers
from engine.action_generator import get_legal_actions
from engine.battle_resolver import resolve_battle
from engine.phase_controller import advance_phase
from engine.sba_checker import check_state_based_actions
from engine.shield_resolver import mark_direct_attack_if_applicable, resolve_shield_break_choice
from engine.zone_mover import (
    combine_king_cells,
    cross_gear_to_creature,
    fortify_shield_with_castle,
    move_hand_to_battle,
    move_hand_to_graveyard,
    move_hand_to_mana,
    move_standby_shield_to_hand,
    tap_mana_for_payment,
)


def _apply_twinpact_face(old_def, chars):
    """
    Create a new CardDefinition with the other face's characteristics.
    Used during Twinpact face selection (Rule 810.3).
    """
    from core.cards import CardDefinition as _CD
    return _CD(
        id=old_def.id,
        slug=old_def.slug,
        name=old_def.name,
        cost=chars["cost"],
        power=chars.get("power"),
        card_type=chars.get("card_type", old_def.card_type),
        card_subtype=chars.get("card_subtype", old_def.card_subtype),
        civilizations=chars.get("civilizations", old_def.civilizations),
        races=chars.get("races", old_def.races),
        keywords=chars.get("keywords", old_def.keywords),
        effects=old_def.effects,
        evolution_source_races=old_def.evolution_source_races,
        evolution_source_types=old_def.evolution_source_types,
        is_multiface=old_def.is_multiface,
        other_face_id=old_def.other_face_id,
        twinpact_other_face=old_def.twinpact_other_face,
    )


def execute_action(state: GameState, action: Action, db=None, validate: bool = True) -> GameState:
    """
    Apply one action and return a new GameState.

    This is the main `(GameState, Action) -> GameState` entry point. The
    implementation is intentionally incremental: complex effects are delegated
    to resolver modules as they are added.
    """
    if state.is_terminal():
        return state.copy()

    if validate and not _is_legal_action(state, action, db):
        raise ValueError(f"Illegal action for current state: {action}")

    s = state.copy()
    action_type = action.action_type

    if action_type == ActionType.PASS:
        step = action.get_extra().get("step")
        win = s.effect_stack.shield_break_window
        if step == "finish_shield_declarations" and win is not None:
            from engine.shield_break_window import finish_declarations, close_window_if_done
            finish_declarations(s)
            close_window_if_done(s)
            return check_state_based_actions(s)
        if step == "finish_shield_resolution" and win is not None:
            from engine.shield_break_window import close_window_if_done
            close_window_if_done(s)
            return check_state_based_actions(s)
        if step == "shield_to_hand" and s.effect_stack.shield_trigger_queue:
            shield_player, shield = s.effect_stack.shield_trigger_queue[0]
            move_standby_shield_to_hand(s, shield_player, shield.uid)
            return check_state_based_actions(s)
        if s.current_phase == Phase.BATTLE:
            return resolve_battle(s)
        if s.current_phase == Phase.DIRECT_ATTACK:
            mark_direct_attack_if_applicable(s)
        advance_phase(s, action)

    elif action_type == ActionType.CHARGE_MANA:
        _require_card_uid(action)
        move_hand_to_mana(s, action.player, action.card_uid)
        s.record_action(action_type, action.player, action.card_id)

    elif action_type == ActionType.SUMMON_CREATURE:
        tap_mana_for_payment(s, action.player, action.mana_used)
        hand_card = s.players[action.player].find_in_hand(action.card_uid or "") if action.card_uid else None
        orig_def = hand_card.definition if hand_card else None
        if action.card_uid:
            # Normal summon from hand
            creature = move_hand_to_battle(
                s,
                action.player,
                action.card_uid,
                evolution_base_uid=action.evolution_base_uid,
            )
        else:
            # GR summon from Ultra GR zone
            from engine.zone_mover import move_ultra_gr_to_battle
            gr_def = s.find_in_ultra_gr(action.player, action.card_id)
            if gr_def is None:
                raise ValueError(f"GR card {action.card_id} not found in Ultra GR zone for player {action.player}")
            creature = move_ultra_gr_to_battle(s, action.player, gr_def)
            orig_def = gr_def
        # Rule 810.3: Twinpact face selection — apply chosen face from original card text
        if action.twinpact_face != 0 and orig_def is not None:
            from core.cards import get_twinpact_characteristics
            chars = get_twinpact_characteristics(orig_def, action.twinpact_face)
            creature.definition = _apply_twinpact_face(orig_def, chars)
            creature.twinpact_face = action.twinpact_face
        s.record_action(action_type, action.player, action.card_id, creature.uid)

    elif action_type == ActionType.CAST_SPELL:
        _require_card_uid(action)
        tap_mana_for_payment(s, action.player, action.mana_used)
        # Look up the spell card definition before moving it to graveyard
        hand_card = s.players[action.player].find_in_hand(action.card_uid)
        spell_def = hand_card.definition if hand_card else None
        move_hand_to_graveyard(s, action.player, action.card_uid, reason="cast")
        s.record_action(action_type, action.player, action.card_id)
        
        # Fire ON_CAST trigger
        if hand_card is not None:
            from core.enums import TriggerEvent
            from engine.trigger_registry import fire_trigger
            fire_trigger(s, TriggerEvent.ON_CAST, {
                "source_uid": hand_card.uid,
                "source_card_id": hand_card.id,
                "controller": action.player,
                "zone": "graveyard",  # spell goes to graveyard after cast
            }, hand_card.uid)
        
        # Queue and resolve spell effects (rules 600-608)
        if spell_def is not None:
            spell_effects = [e for e in spell_def.effects
                             if e.effect_type == EffectType.SPELL]
            for effect in spell_effects:
                trigger = PendingTrigger(
                    effect=effect,
                    source_uid=action.card_uid,
                    source_card_id=action.card_id,
                    controller=action.player,
                )
                s.effect_stack.add_trigger(trigger)
            if s.effect_stack.pending_triggers:
                s = resolve_pending_triggers(s)

    elif action_type in (
        ActionType.GENERATE_CROSS_GEAR,
        ActionType.DEPLOY_FIELD,
        ActionType.EXECUTE_TAMASEED,
    ):
        _require_card_uid(action)
        tap_mana_for_payment(s, action.player, action.mana_used)
        battle_card = move_hand_to_battle(s, action.player, action.card_uid)
        battle_card.has_summoning_sickness = False
        s.record_action(action_type, action.player, action.card_id, battle_card.uid)

    elif action_type == ActionType.CROSS_GEAR:
        _require_card_uid(action)
        if not action.target_uid:
            raise ValueError("CROSS_GEAR requires target_uid")
        tap_mana_for_payment(s, action.player, action.mana_used)
        cross_gear_to_creature(s, action.player, action.card_uid, action.target_uid)
        s.record_action(action_type, action.player, action.card_id, action.target_uid)

    elif action_type == ActionType.FORTIFY_CASTLE:
        _require_card_uid(action)
        if not action.target_uid:
            raise ValueError("FORTIFY_CASTLE requires target_uid")
        tap_mana_for_payment(s, action.player, action.mana_used)
        fortify_shield_with_castle(s, action.player, action.card_uid, action.target_uid)
        s.record_action(action_type, action.player, action.card_id, action.target_uid)

    elif action_type == ActionType.COMBINE_KING_CREATURE:
        if action.card_id is None:
            raise ValueError("COMBINE_KING_CREATURE requires card_id")
        if db is None:
            raise ValueError("COMBINE_KING_CREATURE requires db")
        king_defn = db.require(action.card_id)
        tap_mana_for_payment(s, action.player, action.mana_used)
        creature = combine_king_cells(
            s, action.player, king_defn, list(action.selected_uids)
        )
        s.record_action(action_type, action.player, action.card_id, creature.uid)

    elif action_type == ActionType.USE_G_ZERO:
        _require_card_uid(action)
        creature = move_hand_to_battle(s, action.player, action.card_uid)
        s.record_action(action_type, action.player, action.card_id, creature.uid)

    elif action_type in (ActionType.ATTACK_PLAYER, ActionType.ATTACK_CREATURE):
        _declare_attack(s, action)

    elif action_type in (ActionType.DECLARE_BLOCKER, ActionType.DECLARE_GUARDMAN):
        _declare_block(s, action)

    elif action_type == ActionType.USE_SHIELD_TRIGGER:
        _resolve_shield_trigger_choice(s, action)

    elif action_type == ActionType.SELECT_YES_NO:
        _resolve_select_yes_no(s, action)

    elif action_type == ActionType.USE_G_STRIKE:
        _resolve_g_strike_choice(s, action)

    elif action_type == ActionType.USE_S_BACK:
        _resolve_s_back(s, action)

    elif action_type == ActionType.USE_SABAKI_Z:
        _resolve_sabaki_z(s, action)

    elif action_type == ActionType.SELECT_ATTACK_ORDER:
        if action.shield_index is None:
            raise ValueError("SELECT_ATTACK_ORDER requires shield_index")
        return resolve_shield_break_choice(s, action.shield_index)

    elif action_type == ActionType.USE_NINJA_STRIKE:
        _require_card_uid(action)
        creature = move_hand_to_battle(s, action.player, action.card_uid)
        if s.attack_context:
            s.attack_context.ninja_strike_used = True
            s.attack_context.ninja_strike_card_uid = creature.uid
        s.record_action(action_type, action.player, action.card_id, creature.uid)

    elif action_type == ActionType.USE_ATTACK_CHANCE:
        # Rule 112.3f: Attack Chance — cast spell for free when creature attacks
        _require_card_uid(action)
        # Move spell from hand to graveyard (cast)
        hand_card = s.players[action.player].find_in_hand(action.card_uid)
        spell_def = hand_card.definition if hand_card else None
        move_hand_to_graveyard(s, action.player, action.card_uid, reason="cast")
        s.record_action(action_type, action.player, action.card_id)
        # Queue and resolve spell effects (free, no mana needed)
        if spell_def is not None:
            spell_effects = [e for e in spell_def.effects
                             if e.effect_type == EffectType.SPELL]
            for effect in spell_effects:
                trigger = PendingTrigger(
                    effect=effect,
                    source_uid=action.card_uid,
                    source_card_id=action.card_id,
                    controller=action.player,
                )
                s.effect_stack.add_trigger(trigger)
            if s.effect_stack.pending_triggers:
                s = resolve_pending_triggers(s)

    elif action_type == ActionType.USE_OVER_DRIVE:
        _require_card_uid(action)
        if action.mana_used:
            tap_mana_for_payment(s, action.player, action.mana_used)
        creature = s.players[action.player].find_creature(action.card_uid or "")
        if creature is not None:
            creature.temp_flags["over_drive_used"] = True
            creature.temp_flags["over_drive_active"] = True
        s.record_action(action_type, action.player, action.card_id, action.card_uid)

    elif action_type == ActionType.ACTIVATE_ABILITY:
        s = _execute_activated_ability(s, action)

    elif action_type == ActionType.HYPERIZE:
        creature = s.players[action.player].find_creature(action.card_uid or "")
        if creature is None:
            raise ValueError("Hyperize source not found")
        creature.hyper_mode_released = True
        s.record_action(action_type, action.player, action.card_id, creature.uid)

    else:
        # Selection and advanced free-execution actions are handled by effect
        # and shield resolvers as those systems are filled in.
        s.record_action(action_type, action.player, action.card_id, action.target_uid)

    return check_state_based_actions(s)


def _is_legal_action(state: GameState, action: Action, db=None) -> bool:
    return any(actions_equal(action, legal) for legal in get_legal_actions(state, db))


def _require_card_uid(action: Action) -> None:
    if not action.card_uid:
        raise ValueError(f"{action.action_type.value} requires card_uid")


def _execute_activated_ability(state: GameState, action: Action) -> GameState:
    """
    Execute an ACTIVATE_ABILITY action: pay costs, then queue the activated
    ability's effects as PendingTriggers on the EffectStack for resolution.
    """
    extra = dict(action.extra)
    ability_index = extra.get("ability_index", 0)
    tap_source = extra.get("tap_source", False)
    discard_uid = extra.get("discard_uid")

    # ── Pay mana cost ────────────────────────────────────────────────────
    if action.mana_used:
        tap_mana_for_payment(state, action.player, action.mana_used)

    # ── Tap source card if required ──────────────────────────────────────
    if tap_source:
        source = state.players[action.player].find_creature(action.card_uid or "")
        if source is None:
            # Try mana zone / shield zone
            source = state.players[action.player].find_mana(action.card_uid or "")
        if source is not None and hasattr(source, 'tap'):
            source.tap()

    # ── Pay discard cost ─────────────────────────────────────────────────
    if discard_uid is not None:
        move_hand_to_graveyard(state, action.player, discard_uid, reason="activated ability cost")

    # ── Queue activated ability effects on the EffectStack ──────────────
    source_card = state.players[action.player].find_creature(action.card_uid or "")
    if source_card is None:
        # Try mana zone
        source_card = state.players[action.player].find_mana(action.card_uid or "")
    if source_card is not None:
        defn = source_card.definition
        activated_effects = defn.get_activated_effects()
        if ability_index < len(activated_effects):
            effect = activated_effects[ability_index]
            trigger = PendingTrigger(
                effect=effect,
                source_uid=action.card_uid or "",
                source_card_id=action.card_id or 0,
                controller=action.player,
            )
            state.effect_stack.add_trigger(trigger)
            if state.effect_stack.pending_triggers:
                state = resolve_pending_triggers(state)

    state.record_action(
        action.action_type, action.player, action.card_id, action.card_uid
    )
    return state


def _declare_attack(state: GameState, action: Action) -> None:
    attacker = state.players[action.player].find_creature(action.card_uid or "")
    if attacker is None:
        raise ValueError("Attacking creature not found")
    attacker.tap()
    attacker.has_attacked_this_turn = True

    # Fire ON_ATTACK trigger
    from core.enums import TriggerEvent
    from engine.trigger_registry import fire_trigger
    fire_trigger(state, TriggerEvent.ON_ATTACK, {
        "source_uid": attacker.uid,
        "source_card_id": attacker.id,
        "controller": action.player,
        "target_type": "player" if action.action_type == ActionType.ATTACK_PLAYER else "creature",
        "target_uid": action.target_uid,
    }, attacker.uid)

    target_type = "player" if action.action_type == ActionType.ATTACK_PLAYER else "creature"
    state.attack_context = AttackContext(
        attacker_uid=attacker.uid,
        attacker_player=action.player,
        target_type=target_type,
        target_uid=action.target_uid,
    )
    state.turn_info.phase = Phase.ATTACK_DECLARE
    state.record_action(action.action_type, action.player, action.card_id, action.target_uid)


def _declare_block(state: GameState, action: Action) -> None:
    if state.attack_context is None:
        raise ValueError("No attack is in progress")
    blocker = state.players[action.player].find_creature(action.card_uid or "")
    if blocker is None:
        raise ValueError("Blocking creature not found")
    blocker.tap()
    blocker.is_blocking = True
    blocker.blocking_uid = state.attack_context.attacker_uid
    state.attack_context.blocker_uid = blocker.uid
    state.attack_context.blocker_player = action.player
    state.attack_context.block_was_declared = True
    state.turn_info.phase = Phase.BATTLE
    state.record_action(action.action_type, action.player, action.card_id, blocker.uid)

    # Fire ON_BLOCK trigger
    from core.enums import TriggerEvent
    from engine.trigger_registry import fire_trigger
    fire_trigger(state, TriggerEvent.ON_BLOCK, {
        "source_uid": blocker.uid,
        "source_card_id": blocker.id,
        "controller": action.player,
        "attacker_uid": state.attack_context.attacker_uid,
    }, blocker.uid)


def _resolve_shield_trigger_choice(state: GameState, action: Action) -> None:
    if not action.card_uid:
        raise ValueError("Shield trigger action requires card_uid")

    win = state.effect_stack.shield_break_window
    if win is not None and win.phase == "declare":
        if action.choice:
            win.declared_s_triggers.add(action.card_uid)
        state.record_action(action.action_type, action.player, action.card_id)
        return

    if win is not None and win.phase == "resolve":
        from engine.shield_break_window import close_window_if_done, execute_free_from_hand
        execute_free_from_hand(
            state, action.player, action.card_uid, reason="shield_trigger",
        )
        win.resolved_keys.add(f"s_trigger:{action.card_uid}:")
        close_window_if_done(state)
        state.record_action(action.action_type, action.player, action.card_id)
        return

    if not action.choice:
        move_standby_shield_to_hand(state, action.player, action.card_uid)
        state.record_action(action.action_type, action.player, action.card_id)
        return

    for idx, (queued_player, shield) in enumerate(state.effect_stack.shield_trigger_queue):
        if queued_player == action.player and shield.uid == action.card_uid:
            state.effect_stack.shield_trigger_queue.pop(idx)
            from engine.shield_break_window import execute_free_from_hand
            execute_free_from_hand(
                state, action.player, shield.uid, reason="shield_trigger",
            )
            state.record_action(action.action_type, action.player, action.card_id)
            return
    raise ValueError(f"Standby shield {action.card_uid} not found")


def _resolve_g_strike_choice(state: GameState, action: Action) -> None:
    if not action.card_uid:
        raise ValueError("G-Strike action requires card_uid")

    win = state.effect_stack.shield_break_window
    if win is not None and win.phase == "declare":
        win.declared_g_strikes.add(action.card_uid)
        state.record_action(action.action_type, action.player, action.card_id)
        return

    if win is not None and win.phase == "resolve":
        win.resolved_keys.add(f"g_strike:{action.card_uid}:")
        from engine.shield_break_window import close_window_if_done
        close_window_if_done(state)
        state.record_action(action.action_type, action.player, action.card_id)
        return

    move_standby_shield_to_hand(state, action.player, action.card_uid)
    state.record_action(action.action_type, action.player, action.card_id)


def _resolve_s_back(state: GameState, action: Action) -> None:
    if not action.card_uid or not action.discard_uid:
        raise ValueError("S-Back action requires card_uid and discard_uid")

    win = state.effect_stack.shield_break_window
    if win is not None and win.phase == "declare":
        pair = (action.card_uid, action.discard_uid)
        if pair not in win.declared_s_backs:
            win.declared_s_backs.append(pair)
        state.record_action(action.action_type, action.player, action.card_id)
        return

    if win is not None and win.phase == "resolve":
        from engine.shield_break_window import close_window_if_done, execute_free_from_hand
        execute_free_from_hand(
            state, action.player, action.card_uid, reason="s_back",
        )
        win.resolved_keys.add(f"s_back:{action.card_uid}:{action.discard_uid}")
        close_window_if_done(state)
        state.record_action(action.action_type, action.player, action.card_id)
        return

    shield = None
    for idx, (queued_player, queued_shield) in enumerate(state.effect_stack.shield_trigger_queue):
        if queued_player == action.player and queued_shield.uid == action.discard_uid:
            shield = queued_shield
            state.effect_stack.shield_trigger_queue.pop(idx)
            break
    if shield is None:
        raise ValueError(f"Standby shield {action.discard_uid} not found")

    from core.zones import GraveyardCard
    state.players[action.player].graveyard.insert(
        0,
        GraveyardCard(
            definition=shield.definition,
            uid=shield.uid,
            died_from="s_back_discard",
            died_on_turn=state.turn_number,
            treat_as_hand_discard=True,   # Rule 509.5c
        ),
    )

    s_back_card = state.players[action.player].find_in_hand(action.card_uid)
    if s_back_card is None:
        raise ValueError(f"S-Back card {action.card_uid} not found in hand")
    if s_back_card.definition.is_creature():
        creature = move_hand_to_battle(state, action.player, action.card_uid)
        state.record_action(action.action_type, action.player, action.card_id, creature.uid)
    else:
        move_hand_to_graveyard(state, action.player, action.card_uid, reason="s_back")
        state.record_action(action.action_type, action.player, action.card_id)


def _resolve_sabaki_z(state: GameState, action: Action) -> None:
    """Rule 112.3d: discard Emblem of Judgment from hand to free-execute Sabaki Z."""
    if not action.card_uid or not action.discard_uid:
        raise ValueError("Sabaki Z action requires card_uid and discard_uid")

    from engine.shield_break_window import close_window_if_done, discard_hand_card, execute_free_from_hand

    discard_hand_card(state, action.player, action.discard_uid, reason="sabaki_z_discard")
    execute_free_from_hand(
        state, action.player, action.card_uid, reason="sabaki_z",
    )

    win = state.effect_stack.shield_break_window
    if win is not None:
        win.resolved_keys.add(f"sabaki_z:{action.card_uid}:{action.discard_uid}")
        close_window_if_done(state)

    state.record_action(action.action_type, action.player, action.card_id, action.target_uid)


def _resolve_select_yes_no(state: GameState, action: Action) -> None:
    """Handle SELECT_YES_NO actions, including Silent Skill context."""
    extra = dict(action.extra or ())
    context = extra.get("context", "")
    if context == "silent_skill" and action.choice:
        # Player chose to keep this creature tapped for Silent Skill
        creature = state.players[action.player].find_creature(action.card_uid or "")
        if creature:
            creature.temp_flags["silent_skill_skip_untap"] = True
