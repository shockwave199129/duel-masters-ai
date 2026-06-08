"""
engine/action_generator.py — Legal action generator.

The single entry point is:

    get_legal_actions(state: GameState, db: CardDatabase) -> list[Action]

Given any GameState, returns every action the current player (or non-turn
player, for response phases) is legally allowed to take RIGHT NOW.

Every rule reference is cited from DM Comprehensive Rules Ver. 1.50.

─────────────────────────────────────────────────────────────────────────────
DESIGN RULES

1. PURE FUNCTION — takes state, returns list. No mutation, no side effects.
2. RULE 101.2  — "Cannot" beats "Can". Cards beat rules.
   Every filter applies the most restrictive applicable rule.
3. RULE 101.3  — "Do everything you can."
   Even if part of a play is illegal, the legal part is still offered.
4. PHASE-GATING — actions outside the current phase are never returned.
5. PLAYER-GATING — active player gets main/attack actions; inactive player
   gets response actions (block, ninja strike, shield trigger).
6. MCTS SAFETY  — the action list is self-contained; MCTS can branch without
   touching the database again.

─────────────────────────────────────────────────────────────────────────────
MANA COMBINATION ALGORITHM  (Rule 112.2a)

This is the hardest part. The player must choose:
  a) Which mana cards to tap (enough total mana ≥ cost)
  b) For each multi-civ mana card tapped: which ONE civilization it provides
     (a multi-civ card does NOT provide all its civs simultaneously)
  c) Every required civilization in the card's cost is covered by exactly
     one tapped mana card

We generate ALL valid mana combinations for each card being played.
In training, the bot picks among them using the neural network policy.
During random play, a valid combination is chosen at random.
"""

from __future__ import annotations
import itertools
import re
from typing import Optional

from core.enums import (
    Phase, ActionType, Civilization, Keyword,
    CardType, CardSubtype, ManaUsage,
)
from core.state import GameState
from core.zones import Creature, ManaCard
from core.cards import CardDefinition
from core.actions import (
    Action,
    charge_mana, pass_charge,
    summon_creature, cast_spell, generate_cross_gear,
    cross_gear, fortify_castle, deploy_field, execute_tamaseed,
    pass_main,
    attack_player, attack_creature, pass_attack,
    declare_blocker, declare_guardman, pass_block,
    use_shield_trigger, use_s_back, use_ninja_strike,
    use_g_zero, use_g_strike,
    hyperize,
    select_yes_no, select_target, select_targets,
    select_card, select_evolution_base,
    pass_action,
)

# We lazily import CardDatabase to avoid circular imports
# (db module depends on core, which is fine; engine depends on both)


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def get_legal_actions(state: GameState, db=None) -> list[Action]:
    """
    Return every legal action for the player who must act right now.

    If the effect stack has a pending choice (awaited_choice), that takes
    absolute priority — only the choice actions are returned.

    Otherwise, actions are gated by the current phase.

    Args:
        state:  current GameState (not mutated)
        db:     CardDatabase instance (optional — only needed for cost-mod
                effects that require card lookups; pass None for pure tests)

    Returns:
        Non-empty list of Action objects. The engine guarantees at least
        PASS is always legal so the game can always progress.
    """

    # ── Priority 0: Awaited choice ─────────────────────────────────────────
    # Rule 101.4: effect processing always finishes before player actions.
    # If the stack is waiting for a choice, ONLY return the valid options.
    if state.effect_stack.is_waiting_for_choice():
        return _generate_choice_actions(state)

    # ── Priority 1: Shield trigger queue ──────────────────────────────────
    # Rule 113.6: when a shield enters standby, declarations happen first.
    if state.effect_stack.shield_trigger_queue:
        return _generate_shield_trigger_actions(state)

    # ── Priority 2: Phase-gated actions ───────────────────────────────────
    phase = state.current_phase
    player = state.active_player

    if phase == Phase.START_OF_TURN:
        # Rule 501: turn-based action — untap. No player choices except
        # Silent Skill (choosing NOT to untap a creature).
        return _generate_start_of_turn_actions(state)

    elif phase == Phase.DRAW:
        # Rule 502: mandatory draw — no player choices. PASS signals done.
        return [pass_action(player, "draw")]

    elif phase == Phase.MANA_CHARGE:
        # Rule 503: player may charge 1 card or pass.
        return _generate_mana_charge_actions(state)

    elif phase == Phase.MAIN:
        # Rule 504: play cards (summon, cast, cross gear, etc.)
        return _generate_main_actions(state, db)

    elif phase == Phase.ATTACK:
        # Rule 505: outer attack loop — player picks next attacker or passes.
        return _generate_attack_declarations(state)

    elif phase == Phase.ATTACK_DECLARE:
        # Rule 506.3: after attacker declared — turn player triggers,
        # then non-turn player may use Ninja Strike before block.
        return _generate_post_declare_actions(state)

    elif phase == Phase.BLOCK_DECLARE:
        # Rule 507: non-turn player may block or pass.
        return _generate_block_actions(state)

    elif phase == Phase.BATTLE:
        # Rule 508: battle is automatic after any timing-window choices.
        return _generate_battle_timing_actions(state)

    elif phase == Phase.DIRECT_ATTACK:
        # Rule 509: shield breaks and S-Trigger declarations.
        return _generate_direct_attack_actions(state)

    elif phase == Phase.END_OF_ATTACK:
        # Rule 510: end-of-attack triggers resolve automatically.
        return [pass_action(player, "end_of_attack")]

    elif phase == Phase.END_OF_TURN:
        # Rule 511: end-of-turn triggers resolve automatically.
        return [pass_action(player, "end_of_turn")]

    # Fallback — should never reach here
    return [pass_action(player, "unknown")]


