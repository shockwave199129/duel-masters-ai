"""
engine/action_executor.py — apply one Action to a copied GameState.
"""

from __future__ import annotations

from core.zones import HandCard
from core.actions import Action, actions_equal
from core.card_scripts import COUNTERATTACK, OPPONENT_HAND_REVEALED
from core.continuous_rules import controls
from core.enums import ActionType, CardSubtype, EffectType, Keyword, Phase
from core.state import AttackContext, GameState, PendingTrigger
from engine.trigger_resolver import resolve_pending_triggers
from engine.effect_executor import resolve_choice
from engine.entry_rules import tap_new_mana
from engine.action_generator import get_legal_actions
from engine.battle_resolver import resolve_battle
from engine.phase_controller import advance_phase
from engine.sba_checker import check_state_based_actions
from engine.shield_resolver import mark_direct_attack_if_applicable, resolve_shield_break_choice
from engine.zone_mover import (
    combine_king_cells,
    cross_gear_to_creature,
    fortify_shield_with_castle,
    leave_battle_zone,
    move_battle_to_graveyard,
    move_hand_to_battle,
    move_hand_to_field,
    move_hand_to_graveyard,
    move_hand_to_mana,
    move_hand_to_shield,
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


def _stage_for_summon(state: GameState, player: int, card_uid: str, zone: str) -> None:
    p_state = state.players[player]
    cards = p_state.graveyard if zone == "graveyard" else p_state.mana_zone
    card = next((c for c in cards if c.uid == card_uid), None)
    if card is None:
        raise ValueError(f"Card {card_uid} is not in the {zone}")
    cards.remove(card)
    p_state.hand.append(HandCard(definition=card.definition, uid=card.uid))


_CHOICE_ANSWERS = frozenset({
    ActionType.PASS,
    ActionType.SELECT_YES_NO,
    ActionType.SELECT_TARGET,
    ActionType.SELECT_CARD,
    ActionType.SELECT_MANA,
})


def execute_action(state: GameState, action: Action, db=None, validate: bool = True) -> GameState:
    """
    Execute one legal action and return the new state.

    Triggers queued by the action, including those from state-based actions,
    resolve before this returns, unless an effect needs a player choice.
    """
    mana_before = [{m.uid for m in p.mana_zone} for p in state.players]
    result = _apply_action(state, action, db=db, validate=validate)
    if (
        result.effect_stack.pending_triggers
        and not result.effect_stack.is_waiting_for_choice()
        and not result.is_terminal()
    ):
        result = resolve_pending_triggers(result)
    end_attack_if_removed(result)
    tap_new_mana(mana_before, result)
    # Before and after, so a card that leaves a public zone is not missed
    remember_public_cards(state, result)
    remember_public_cards(result, result)
    return result


def remember_public_cards(seen_in: GameState, state: GameState) -> None:
    """Record in ``state`` every card that both players can see in ``seen_in``."""
    public = state.public_uids
    for player, p_state in enumerate(seen_in.players):
        for creature in p_state.battle_zone:
            public.add(creature.uid)
            public.update(entry.uid for entry in creature.evolution_stack)
        public.update(card.uid for card in p_state.mana_zone)
        public.update(card.uid for card in p_state.graveyard)
        public.update(card.uid for card in p_state.resolving)
        public.update(shield.uid for shield in p_state.shield_zone if shield.is_face_up)
        # A hand the opponent is being shown
        if controls(seen_in, 1 - player, OPPONENT_HAND_REVEALED):
            public.update(card.uid for card in p_state.hand)


def end_attack_if_removed(state: GameState) -> None:
    """
    An attack ends at once if the attacker, or the creature being attacked,
    leaves it (rules 505.6, 505.6a).
    """
    ctx = state.attack_context
    if ctx is None or state.effect_stack.is_waiting_for_choice() or state.current_phase not in (
        Phase.ATTACK_DECLARE, Phase.BLOCK_DECLARE, Phase.BATTLE, Phase.DIRECT_ATTACK,
    ):
        return
    target_gone = (
        ctx.is_attacking_creature and not ctx.block_was_declared
        and state.find_creature_anywhere(ctx.target_uid or "") is None
    )
    if state.find_creature_anywhere(ctx.attacker_uid) is None or target_gone:
        state.turn_info.phase = Phase.END_OF_ATTACK


def _apply_action(state: GameState, action: Action, db=None, validate: bool = True) -> GameState:
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

    # An awaited choice takes priority over the normal meaning of the action
    choice = s.effect_stack.awaited_choice
    if choice is not None and action.player == choice.player and action_type in _CHOICE_ANSWERS:
        return resolve_choice(s, action)

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
        ctx = s.attack_context
        if (s.current_phase == Phase.BLOCK_DECLARE and ctx is not None
                and ctx.blocker_uid and not ctx.block_finished):
            _finish_block(s)  # no more blockers
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
        for uid in action.get_extra().get("sasagale", ()):
            move_battle_to_graveyard(s, action.player, uid, reason="effect")
        offered = action.get_extra().get("fushigiverse")
        if offered:
            giver = s.players[action.player].find_creature(offered)
            leave_battle_zone(s, action.player, giver, reason="fushigiverse", to_zone="mana_zone")
            s.players[action.player].find_mana(offered).is_tapped = True
        tap_mana_for_payment(s, action.player, action.mana_used)
        if action.target_zone in ("graveyard", "mana_zone"):
            # Staged through the hand so every summon enters the same way. The
            # cost is already paid, so a card in mana may have tapped itself.
            _stage_for_summon(s, action.player, action.card_uid, action.target_zone)
        hand_card = s.players[action.player].find_in_hand(action.card_uid or "") if action.card_uid else None
        orig_def = hand_card.definition if hand_card else None
        if action.card_uid:
            # Normal summon from hand
            creature = move_hand_to_battle(
                s,
                action.player,
                action.card_uid,
                evolution_base_uid=action.evolution_base_uid,
                paid_mana=bool(action.mana_used),
                from_graveyard=action.target_zone == "graveyard",
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
        if action.get_extra().get("abyss_rush"):
            creature.has_summoning_sickness = False
            creature.temp_flags["abyss_rush_turn"] = s.turn_number
        summoner = s.players[action.player]
        if summoner.next_creature_discount:
            # Used up by this summon; the creature may attack this turn
            summoner.next_creature_discount = 0
            creature.has_summoning_sickness = False
        s.record_action(action_type, action.player, action.card_id, creature.uid)

    elif action_type == ActionType.CAST_SPELL:
        _require_card_uid(action)
        tap_mana_for_payment(s, action.player, action.mana_used)
        recycled = action.target_zone == "graveyard"
        if recycled:
            _stage_for_summon(s, action.player, action.card_uid, "graveyard")
        # Look up the spell card definition before moving it to graveyard
        hand_card = s.players[action.player].find_in_hand(action.card_uid)
        spell_def = hand_card.definition if hand_card else None
        s.record_action(action_type, action.player, action.card_id)

        # Queue the spell's effects (rules 600-608); they resolve when the action ends
        if spell_def is not None:
            s.players[action.player].hand.remove(hand_card)
            from engine.shield_break_window import queue_spell_effects
            face = spell_def
            if action.get_extra().get("twinpact_spell"):
                from engine.shield_break_window import spell_side
                face = spell_side(spell_def)
            queue_spell_effects(s, action.player, action.card_uid, face,
                                paid=bool(action.mana_used), card_definition=spell_def,
                                destination="deck_bottom" if recycled else "graveyard")

    elif action_type in (
        ActionType.GENERATE_CROSS_GEAR,
        ActionType.EXECUTE_TAMASEED,
    ):
        _require_card_uid(action)
        tap_mana_for_payment(s, action.player, action.mana_used)
        battle_card = move_hand_to_battle(
            s, action.player, action.card_uid, paid_mana=bool(action.mana_used))
        battle_card.has_summoning_sickness = False
        s.record_action(action_type, action.player, action.card_id, battle_card.uid)

    elif action_type == ActionType.DEPLOY_FIELD:
        _require_card_uid(action)
        tap_mana_for_payment(s, action.player, action.mana_used)
        field_creature = move_hand_to_field(s, action.player, action.card_uid)
        s.record_action(action_type, action.player, action.card_id, field_creature.uid)

    elif action_type == ActionType.CROSS_GEAR:
        _require_card_uid(action)
        if not action.target_uid:
            raise ValueError("CROSS_GEAR requires target_uid")
        tap_mana_for_payment(s, action.player, action.mana_used)
        cross_gear_to_creature(s, action.player, action.card_uid, action.target_uid)
        s.record_action(action_type, action.player, action.card_id, action.target_uid)

    elif action_type == ActionType.FORTIFY_CASTLE:
        _require_card_uid(action)
        held = s.players[action.player].find_in_hand(action.card_uid)
        tap_mana_for_payment(s, action.player, action.mana_used)
        if held is not None and held.definition.card_subtype == CardSubtype.G_CASTLE:
            shield = move_hand_to_shield(s, action.player, action.card_uid)  # rule 822.2a
            shield.is_face_up = True
            from core.continuous_rules import queue_ability, sources_of
            queue_ability(s, shield.definition, "face_up_self", source_uid=shield.uid,
                          controller=action.player, data={})
            for watcher in sources_of(s, "face_up_shield"):
                if watcher.controller == action.player:
                    queue_ability(s, watcher.definition, "face_up_shield", source_uid=watcher.uid,
                                  controller=action.player, data={})
        elif not action.target_uid:
            raise ValueError("FORTIFY_CASTLE requires target_uid")
        else:
            fortify_shield_with_castle(s, action.player, action.card_uid, action.target_uid)
        s.record_action(action_type, action.player, action.card_id, action.target_uid)

    elif action_type == ActionType.COMBINE_KING_CREATURE:
        if action.card_id is None:
            raise ValueError("COMBINE_KING_CREATURE requires card_id")
        if db is None:
            raise ValueError("COMBINE_KING_CREATURE requires db")
        king_defn = db.require(action.card_id)
        # Validate combine requirements before executing
        from engine.special_cards.king_cells import validate_king_combine
        valid, reason = validate_king_combine(
            s, action.player, king_defn, list(action.selected_uids)
        )
        if not valid:
            raise ValueError(f"Invalid King Cell combine: {reason}")
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
        return resolve_shield_break_choice(s, action.shield_index, action=action)

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
        s.record_action(action_type, action.player, action.card_id)
        if action.get_extra().get("counterattack"):
            amount, civilization = COUNTERATTACK[spell_def.slug]
            s.players[action.player].mana_debts.append((amount, civilization))
            s.attack_context.counterattack_used = True
        if spell_def is not None:
            s.players[action.player].hand.remove(hand_card)
            from engine.shield_break_window import queue_spell_effects
            queue_spell_effects(s, action.player, action.card_uid, spell_def)

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
        if action.target_uid:  # the creature tapped to pay for it
            s.players[action.player].find_creature(action.target_uid).tap()
        creature.hyper_mode_released = True
        s.record_action(action_type, action.player, action.card_id, creature.uid)

    else:
        # Selection and advanced free-execution actions are handled by effect
        # and shield resolvers as those systems are filled in.
        s.record_action(action_type, action.player, action.card_id, action.target_uid)

    return check_state_based_actions(s)


def _is_legal_action(state: GameState, action: Action, db=None) -> bool:
    """
    Check if an action is legal in the current state.
    
    Validates against get_legal_actions, plus additional mandatory action checks:
    - Rule 506.1b: pass_attack is illegal if any must_attack creature can still attack
    - Rule 507.1b: pass_block is illegal if any must_block creature hasn't been chosen
    """
    # First check: is action in legal_actions list?
    legal_actions = get_legal_actions(state, db)
    if not any(actions_equal(action, legal) for legal in legal_actions):
        return False
    
    # Second check: mandatory action enforcement
    from core.enums import ActionType, Phase
    
    # Rule 506.1b: cannot pass attack if must_attack creatures still can attack
    if action.action_type == ActionType.PASS:
        step = action.get_extra().get("step")
        if step == "attack" and state.current_phase == Phase.ATTACK:
            player = state.active_player
            p_state = state.players[player]
            must_attack_creatures = [
                c for c in p_state.battle_zone
                if c.can_attack() and c.temp_flags.get("must_attack", False)
            ]
            if must_attack_creatures:
                return False  # Must attack with one of these creatures first
    
    # Rule 507.1b: cannot pass block if must_block creatures haven't all declared
    if action.action_type == ActionType.PASS:
        step = action.get_extra().get("step")
        if step == "block" and state.current_phase == Phase.BLOCK_DECLARE:
            if state.attack_context:
                defender = state.attack_context.defending_player
                d_state = state.players[defender]
                must_block_creatures = [
                    c for c in d_state.battle_zone
                    if not c.is_ignored and not c.is_tapped
                    and c.uid != state.attack_context.target_uid
                    and c.is_blocker()
                    and c.temp_flags.get("must_block", False)
                ]
                if must_block_creatures:
                    return False  # Must declare block with all compelled creatures first
    
    return True


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

    # POWER_ATTACKER: set flag so compute_power() adds the bonus
    if attacker.has_keyword(Keyword.POWER_ATTACKER):
        attacker.temp_flags["power_attacker_active"] = True

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
    """
    Use one creature's blocker ability. The defender may use more, one at a
    time; the attack target is whichever was used last (rules 507.1-507.2).
    """
    if state.attack_context is None:
        raise ValueError("No attack is in progress")
    blocker = state.players[action.player].find_creature(action.card_uid or "")
    if blocker is None:
        raise ValueError("Blocking creature not found")
    blocker.tap()
    state.attack_context.blocker_uid = blocker.uid
    state.attack_context.blocker_player = action.player
    state.attack_context.block_was_declared = True
    state.record_action(action.action_type, action.player, action.card_id, blocker.uid)

    if not any(
        a.action_type in (ActionType.DECLARE_BLOCKER, ActionType.DECLARE_GUARDMAN)
        for a in get_legal_actions(state)
    ):
        _finish_block(state)


def _finish_block(state: GameState) -> None:
    """Only the creature used last counts as blocking (rule 507.2a)."""
    ctx = state.attack_context
    ctx.block_finished = True
    state.turn_info.phase = Phase.BATTLE
    found = state.find_creature_anywhere(ctx.blocker_uid or "")
    if found is None:
        return
    blocker = found[1]
    blocker.is_blocking = True
    blocker.blocking_uid = ctx.attacker_uid

    from core.enums import TriggerEvent
    from engine.trigger_registry import fire_trigger
    fire_trigger(state, TriggerEvent.ON_BLOCK, {
        "source_uid": blocker.uid,
        "source_card_id": blocker.id,
        "controller": ctx.blocker_player,
        "attacker_uid": ctx.attacker_uid,
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

    from engine.shield_break_window import close_window_if_done, rules_trigger
    if win is not None and win.phase == "resolve":
        win.resolved_keys.add(f"g_strike:{action.card_uid}:")
        close_window_if_done(state)
    else:
        move_standby_shield_to_hand(state, action.player, action.card_uid)
    state.effect_stack.add_immediate([rules_trigger(
        "g_strike", action.card_uid, action.card_id or 0, action.player)])
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
