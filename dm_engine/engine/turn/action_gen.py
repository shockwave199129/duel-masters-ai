"""engine/turn/action_gen.py — Phase-specific action generators.
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
    CardType, CardSubtype, ManaUsage, EffectAction, EffectType,
)
from core.state import GameState
from core.zones import Creature, ManaCard
from core.cards import CardDefinition, is_twinpact, get_twinpact_characteristics, is_duel_mate
from core.actions import (
    Action,
    charge_mana, pass_charge,
    summon_creature, cast_spell, generate_cross_gear,
    cross_gear, fortify_castle, deploy_field, execute_tamaseed,
    combine_king_creature,
    activate_ability,
    pass_main,
    attack_player, attack_creature, pass_attack,
    declare_blocker, declare_guardman, pass_block,
    use_shield_trigger, use_s_back, use_ninja_strike,
    use_g_zero, use_g_strike, use_attack_chance, use_over_drive, use_sabaki_z,
    hyperize,
    select_yes_no, select_target, select_targets,
    select_card, select_mana, select_evolution_base,
    pass_action,
)

# We lazily import CardDatabase to avoid circular imports
# (db module depends on core, which is fine; engine depends on both)


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
            # Build Action directly to pass context for the executor
            from core.actions import Action
            actions.append(Action(
                player=player,
                action_type=ActionType.SELECT_YES_NO,
                choice=True,  # keep tapped
                card_uid=creature.uid,
                extra=(("source_uid", creature.uid), ("context", "silent_skill")),
            ))
            actions.append(Action(
                player=player,
                action_type=ActionType.SELECT_YES_NO,
                choice=False,  # allow untap
                card_uid=creature.uid,
                extra=(("source_uid", creature.uid), ("context", "silent_skill")),
            ))

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

    # ── GR creatures from Ultra GR zone (rule 408, 701.30) ─────────────────
    # GR creatures can be summoned from the Ultra GR zone during the main phase.
    # They require paying their mana cost and enter with summoning sickness.
    for gr_def in p_state.ultra_gr_zone:
        if gr_def.card_type != CardType.CREATURE:
            continue
        if gr_def.card_subtype != CardSubtype.GR:
            continue
        # Global restriction check
        if not state.global_effects.can_summon_creature(
            player,
            gr_def.civilizations,
            card_type=gr_def.card_type.value,
            card_subtype=gr_def.card_subtype.value,
        ):
            continue
        effective_cost = _compute_effective_cost(gr_def, state, player)
        combos = _get_mana_combinations(
            p_state.mana_zone, effective_cost, gr_def.civilizations
        )
        for combo in combos:
            actions.append(summon_creature(
                player, "", gr_def.id, combo,
            ))

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

    # ── Over Drive (rule 112.2d) ────────────────────────────────────────────
    current_turn = state.turn_number
    for creature in p_state.battle_zone:
        if creature.entered_turn != current_turn:
            continue
        od_req = _get_over_drive_requirements(creature.definition)
        if not od_req or creature.temp_flags.get("over_drive_used"):
            continue
        for mana_used in _over_drive_mana_combos(p_state.mana_zone, od_req):
            actions.append(use_over_drive(
                player,
                creature.uid,
                creature.id,
                mana_used,
            ))

    # ── King Cell combine (rule 814.1c) ─────────────────────────────────────
    if db is not None:
        actions.extend(_king_combine_actions(player, state, db))

    # ── Activated abilities (rule 110.3c) ──────────────────────────────────
    actions.extend(_generate_activated_ability_actions(player, state, db))

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

    # ── Hand-zone static effects (Rule 110.4e) ──────────────────────────────
    # Check global effects that restrict execution from hand
    can_execute, reason = state.global_effects.can_execute_from_hand(
        player, card_type.value, card_subtype.value, defn.civilizations
    )
    if not can_execute:
        return []  # Blocked by hand-zone static effect

    # ── Creatures ──────────────────────────────────────────────────────────
    if card_type == CardType.CREATURE:
        # Rule 814.1: King Creatures are only summoned by combining cells.
        if defn.is_king_creature():
            return []

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
            # Rule 815.1: S-MAX can be summoned without a base
            is_smax = defn.card_subtype == CardSubtype.STAR_MAX

            # Rule 802.1: NEO can be played as normal creature OR evolved
            is_neo = defn.card_subtype == CardSubtype.NEO_EVOLUTION

            valid_bases = _get_valid_evolution_bases(defn, p_state)

            # Apply cost modifiers and get mana combinations
            effective_cost = _compute_effective_cost(defn, state, player)
            combos = _get_mana_combinations(
                p_state.mana_zone, effective_cost, defn.civilizations
            )
            if not combos:
                return actions  # can't afford

            # ── S-MAX: can summon without base (rule 815.1) ─────────────────
            if is_smax:
                for combo in combos:
                    actions.append(summon_creature(
                        player, card_uid, defn.id, combo,
                        evolution_base_uid=None  # No base for S-MAX
                    ))
                return actions

            # ── NEO: can play as normal creature OR evolved (rule 802.1) ────
            if is_neo:
                # Always allow normal (non-evolved) summon
                for combo in combos:
                    actions.append(summon_creature(
                        player, card_uid, defn.id, combo,
                        evolution_base_uid=None  # Normal summon, not evolved
                    ))
                # Additionally allow evolved summon if valid base exists
                if valid_bases:
                    for base in valid_bases:
                        for combo in combos:
                            actions.append(summon_creature(
                                player, card_uid, defn.id, combo,
                                evolution_base_uid=base.uid
                            ))
                return actions

            # ── Standard evolution: requires a valid base (rule 801.1a) ─────
            if not valid_bases:
                return actions  # no valid base → cannot summon

            for base in valid_bases:
                for combo in combos:
                    actions.append(summon_creature(
                        player, card_uid, defn.id, combo,
                        evolution_base_uid=base.uid
                    ))
            return actions

        # ── Twinpact dual-face summon (Rule 810.3) ────────────────────────
        if is_twinpact(defn):
            for face in (0, 1):
                chars = get_twinpact_characteristics(defn, face)
                face_cost = chars["cost"]
                face_civs = chars["civilizations"]
                eff_cost = _compute_effective_cost(defn, state, player)
                # Override cost for face 1 (face 0 uses card's own cost already)
                if face == 1:
                    eff_cost = max(0, face_cost + (eff_cost - defn.cost))
                combos = _get_mana_combinations(
                    p_state.mana_zone, eff_cost, face_civs
                )
                for combo in combos:
                    actions.append(summon_creature(
                        player, card_uid, defn.id, combo,
                        twinpact_face=face,
                    ))
            return actions

        # ── Duel Mate (rule 820) ────────────────────────────────────────────
        if is_duel_mate(defn):
            effective_cost = _compute_effective_cost(defn, state, player)
            combos = _get_mana_combinations(
                p_state.mana_zone, effective_cost, defn.civilizations
            )
            for combo in combos:
                actions.append(summon_creature(player, card_uid, defn.id, combo))
        else:
            # ── Normal creature summon ─────────────────────────────────────
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

    # ── Zerom ritual spells (rule 812) ──────────────────────────────────────
    elif card_subtype == CardSubtype.ZEROM:
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
        if state.effective_shield_count(player) == 0:
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


def _generate_activated_ability_actions(
    player: int,
    state: GameState,
    db=None,
) -> list[Action]:
    """
    Rule 110.3c: generate ACTIVATE_ABILITY actions for every card in play
    that has an activated ability and whose cost can be paid right now.

    Scans the player's battle zone, mana zone, and shield zone. For each
    card with activated effects, checks:
      - Is the source card untapped (if tap is required)?
      - Can the mana cost be paid with current untapped mana?
      - Is a discard available (if discard is required)?
      - Is the effect active in the current phase?

    Returns a list of activate_ability Action objects (may be empty).
    """
    actions: list[Action] = []
    p_state = state.players[player]
    current_phase = state.current_phase

    # ── Collect all zones that can hold activatable cards ─────────────────
    zones: list = []
    zones.extend(p_state.battle_zone)   # creatures, cross gears, etc.
    zones.extend(p_state.mana_zone)     # mana cards with activated abilities
    zones.extend(p_state.shield_zone)   # shield cards with activated abilities

    for card in zones:
        defn = card.definition
        activated = defn.get_activated_effects()
        if not activated:
            continue

        # Filter by phase - only include effects active in current phase
        activated = _filter_effects_by_phase(activated, current_phase)
        if not activated:
            continue

        # Determine if the source card needs to be untapped
        needs_untapped = card.is_tapped if hasattr(card, 'is_tapped') else False
        if needs_untapped:
            continue  # can't activate if already tapped

        # Check if card is ignored (sealed, rule 116.2)
        if hasattr(card, 'is_ignored') and card.is_ignored:
            continue

        for i, effect in enumerate(activated):
            # ── Determine cost from effect data ──────────────────────────
            cost_info = effect.effect_value if effect.effect_value else {}
            mana_cost = cost_info.get("mana_cost", 0)
            tap_cost = cost_info.get("tap_source", False)
            needs_discard = cost_info.get("discard", False)

            # ── Check mana affordability ─────────────────────────────────
            if mana_cost > 0:
                civs = defn.civilizations
                combos = _get_mana_combinations(
                    p_state.mana_zone, mana_cost, civs
                )
                if not combos:
                    continue  # can't afford mana cost
            else:
                combos = [[]]  # free activation

            # ── Check discard availability ────────────────────────────────
            if needs_discard and not p_state.hand:
                continue  # no card to discard

            # ── Generate actions ─────────────────────────────────────────
            for combo in combos:
                if needs_discard:
                    for hand_card in p_state.hand:
                        actions.append(activate_ability(
                            player=player,
                            source_uid=card.uid,
                            source_card_id=defn.id,
                            ability_index=i,
                            mana_used=combo,
                            tap_source=tap_cost,
                            discard_uid=hand_card.uid,
                        ))
                else:
                    actions.append(activate_ability(
                        player=player,
                        source_uid=card.uid,
                        source_card_id=defn.id,
                        ability_index=i,
                        mana_used=combo,
                        tap_source=tap_cost,
                    ))

    # ── Forbidden Release ability actions (Rule 809) ─────────────────────────
    # Forbidden creatures in hand can be "released" (flipped and summoned)
    # as an activated ability. This is similar to S-Trigger but from hand.
    for hand_card in p_state.hand:
        hc_def = hand_card.definition
        if hc_def.card_subtype == CardSubtype.FORBIDDEN:
            # Check if this Forbidden card has a release ability
            # Filter by phase
            activated = _filter_effects_by_phase(hc_def.get_activated_effects(), current_phase)
            for i, effect in enumerate(activated):
                if effect.effect_action == EffectAction.FORBIDDEN_RELEASE:
                    cost_info = effect.effect_value or {}
                    mana_cost = cost_info.get("mana_cost", 0)
                    if mana_cost > 0:
                        combos = _get_mana_combinations(
                            p_state.mana_zone, mana_cost, hc_def.civilizations
                        )
                    else:
                        combos = [[]]
                    for combo in combos:
                        actions.append(activate_ability(
                            player=player,
                            source_uid=hand_card.uid,
                            source_card_id=hc_def.id,
                            ability_index=i,
                            mana_used=combo,
                            tap_source=False,
                            is_forbidden_release=True,
                        ))

    # ── NEO Evolution ability actions (Rule 802) ─────────────────────────────
    # NEO creatures in the battle zone can activate their evolution ability
    # to place a new evolution stack entry (evolve in place).
    for creature in p_state.battle_zone:
        cr_def = creature.definition
        if cr_def.card_subtype in (CardSubtype.NEO_EVOLUTION, CardSubtype.NEO):
            # Filter by phase
            activated = _filter_effects_by_phase(cr_def.get_activated_effects(), current_phase)
            for i, effect in enumerate(activated):
                if effect.effect_action == EffectAction.NEO_EVOLVE:
                    cost_info = effect.effect_value or {}
                    mana_cost = cost_info.get("mana_cost", 0)
                    if mana_cost > 0:
                        combos = _get_mana_combinations(
                            p_state.mana_zone, mana_cost, cr_def.civilizations
                        )
                    else:
                        combos = [[]]
                    for combo in combos:
                        actions.append(activate_ability(
                            player=player,
                            source_uid=creature.uid,
                            source_card_id=cr_def.id,
                            ability_index=i,
                            mana_used=combo,
                            tap_source=True,
                            is_neo_evolve=True,
                        ))

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

    # Check for creatures that MUST attack
    must_attack_creatures = [
        c for c in p_state.battle_zone
        if c.can_attack() and c.temp_flags.get("must_attack", False)
    ]
    has_must_attack = len(must_attack_creatures) > 0

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

    # If there are must-attack creatures that can attack, pass is NOT legal
    # (Rule 506.1b: compelled creatures MUST attack)
    if not has_must_attack:
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

    # ── Attack Chance (rule 112.3f) ─────────────────────────────────────────
    # Turn player may cast Attack Chance spell for free when attacking creature
    # meets the card-specific condition.
    ctx = state.attack_context
    if ctx is not None and ctx.attacker_player == player:
        attacker = state.find_creature_anywhere(ctx.attacker_uid)
        attacker_defn = attacker[1].definition if attacker else None
        for hand_card in state.players[player].hand:
            defn = hand_card.definition
            if not defn.has_keyword(Keyword.ATTACK_CHANCE):
                continue
            if attacker_defn is not None and _attack_chance_condition_met(defn, attacker_defn):
                actions.append(use_attack_chance(
                    player,
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

    # Check for creatures that MUST block (and can legally block)
    must_block_creatures = [
        c for c in d_state.battle_zone
        if not c.is_ignored and not c.is_tapped
        and c.uid != ctx.target_uid
        and c.is_blocker()
        and c.temp_flags.get("must_block", False)
    ]
    has_must_block = len(must_block_creatures) > 0

    for creature in d_state.battle_zone:
        if creature.is_ignored:          # rule 116.2
            continue
        if creature.is_tapped:           # rule 507.1a example
            continue
        if creature.uid == ctx.target_uid:  # rule 507.1a: attacked creature can't self-block
            continue
        # CANNOT_BLOCK: this creature cannot be chosen as a blocker
        if creature.temp_flags.get("cannot_block", False):
            continue

        # ── Blocker (rule 507.1a) ─────────────────────────────────────────
        if creature.is_blocker():
            actions.append(declare_blocker(defender, creature.uid, creature.id))

        # ── Guardman (rule 507.1a) ────────────────────────────────────────
        # Guardman is not usable when the player is attacked directly.
        if creature.is_guardman() and ctx.is_attacking_creature:
            actions.append(declare_guardman(defender, creature.uid, creature.id))

    # If there are must-block creatures that can legally block, pass is NOT legal
    if not has_must_block:
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
    if state.effective_shield_count(defender) == 0:
        return [pass_action(player, "direct_attack")]

    # Check for simultaneous T+W Breaker (Rule 509.2c)
    attacker_result = state.find_creature_anywhere(ctx.attacker_uid)
    has_triple = False
    has_double = False
    if attacker_result is not None:
        _, attacker = attacker_result
        has_triple = attacker.has_keyword(Keyword.TRIPLE_BREAKER)
        has_double = attacker.has_keyword(Keyword.DOUBLE_BREAKER)

    actions: list[Action] = []
    if has_triple and has_double:
        # Rule 509.2c: player MUST choose which breaker to use.
        # Single-break not offered. Generate paired choices for each shield.
        from core.actions import Action as _Action
        for i in range(state.effective_shield_count(defender)):
            actions.append(_Action(
                player=player,
                action_type=ActionType.SELECT_ATTACK_ORDER,
                shield_index=i,
                extra=(("break_mode", "triple"),),
            ))
            actions.append(_Action(
                player=player,
                action_type=ActionType.SELECT_ATTACK_ORDER,
                shield_index=i,
                extra=(("break_mode", "double"),),
            ))
    else:
        # Normal path — one SELECT_ATTACK_ORDER per shield position
        for i in range(state.effective_shield_count(defender)):
            from core.actions import select_shield_to_break
            actions.append(select_shield_to_break(player, shield_index=i))

    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Shield break window  (rule 113.6, 509.5a-e)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_shield_break_window_actions(state: GameState) -> list[Action]:
    """Batch shield break declarations and resolution."""
    win = state.effect_stack.shield_break_window
    if win is None:
        return []

    player = win.defending_player
    actions: list[Action] = []

    if win.phase == "declare":
        for shield in win.standby_shields:
            defn = shield.definition
            uid = shield.uid
            if defn.has_shield_trigger() and uid not in win.declared_s_triggers:
                actions.append(use_shield_trigger(player, uid, defn.id, use=True))
            if defn.has_keyword(Keyword.G_STRIKE) and uid not in win.declared_g_strikes:
                actions.append(use_g_strike(player, uid, defn.id, use=True))
            for hand_card in state.players[player].hand:
                if hand_card.definition.has_keyword(Keyword.S_BACK):
                    pair = (hand_card.uid, uid)
                    if pair not in win.declared_s_backs:
                        actions.append(use_s_back(
                            player,
                            hand_card.uid, hand_card.id,
                            uid, defn.id,
                        ))
        actions.append(pass_action(player, "finish_shield_declarations"))
        return actions

    if win.phase == "resolve":
        for kind, primary, secondary in win.pending_resolutions:
            key = f"{kind}:{primary}:{secondary}"
            if key in win.resolved_keys:
                continue
            if kind == "s_trigger":
                hand_card = state.players[player].find_in_hand(primary)
                if hand_card is None:
                    continue
                actions.append(use_shield_trigger(
                    player, primary, hand_card.definition.id, use=True,
                ))
            elif kind == "g_strike":
                hand_card = state.players[player].find_in_hand(primary)
                card_id = hand_card.definition.id if hand_card else 0
                actions.append(use_g_strike(player, primary, card_id, use=True))
            elif kind == "s_back":
                hand_card = state.players[player].find_in_hand(primary)
                if hand_card is None:
                    continue
                actions.append(use_s_back(
                    player,
                    primary, hand_card.definition.id,
                    secondary, 0,
                ))
        for emblem_uid in win.emblems_added:
            for hand_card in state.players[player].hand:
                if not hand_card.definition.has_keyword(Keyword.SABAKI_Z):
                    continue
                key = f"sabaki_z:{hand_card.uid}:{emblem_uid}"
                if key in win.resolved_keys:
                    continue
                actions.append(use_sabaki_z(
                    player,
                    hand_card.uid, hand_card.id,
                    emblem_uid,
                ))
        if actions:
            return actions
        actions.append(pass_action(player, "finish_shield_resolution"))
        return actions

    return [pass_action(player, "finish_shield_resolution")]


# ─────────────────────────────────────────────────────────────────────────────
# Shield trigger declarations  (legacy single-shield fallback)
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

    # ── S-Trigger batch (rule 509.5a / 112.3a) ────────────────────────────
    # Rule 112.3a: If multiple cards with S-Trigger are added from shields
    # to your hand simultaneously, you show and declare ALL cards you will
    # use S-Trigger for. Once all declarations are finished, execute those
    # cards one by one.
    batch_shields = [
        (p, s) for p, s in queue
        if p == shield_player and s.definition.has_shield_trigger()
    ]

    if len(batch_shields) > 1:
        # Multiple S-Trigger shields broken simultaneously — batch declaration
        for p, s in batch_shields:
            actions.append(use_shield_trigger(
                p, s.uid, s.definition.id, use=True
            ))
            actions.append(use_shield_trigger(
                p, s.uid, s.definition.id, use=False
            ))
    else:
        # Single shield — standard S-Trigger offer
        if defn.has_shield_trigger():
            actions.append(use_shield_trigger(
                shield_player, shield_card.uid, defn.id, use=True
            ))
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

    # ── Sabaki Z handled after hand add in shield break window (509.5d) ───

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
            zone = choice.effect.active_in_zone[0] if choice.effect and choice.effect.active_in_zone else "battle_zone"
            actions.append(select_target(
                player, target_uid,
                zone,
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

    elif choice.choice_type == "select_mana":
        # Generate one select_mana action per valid mana combination.
        # The effect's effect_value may carry cost/civ info; if not,
        # fall back to all untapped mana in the player's zone.
        p_state = state.players[player]
        mana_zone = p_state.mana_zone
        ev = choice.effect.effect_value if choice.effect else {}
        cost = int(ev.get("cost", 0)) if ev else 0
        card_civs: frozenset = frozenset()
        if choice.effect and len(choice.effect.civilizations) > 0:
            card_civs = choice.effect.civilizations
        combos = _get_mana_combinations(mana_zone, cost, card_civs)
        for combo in combos:
            actions.append(select_mana(player, list(combo), choice.source_uid))
        if not actions:
            # No valid mana combos — allow pass to decline
            actions.append(pass_action(player, "select_mana_none"))

    elif choice.choice_type == "select_targets":
        # Multi-target: generate one action per valid target uid.
        for target_uid in options:
            actions.append(select_targets(
                player, [target_uid],
                choice.effect.active_in_zone[0] if choice.effect and choice.effect.active_in_zone else "battle_zone",
                choice.source_uid,
            ))
        # "up to N" effects allow choosing fewer targets → include pass
        if choice.min_choices == 0:
            actions.append(pass_action(player, "select_targets_done"))

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


def _king_combine_actions(player: int, state: GameState, db) -> list[Action]:
    """
    Rule 814.1c: combine King Cells from hand/mana into a King Creature.
    """
    actions: list[Action] = []
    p_state = state.players[player]

    available: dict[str, list[str]] = {}
    for hand_card in p_state.hand:
        if hand_card.definition.is_king_cell():
            available.setdefault(hand_card.definition.slug, []).append(hand_card.uid)
    for mana_card in p_state.mana_zone:
        if mana_card.definition.is_king_cell():
            available.setdefault(mana_card.definition.slug, []).append(mana_card.uid)

    for defn in db.all_cards():
        if not defn.is_king_creature():
            continue
        required = sorted(defn.king_combine_required_slugs)
        if not required or not all(slug in available for slug in required):
            continue

        uid_lists = [available[slug] for slug in required]
        for pick in itertools.product(*uid_lists):
            if len(set(pick)) != len(pick):
                continue
            cell_uids = list(pick)
            combos = _get_mana_combinations(
                p_state.mana_zone,
                defn.cost,
                defn.civilizations,
            )
            for combo in combos:
                actions.append(
                    combine_king_creature(player, defn.id, cell_uids, combo)
                )

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
      - Match the evolution requirements:
        * At least one matching race from defn.evolution_source_races, OR
        * At least one matching type from defn.evolution_source_types
      - Not be ignored (rule 116.2: ignored creatures can't be evolved onto)

    Special cases:
      - S-MAX Evolution (rule 815): no base required — handled in caller.
      - Star Evolution (rule 813): needs a base of any creature.
      - Forbidden Star Evolution (rule 813.1b): can summon without base.
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

        # Check evolution requirements: race OR type match
        has_race_match = (
            defn.evolution_source_races and
            defn.evolution_source_races.intersection(creature.definition.races)
        )
        has_type_match = (
            defn.evolution_source_types and
            creature.definition.card_type in defn.evolution_source_types
        )

        if defn.evolution_source_races or defn.evolution_source_types:
            # At least one requirement exists → must match at least one
            if has_race_match or has_type_match:
                valid.append(creature)
        else:
            # No specific requirements → any creature is a valid base
            # (e.g., Star Evolution, generic evolution)
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
        # Rule 112.1a: cost reduced by number of creatures of matching race.
        # Reads the sympathy race from effect_target on COST_REDUCE effects.
        import json
        found_sympathy = False
        for effect in defn.effects:
            if effect.effect_action != EffectAction.COST_REDUCE:
                continue
            if effect.trigger_event is not None:
                continue  # only static cost reductions apply here
            try:
                target = json.loads(effect.effect_target or "{}")
            except (ValueError, TypeError):
                continue
            sympathy_race = target.get("race")
            if sympathy_race is None:
                continue
            found_sympathy = True
            count = sum(
                1 for c in state.players[player].battle_zone
                if sympathy_race.lower() in [r.lower() for r in c.definition.races]
            )
            modification -= count
        # Fallback: if no effect matches, use old race-iteration heuristic
        if not found_sympathy:
            for race in defn.races:
                for creature in state.players[player].battle_zone:
                    if race in creature.races:
                        modification -= 1

    # ── Global cost modifiers from active card effects ───────────────────
    global_mod = state.global_effects.get_cost_modifiers(player, defn.id)
    modification += global_mod

    # ── Clamp to zero (cost cannot go negative) ────────────────────────
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
    Reads the condition from card_effects (effect_action == COST_MOD with
    a trigger_condition). Falls back to False (conservative) when no
    condition is parseable.
    """
    for effect in defn.effects:
        if effect.effect_type != EffectType.COST_MOD:
            continue
        if not effect.trigger_condition:
            continue
        import json
        try:
            condition = json.loads(effect.trigger_condition)
        except (ValueError, TypeError):
            continue
        return _evaluate_condition(condition, state, player)
    return False