# ─────────────────────────────────────────────────────────────────────────────
# Phase: START_OF_TURN  (rule 501)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_start_of_turn_actions(state: GameState) -> list[Action]:
    """
    Rule 501.1: untap is a turn-based action.
    Rule 501.1a: Silent Skill — player MAY choose not to untap a creature
                 with Silent Skill to gain its effect instead.

    We return:
      - PASS (proceed with normal untap of all cards)
      - For each creature with Silent Skill that is currently tapped:
        a SELECT_YES_NO for "keep this tapped for Silent Skill?"
    """
    player = state.active_player
    actions: list[Action] = []

    # Check for Silent Skill creatures (must be currently tapped to matter)
    for creature in state.players[player].battle_zone:
        if (creature.has_keyword(Keyword.SILENT_SKILL)
                and creature.is_tapped
                and not creature.is_ignored):
            # Offer the choice: activate Silent Skill (don't untap)
            actions.append(select_yes_no(player, True, creature.uid))
            actions.append(select_yes_no(player, False, creature.uid))

    # Always legal: proceed with normal untap
    actions.append(pass_action(player, "start_of_turn"))
    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Phase: MANA_CHARGE  (rule 503)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_mana_charge_actions(state: GameState) -> list[Action]:
    """
    Rule 503.1: turn player may place 1 card from hand into mana zone.
    Rule 503.2: normally only 1 charge per turn.

    Global effects may prevent charging (rule 101.2: "cannot" beats "can").
    Cards with no civilization are still valid to charge (they produce 1 mana,
    colorless, rule 207.3).
    """
    player = state.active_player
    p_state = state.players[player]

    actions: list[Action] = []

    # Check global "cannot charge" restriction
    if not state.global_effects.can_charge_mana(player):
        return [pass_charge(player)]

    # Already charged this turn
    if p_state.has_charged_mana_this_turn:
        return [pass_charge(player)]

    # Offer each hand card as a valid charge target
    for card in p_state.hand:
        actions.append(charge_mana(player, card.uid, card.id))

    # Always legal to skip charging
    actions.append(pass_charge(player))
    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Phase: MAIN  (rule 504)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_main_actions(state: GameState, db=None) -> list[Action]:
    """
    Rule 504.1: turn player may execute any number of cards during main step.
    Rule 504.2: may also cross an existing cross gear onto a creature.

    Checks per card:
      - Is it the right card type to execute? (creature/spell/cross gear/etc.)
      - Can the player legally execute it? (global restrictions)
      - Can the player afford it? (mana amount + civilizations)
      - For evolutions: is there a valid base in the battle zone?
      - Special subtypes: G-Zero, Gravity Zero (free if condition met)
      - Hyperize: if a creature with unused Hyper Mode is in the battle zone

    Returns summon/cast/generate/etc. actions for each playable card,
    plus cross gear actions for each gear already in the battle zone,
    plus PASS (end main step, move to attack).
    """
    player = state.active_player
    p_state = state.players[player]
    actions: list[Action] = []

    # ── Cards from hand ────────────────────────────────────────────────────
    for hand_card in p_state.hand:
        defn = hand_card.definition

        card_actions = _actions_for_hand_card(
            player, hand_card.uid, defn, state, db
        )
        actions.extend(card_actions)

    # ── Cross existing Cross Gears in battle zone onto creatures ──────────
    # Rule 504.2: pay cost again to cross a gear onto a creature.
    for gear in p_state.battle_zone:
        if gear.definition.card_type == CardType.CROSS_GEAR:
            gear_cost = gear.definition.cost
            gear_civs = gear.definition.civilizations
            combos = _get_mana_combinations(
                p_state.mana_zone, gear_cost, gear_civs
            )
            if combos:
                # Can cross onto any of our own creatures
                for target in p_state.battle_zone:
                    if target.definition.card_type != CardType.CROSS_GEAR:
                        # Rule 303.3: can't re-cross onto same creature
                        if target.uid != gear.uid:
                            for combo in combos:
                                actions.append(cross_gear(
                                    player, gear.uid, gear.id,
                                    target.uid, combo
                                ))

    # ── Hyperize (rule 816) ────────────────────────────────────────────────
    # A creature in the battle zone with Hyperize ability whose Hyper Mode
    # has not yet been released this turn.
    for creature in p_state.battle_zone:
        if (creature.has_keyword(Keyword.HYPERIZE)
                and not creature.hyper_mode_released
                and not creature.is_ignored):
            actions.append(hyperize(player, creature.uid, creature.id))

    # ── Pass (end main step) ───────────────────────────────────────────────
    actions.append(pass_main(player))
    return actions


def _actions_for_hand_card(
    player: int,
    card_uid: str,
    defn: CardDefinition,
    state: GameState,
    db=None,
) -> list[Action]:
    """
    Generate all legal play actions for one card in hand.
    Returns empty list if the card cannot be played right now.
    """
    actions: list[Action] = []
    p_state = state.players[player]

    card_type    = defn.card_type
    card_subtype = defn.card_subtype

    # ── Creatures ──────────────────────────────────────────────────────────
    if card_type == CardType.CREATURE:
        # Global restriction check (rule 101.2)
        if not state.global_effects.can_summon_creature(
            player,
            defn.civilizations,
            card_type=card_type.value,
            card_subtype=card_subtype.value,
        ):
            return []

        # ── G-Zero / Gravity Zero (free summon, rule 112.3e) ──────────────
        if (defn.has_keyword(Keyword.G_ZERO)
                or defn.has_keyword(Keyword.GRAVITY_ZERO)):
            if _g_zero_condition_met(defn, state, player):
                actions.append(use_g_zero(player, card_uid, defn.id))

        # ── Evolution creatures (rule 801) ────────────────────────────────
        if defn.is_evolution():
            valid_bases = _get_valid_evolution_bases(defn, p_state)
            if not valid_bases:
                return actions  # no valid base → cannot summon

            # Apply cost modifiers and get mana combinations
            effective_cost = _compute_effective_cost(defn, state, player)
            combos = _get_mana_combinations(
                p_state.mana_zone, effective_cost, defn.civilizations
            )
            if not combos:
                return actions  # can't afford

            for base in valid_bases:
                for combo in combos:
                    actions.append(summon_creature(
                        player, card_uid, defn.id, combo,
                        evolution_base_uid=base.uid
                    ))
            return actions

        # ── Normal creature summon ─────────────────────────────────────────
        effective_cost = _compute_effective_cost(defn, state, player)
        combos = _get_mana_combinations(
            p_state.mana_zone, effective_cost, defn.civilizations
        )
        for combo in combos:
            actions.append(summon_creature(player, card_uid, defn.id, combo))

    # ── Spells ─────────────────────────────────────────────────────────────
    elif card_type == CardType.SPELL:
        # Global restriction check (rule 101.2)
        if not state.global_effects.can_cast_spell(player, defn.civilizations):
            return []

        effective_cost = _compute_effective_cost(defn, state, player)
        combos = _get_mana_combinations(
            p_state.mana_zone, effective_cost, defn.civilizations
        )
        for combo in combos:
            actions.append(cast_spell(player, card_uid, defn.id, combo))

    # ── Cross Gear ─────────────────────────────────────────────────────────
    elif card_type == CardType.CROSS_GEAR:
        effective_cost = _compute_effective_cost(defn, state, player)
        combos = _get_mana_combinations(
            p_state.mana_zone, effective_cost, defn.civilizations
        )
        for combo in combos:
            actions.append(generate_cross_gear(player, card_uid, defn.id, combo))

    # ── Castle (rule 304) ──────────────────────────────────────────────────
    elif card_type == CardType.CASTLE:
        # Must have at least one shield to fortify
        if state.players[player].shield_count == 0:
            return []
        effective_cost = _compute_effective_cost(defn, state, player)
        combos = _get_mana_combinations(
            p_state.mana_zone, effective_cost, defn.civilizations
        )
        for combo in combos:
            # Can attach to any shield (represented by index)
            for i, shield in enumerate(state.players[player].shield_zone):
                actions.append(fortify_castle(
                    player, card_uid, defn.id, combo,
                    target_uid=shield.uid
                ))

    # ── Field (rule 308) ───────────────────────────────────────────────────
    elif card_type == CardType.FIELD:
        effective_cost = _compute_effective_cost(defn, state, player)
        combos = _get_mana_combinations(
            p_state.mana_zone, effective_cost, defn.civilizations
        )
        for combo in combos:
            actions.append(deploy_field(player, card_uid, defn.id, combo))

    # ── Tamaseed (rule 315) ────────────────────────────────────────────────
    elif card_type == CardType.TAMASEED:
        effective_cost = _compute_effective_cost(defn, state, player)
        combos = _get_mana_combinations(
            p_state.mana_zone, effective_cost, defn.civilizations
        )
        for combo in combos:
            actions.append(execute_tamaseed(player, card_uid, defn.id, combo))

    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Phase: ATTACK  (rule 505-506)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_attack_declarations(state: GameState) -> list[Action]:
    """
    Rule 506.1: turn player chooses one creature to attack with, or passes.
    Rule 505.2: can attack non-turn player OR non-turn player's TAPPED creatures.
    Rule 506.1a: attacker must be untapped, not have summoning sickness
                 (unless Speed Attacker), and not have cannot_attack flag.
    Rule 116.2: ignored creatures (with seals) cannot attack.
    Rule 506.1b: compelled creatures MUST attack but order is player's choice.
    Rule 505.5: player can attack as many times as they want per turn.
    """
    player   = state.active_player
    opponent = state.inactive_player
    p_state  = state.players[player]
    o_state  = state.players[opponent]

    actions: list[Action] = []

    # Global "cannot attack" check
    if not state.global_effects.can_attack_globally(player):
        return [pass_attack(player)]

    for creature in p_state.battle_zone:
        if not creature.can_attack():
            continue

        # ── Attack player (rule 506.1e) ────────────────────────────────────
        # Cannot attack player if creature has "cannot_attack_players"
        if creature.can_attack_players():
            actions.append(attack_player(player, creature.uid, creature.id))

        # ── Attack opponent's creatures (rule 506.1e) ─────────────────────
        # Rule 505.2: normally only TAPPED opponent creatures.
        # Exception: Mach Fighter can attack UNTAPPED opponent creatures too.
        is_mach = creature.has_keyword(Keyword.MACH_FIGHTER)

        for target in o_state.battle_zone:
            if target.is_ignored:          # rule 116.2: ignored can't be targeted
                continue
            if target.is_tapped or is_mach:
                actions.append(attack_creature(
                    player, creature.uid, creature.id,
                    target.uid, target.id
                ))

    # Always legal to stop attacking (rule 506.2: optional)
    actions.append(pass_attack(player))
    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Phase: ATTACK_DECLARE  (rule 506.3 — after attacker declared)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_post_declare_actions(state: GameState) -> list[Action]:
    """
    After an attacker is declared (rule 506.3), before the block step:
      - Turn player may declare Revolution Change, Attack Chance, etc.
      - Non-turn player may declare Ninja Strike (rule 112.3c).

    In this phase the 'active_player' is still the turn player, but
    we check the attack context to know who responds.

    For simplicity: we offer PASS to signal "done with declarations".
    Complex triggers (Revolution Change etc.) are handled by the effect
    system when the card effect resolves — not generated here as standalone
    legal actions (they appear as SELECT_YES_NO when the trigger fires).
    """
    player = state.active_player
    opponent = 1 - player
    actions: list[Action] = []

    if state.attack_context is None:
        return [pass_action(player, "post_declare")]

    # ── Ninja Strike (rule 112.3c) ─────────────────────────────────────────
    # Non-turn player may declare Ninja Strike after a turn player's creature
    # attacks. Condition: mana count >= specified threshold.
    opp_state = state.players[opponent]
    opp_hand  = opp_state.hand
    opp_mana  = opp_state.mana_count

    for hand_card in opp_hand:
        defn = hand_card.definition
        if not defn.has_keyword(Keyword.NINJA_STRIKE):
            continue
        # Check mana condition from card effect (simplified: cost <= mana)
        # The full check would read the ninja_strike condition from card_effects
        ns_cost = _get_ninja_strike_cost(defn)
        if ns_cost is not None and opp_mana >= ns_cost:
            actions.append(use_ninja_strike(
                opponent,
                hand_card.uid, hand_card.id,
            ))

    actions.append(pass_action(player, "post_declare"))
    return actions