def _evaluate_condition(
    condition: dict,
    state: GameState,
    player: int,
) -> bool:
    """Evaluate a parsed G-Zero trigger condition against the current state."""
    cond_type = condition.get("type", "")
    if cond_type == "own_creature_count_gte":
        threshold = condition.get("value", 1)
        return len(state.players[player].battle_zone) >= threshold
    elif cond_type == "own_shield_count_lte":
        threshold = condition.get("value", 0)
        return state.effective_shield_count(player) <= threshold
    elif cond_type == "opponent_shield_count_lte":
        threshold = condition.get("value", 0)
        return state.effective_shield_count(1 - player) <= threshold
    elif cond_type == "own_mana_count_gte":
        threshold = condition.get("value", 1)
        return state.players[player].mana_count >= threshold
    elif cond_type == "own_creature_race":
        target_race = condition.get("race", "")
        if not target_race:
            return False
        return any(
            target_race.lower() in [r.lower() for r in c.definition.races]
            for c in state.players[player].battle_zone
        )
    # Unknown condition type — safe fallback
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Ninja Strike cost check  (rule 112.3c)
# ─────────────────────────────────────────────────────────────────────────────

_NINJA_STRIKE_RE = re.compile(r"ninja\s*strike\s+(\d+)", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
# Over Drive cost check  (rule 112.2d)
# ─────────────────────────────────────────────────────────────────────────────

_OVER_DRIVE_RE = re.compile(
    r"over\s*drive\s*[-—]\s*((?:[\w\s]+\s*x\d+\s*)+)",
    re.IGNORECASE
)


def _get_over_drive_requirements(defn: CardDefinition) -> Optional[dict]:
    """
    Rule 112.2d: Over Drive — when you summon this creature, you may tap
    another N cards of specified civilization(s) in your mana zone.

    Returns a dict like: {Civilization.FIRE: 2, Civilization.LIGHT: 1}
    or None if the card doesn't have Over Drive.
    """
    # Pattern to match each "CIV xN" part
    civ_count_re = re.compile(r"(\w+)\s*x(\d+)", re.IGNORECASE)

    for effect in defn.effects:
        raw = effect.raw_text or ""
        # First find the Over Drive section
        od_match = _OVER_DRIVE_RE.search(raw)
        if not od_match:
            continue

        od_text = od_match.group(1)
        result = {}

        # Map civilization name to Civilization enum
        civ_map = {
            "water": Civilization.WATER,
            "fire": Civilization.FIRE,
            "nature": Civilization.NATURE,
            "light": Civilization.LIGHT,
            "darkness": Civilization.DARKNESS,
        }

        # Find all "CIV xN" patterns
        for match in civ_count_re.finditer(od_text):
            civ_name = match.group(1).strip().lower()
            count = int(match.group(2))
            civ = civ_map.get(civ_name)
            if civ:
                result[civ] = count

        if result:
            return result
    return None


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


def _over_drive_mana_combos(
    mana_zone: list[ManaCard],
    requirements: dict,
) -> list[list[ManaUsage]]:
    """All valid mana tap combinations for an Over Drive additional cost."""
    civ_items = list(requirements.items())
    per_civ_choices: list[list[ManaUsage]] = []

    for civ, count in civ_items:
        available = [m for m in mana_zone if civ in m.definition.civilizations]
        if len(available) < count:
            return []
        civ_combos: list[list[ManaUsage]] = []
        for mana_cards in itertools.combinations(available, count):
            civ_combos.append([
                ManaUsage(mana_uid=m.uid, used_for_civ=civ)
                for m in mana_cards
            ])
        per_civ_choices.append(civ_combos)

    if not per_civ_choices:
        return []

    results: list[list[ManaUsage]] = []
    for combo_tuple in itertools.product(*per_civ_choices):
        merged: list[ManaUsage] = []
        used_uids: set[str] = set()
        valid = True
        for part in combo_tuple:
            for usage in part:
                if usage.mana_uid in used_uids:
                    valid = False
                    break
                used_uids.add(usage.mana_uid)
                merged.append(usage)
            if not valid:
                break
        if valid:
            results.append(merged)
    return results


_ATTACK_CHANCE_CIV_RE = re.compile(
    r"attack\s*chance[^a-z0-9]*(fire|water|nature|light|darkness)",
    re.IGNORECASE,
)


def _attack_chance_condition_met(
    spell_defn: CardDefinition,
    attacker_defn: CardDefinition,
) -> bool:
    """
    Rule 112.3f: Attack Chance fires under card-specific conditions.
    Falls back to any attack if no condition is parseable.
    """
    for effect in spell_defn.effects:
        raw = effect.raw_text or ""
        match = _ATTACK_CHANCE_CIV_RE.search(raw)
        if match:
            civ_name = match.group(1).lower()
            civ_map = {
                "water": Civilization.WATER,
                "fire": Civilization.FIRE,
                "nature": Civilization.NATURE,
                "light": Civilization.LIGHT,
                "darkness": Civilization.DARKNESS,
            }
            required = civ_map.get(civ_name)
            if required is not None:
                return required in attacker_defn.civilizations
    return True


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