def _generate_battle_timing_actions(state: GameState) -> list[Action]:
    """
    Before battle resolves, the turn player may have a Ninja Strike timing
    window if the non-turn player blocked.
    """
    player = state.active_player
    actions: list[Action] = []
    ctx = state.attack_context
    if ctx is not None and ctx.block_was_declared:
        p_state = state.players[player]
        for hand_card in p_state.hand:
            defn = hand_card.definition
            if not defn.has_keyword(Keyword.NINJA_STRIKE):
                continue
            ns_cost = _get_ninja_strike_cost(defn)
            if ns_cost is not None and p_state.mana_count >= ns_cost:
                actions.append(use_ninja_strike(player, hand_card.uid, hand_card.id))

    actions.append(pass_action(player, "battle"))
    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Phase: BLOCK_DECLARE  (rule 507)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_block_actions(state: GameState) -> list[Action]:
    """
    Rule 507.1: non-turn player may use Blocker or Guardman to change the
                attack target.
    Rule 507.1a: blocker must be:
                  - untapped
                  - not ignored (no seals, rule 116.2)
                  - have Blocker keyword
                  - not the creature being attacked (rule 507.1a example)
    Rule 507.1a (Guardman): Guardman changes the target of a creature attack;
                  it cannot be chosen when the opponent attacks the player.
    Rule 507.1a (Cannot be blocked): if attacker has "cannot_be_blocked",
                  no creature may declare Blocker.

    The NON-TURN player makes block declarations.
    We use state.attack_context to determine the defending player.
    """
    if state.attack_context is None:
        return [pass_block(state.inactive_player)]

    ctx      = state.attack_context
    defender = ctx.defending_player
    d_state  = state.players[defender]

    actions: list[Action] = []

    # If the attacker cannot be blocked, return pass immediately
    attacker_result = state.find_creature_anywhere(ctx.attacker_uid)
    if attacker_result:
        _, attacker = attacker_result
        if not attacker.can_be_blocked():
            return [pass_block(defender)]

    for creature in d_state.battle_zone:
        if creature.is_ignored:          # rule 116.2
            continue
        if creature.is_tapped:           # rule 507.1a example
            continue
        if creature.uid == ctx.target_uid:  # rule 507.1a: attacked creature can't self-block
            continue

        # ── Blocker (rule 507.1a) ─────────────────────────────────────────
        if creature.is_blocker():
            actions.append(declare_blocker(defender, creature.uid, creature.id))

        # ── Guardman (rule 507.1a) ────────────────────────────────────────
        # Guardman is not usable when the player is attacked directly.
        if creature.is_guardman() and ctx.is_attacking_creature:
            actions.append(declare_guardman(defender, creature.uid, creature.id))

    # Always legal: don't block
    actions.append(pass_block(defender))
    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Phase: DIRECT_ATTACK  (rule 509)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_direct_attack_actions(state: GameState) -> list[Action]:
    """
    Rule 509: the attacking creature breaks shields or attacks directly.
    Rule 509.1: if opponent has 0 shields, it's a direct attack (loss condition).
    Rule 509.2: if opponent has shields, determine break count.
    Rule 509.3: active player CHOOSES which shield(s) to break.
    Rule 509.5a-c: after breaks, S-Trigger, G-Strike, S-Back declarations.

    During normal flow, the shield break order is selected by the turn player.
    S-Trigger / G-Strike responses are handled by shield_trigger_queue.
    """
    player = state.active_player
    if state.attack_context is None:
        return [pass_action(player, "direct_attack")]

    ctx      = state.attack_context
    defender = ctx.defending_player
    d_state  = state.players[defender]

    if not ctx.is_attacking_player:
        return [pass_action(player, "direct_attack")]

    # No shields → direct attack → state-based action handles the win
    # Just pass to let SBA checker run
    if d_state.shield_count == 0:
        return [pass_action(player, "direct_attack")]

    # Player must choose which shield(s) to break
    # We return one SELECT_ATTACK_ORDER action per available shield position
    actions: list[Action] = []
    for i in range(d_state.shield_count):
        from core.actions import select_shield_to_break
        actions.append(select_shield_to_break(player, shield_index=i))

    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Shield trigger declarations  (rule 113.6, 509.5a-c)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_shield_trigger_actions(state: GameState) -> list[Action]:
    """
    Rule 113.6: when a shield is broken, before it moves to hand, the
    defending player may declare S-Trigger, G-Strike, and S-Back.
    Rule 509.5a: S-Trigger — cast for free from broken shield.
    Rule 509.5b: G-Strike — use G-Strike effect for free.
    Rule 509.5c: S-Back — discard a card from hand to execute for free.

    The shield is in standby state (in shield_trigger_queue).
    The DEFENDING player (owner of the shield) makes these declarations.
    """
    queue = state.effect_stack.shield_trigger_queue
    if not queue:
        return []

    shield_player, shield_card = queue[0]  # peek at next pending shield
    actions: list[Action] = []

    defn = shield_card.definition

    # ── S-Trigger (rule 509.5a / 112.3a) ─────────────────────────────────
    if defn.has_shield_trigger():
        actions.append(use_shield_trigger(
            shield_player, shield_card.uid, defn.id, use=True
        ))
        # Also offer "add to hand without triggering"
        actions.append(use_shield_trigger(
            shield_player, shield_card.uid, defn.id, use=False
        ))

    # ── G-Strike (rule 509.5b / 101.4b) ──────────────────────────────────
    if defn.has_keyword(Keyword.G_STRIKE):
        actions.append(use_g_strike(
            shield_player, shield_card.uid, defn.id, use=True
        ))

    # ── S-Back (rule 509.5c / 112.3b) ────────────────────────────────────
    # The executable S-Back card is in hand; the broken shield card is the
    # discard cost. Full condition matching is card-specific and parsed later.
    for hand_card in state.players[shield_player].hand:
        if hand_card.definition.has_keyword(Keyword.S_BACK):
            actions.append(use_s_back(
                shield_player,
                hand_card.uid, hand_card.id,
                shield_card.uid, shield_card.id,
            ))

    # If none of the above or player declines all, just add to hand
    if not actions:
        actions.append(pass_action(shield_player, "shield_to_hand"))
    else:
        # Always offer "no trigger, just add to hand"
        actions.append(pass_action(shield_player, "shield_to_hand"))

    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Awaited choice  (effect stack is waiting for player input)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_choice_actions(state: GameState) -> list[Action]:
    """
    When an effect is waiting for a player choice (select_target, yes/no,
    select card etc.), only the valid options for that choice are returned.

    Rule 101.3: do everything you can. If valid_options is empty the effect
    fizzles — return PASS to let the executor know.
    """
    choice = state.effect_stack.awaited_choice
    if choice is None:
        return [pass_action(state.active_player, "no_choice")]

    player  = choice.player
    options = choice.valid_options
    actions: list[Action] = []

    if choice.choice_type == "yes_no":
        actions.append(select_yes_no(player, True,  choice.source_uid))
        actions.append(select_yes_no(player, False, choice.source_uid))

    elif choice.choice_type == "select_target":
        for target_uid in options:
            actions.append(select_target(
                player, target_uid,
                choice.effect.active_in_zone[0] if choice.effect else "battle_zone",
                choice.source_uid,
            ))
        # "up_to" effects allow choosing fewer targets → include pass
        if choice.min_choices == 0:
            actions.append(pass_action(player, "select_target_done"))

    elif choice.choice_type == "select_card":
        for card_uid in options:
            actions.append(select_card(
                player, card_uid, 0,  # card_id unknown here; executor resolves
                choice.source_uid,
                choice.effect.active_in_zone[0] if choice.effect else "hand",
            ))

    elif choice.choice_type in ("shield_trigger", "g_strike"):
        # yes = use, no = add to hand
        actions.append(select_yes_no(player, True,  choice.source_uid))
        actions.append(select_yes_no(player, False, choice.source_uid))

    elif choice.choice_type == "ninja_strike":
        # yes = declare ninja strike, no = proceed with normal battle
        actions.append(select_yes_no(player, True,  choice.source_uid))
        actions.append(select_yes_no(player, False, choice.source_uid))

    if not actions:
        actions.append(pass_action(player, "choice_fizzle"))

    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Mana combination algorithm  (rule 112.2a)
# ─────────────────────────────────────────────────────────────────────────────

def _get_mana_combinations(
    mana_zone: list[ManaCard],
    cost:      int,
    card_civs: frozenset[Civilization],
) -> list[list[ManaUsage]]:
    """
    Rule 112.2a: generate all valid sets of mana cards the player can tap
    to pay `cost` mana while covering every civilization in `card_civs`.

    A multi-civilization mana card provides EXACTLY ONE civilization
    chosen by the player at payment time.

    Returns list of ManaUsage lists. Empty list = cannot afford.

    Algorithm:
      1. Identify which required civilizations need to be covered.
      2. For each required civilization, find mana cards that can provide it.
      3. Assign one mana card per required civilization (no sharing —
         each mana card covers ONE civ per tap, rule 112.2a).
      4. Fill remaining cost with any untapped mana cards.
      5. Deduplicate equivalent combinations.

    We limit to a reasonable max combinations to keep action space tractable.
    """
    untapped = [m for m in mana_zone if not m.is_tapped]
    required_civ_count = len(card_civs)
    minimum_cards_needed = max(cost, required_civ_count)

    if cost <= 0 and not card_civs:
        return [[]]  # free and no civilization requirement

    if len(untapped) < minimum_cards_needed:
        return []  # not enough mana at all

    # Special case: no civilization requirement (colorless card)
    if not card_civs:
        return _combinations_no_civ(untapped, cost)

    results: list[list[ManaUsage]] = []
    seen: set[frozenset] = set()

    # For each required civilization, find which untapped mana cards cover it
    # (a multi-civ mana card can cover any one of its civilizations)
    civ_list = list(card_civs)

    # Generate assignments: one untapped mana card per required civilization
    # We use itertools.product over candidate mana cards per civilization
    candidates_per_civ: list[list[tuple[ManaCard, Civilization]]] = []
    for civ in civ_list:
        candidates = [
            (m, civ)
            for m in untapped
            if civ in m.civilizations
        ]
        if not candidates:
            return []  # this civilization is not coverable
        candidates_per_civ.append(candidates)

    MAX_COMBOS = 50  # keep action space tractable for MCTS

    for civ_assignment in itertools.product(*candidates_per_civ):
        # Check no mana card is used twice for civilization coverage
        used_uids = [m.uid for m, _ in civ_assignment]
        if len(set(used_uids)) < len(used_uids):
            continue  # same mana card assigned to two civs

        # Build the civilization-paying usages
        civ_usages = [ManaUsage(m.uid, c) for m, c in civ_assignment]
        civ_uid_set = {m.uid for m, _ in civ_assignment}

        # Remaining mana needed beyond civilization coverage
        remaining_cost = cost - len(civ_usages)

        if remaining_cost < 0:
            # More civ assignments than cost (can happen if cost < num_civs
            # due to cost reduction) — still valid, just prioritize civs
            # Rule 112.2b: civilizations are prioritized.
            if len(civ_usages) <= len(untapped):
                combo_key = frozenset((u.mana_uid, str(u.used_for_civ)) for u in civ_usages)
                if combo_key not in seen:
                    seen.add(combo_key)
                    results.append(civ_usages)
            continue

        if remaining_cost == 0:
            combo_key = frozenset((u.mana_uid, str(u.used_for_civ)) for u in civ_usages)
            if combo_key not in seen:
                seen.add(combo_key)
                results.append(civ_usages)
            continue

        # Fill remaining cost with any untapped mana not already used for civs
        remaining_pool = [m for m in untapped if m.uid not in civ_uid_set]
        if len(remaining_pool) < remaining_cost:
            continue  # can't fill remaining cost

        # Choose any `remaining_cost` cards from the pool
        for filler in itertools.combinations(remaining_pool, remaining_cost):
            filler_usages = [ManaUsage(m.uid, None) for m in filler]
            combo = civ_usages + filler_usages
            combo_key = frozenset((u.mana_uid, str(u.used_for_civ)) for u in combo)
            if combo_key not in seen:
                seen.add(combo_key)
                results.append(combo)
                if len(results) >= MAX_COMBOS:
                    return results

    return results


def _combinations_no_civ(
    untapped: list[ManaCard],
    cost: int,
) -> list[list[ManaUsage]]:
    """
    For colorless cards (no civilization requirement):
    any `cost` untapped mana cards.
    """
    if len(untapped) < cost:
        return []
    results = []
    seen: set[frozenset] = set()
    MAX_COMBOS = 30
    for combo in itertools.combinations(untapped, cost):
        usages = [ManaUsage(m.uid, None) for m in combo]
        key = frozenset(m.uid for m in combo)
        if key not in seen:
            seen.add(key)
            results.append(usages)
            if len(results) >= MAX_COMBOS:
                break
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Evolution base validation  (rule 801)
# ─────────────────────────────────────────────────────────────────────────────

def _get_valid_evolution_bases(
    defn:    CardDefinition,
    p_state,
) -> list[Creature]:
    """
    Rule 801.1: evolution requires a valid base creature in the battle zone.
    Rule 801.1a: if no valid evolution base exists, cannot summon.

    The evolution base must:
      - Be a creature (or appropriate card type per the evolution spec)
      - Have at least one matching race from defn.evolution_source_races
      - Not be ignored (rule 116.2: ignored creatures can't be evolved onto
        because they effectively don't exist)

    Special cases:
      - S-MAX Evolution (rule 815): no base required — handled separately.
      - Star Evolution (rule 813): needs a base of any creature.
    """
    # S-MAX: no base required
    if defn.card_subtype == CardSubtype.STAR_MAX:
        return []  # handled separately (no base chosen)

    valid = []
    for creature in p_state.battle_zone:
        if creature.is_ignored:          # rule 116.2
            continue
        if creature.definition.card_type != CardType.CREATURE:
            continue
        # Check race match
        if defn.evolution_source_races:
            # At least one required race must be present in the base
            if not defn.evolution_source_races.intersection(creature.races):
                continue
        valid.append(creature)

    return valid


# ─────────────────────────────────────────────────────────────────────────────
# Cost computation  (rules 112.2, 601.1e)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_effective_cost(
    defn:   CardDefinition,
    state:  GameState,
    player: int,
) -> int:
    """
    Rule 601.1e: apply cost increase/decrease effects to get the final cost.
    Rule 112.2b: if cost drops below the number of required civilizations,
                 civilizations are still paid; excess mana disappears.

    Sources of cost modification (checked in order):
      1. Card's own cost_mod effects (e.g. Sympathy, rule 112)
      2. Global cost reductions from other cards in play
      3. Minimum: 0 (cost cannot go negative, rule 108.1a)
    """
    base_cost = defn.cost
    modification = 0

    # ── Sympathy (cost reduced by number of matching creatures) ───────────
    if defn.has_keyword(Keyword.SYMPATHY):
        # Simplified: count our own creatures in battle zone with matching race
        # Full implementation reads sympathy value from card_effects row
        for race in defn.races:
            for creature in state.players[player].battle_zone:
                if race in creature.races:
                    modification -= 1

    # ── Global cost modifiers from card_effects rows ───────────────────────
    # (Full implementation queries active COST_MOD effects from state.)
    # Placeholder: no global cost mods by default.

    effective = max(0, base_cost + modification)
    return effective


# ─────────────────────────────────────────────────────────────────────────────
# G-Zero condition check  (rule 112.3e)
# ─────────────────────────────────────────────────────────────────────────────

def _g_zero_condition_met(
    defn:   CardDefinition,
    state:  GameState,
    player: int,
) -> bool:
    """
    Rule 112.3e: G-Zero allows free summon if a specified condition is met.
    The exact condition is card-specific and stored in card_effects.

    Simplified check: count creatures of the same race in the battle zone.
    Full implementation reads the G-Zero condition from the card_effects row.
    """
    # Simplified: if player has ≥ 1 creature of the same race, condition met.
    for race in defn.races:
        for creature in state.players[player].battle_zone:
            if race in creature.races and not creature.is_ignored:
                return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Ninja Strike cost check  (rule 112.3c)
# ─────────────────────────────────────────────────────────────────────────────

_NINJA_STRIKE_RE = re.compile(r"ninja\s*strike\s+(\d+)", re.IGNORECASE)


def _get_ninja_strike_cost(defn: CardDefinition) -> Optional[int]:
    """
    Rule 112.3c: Ninja Strike can be used if the number of cards in the mana
    zone is >= the ability's threshold ("Ninja Strike N").

    The threshold N comes from the Ninja Strike ability text — it is the
    ability's own value, NOT the creature's printed summon cost (which is
    usually higher). We read N from the parsed effect rows; the card's printed
    cost is only a last-resort fallback if the text cannot be parsed.
    """
    for effect in defn.effects:
        for text in (
            effect.raw_text,
            effect.effect_value.get("text") if isinstance(effect.effect_value, dict) else None,
            effect.trigger_condition.get("text") if isinstance(effect.trigger_condition, dict) else None,
        ):
            if not text:
                continue
            match = _NINJA_STRIKE_RE.search(text)
            if match:
                return int(match.group(1))
    # Fallback: text was not parseable — use the printed cost as a proxy.
    return defn.cost


# ─────────────────────────────────────────────────────────────────────────────
# Convenience — single-card legality check (used by tests and executor)
# ─────────────────────────────────────────────────────────────────────────────

def can_play_card(
    state:    GameState,
    player:   int,
    card_uid: str,
    db=None,
) -> bool:
    """
    Returns True if the card with `card_uid` in `player`'s hand can be
    played right now (in the current phase, with current mana).
    """
    if state.current_phase != Phase.MAIN:
        return False
    if state.active_player != player:
        return False

    p_state = state.players[player]
    hand_card = p_state.find_in_hand(card_uid)
    if hand_card is None:
        return False

    actions = _actions_for_hand_card(
        player, card_uid, hand_card.definition, state, db
    )
    return len(actions) > 0


def can_attack(state: GameState, player: int, creature_uid: str) -> bool:
    """Returns True if the creature can attack in the current state."""
    if state.current_phase not in (Phase.ATTACK, Phase.ATTACK_DECLARE):
        return False
    if state.active_player != player:
        return False

    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        return False

    return creature.can_attack()
