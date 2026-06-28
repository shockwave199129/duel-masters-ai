"""engine/action_generator.py — Legal action generation orchestrator.
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

# Import phase-specific generators from engine.turn.action_gen
from engine.turn.action_gen import (
    _generate_start_of_turn_actions,
    _generate_mana_charge_actions,
    _generate_main_actions,
    _generate_attack_declarations,
    _generate_post_declare_actions,
    _generate_battle_timing_actions,
    _generate_block_actions,
    _generate_direct_attack_actions,
    _generate_shield_break_window_actions,
    _generate_shield_trigger_actions,
    _generate_choice_actions,
)


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

    # ── Priority 1: Shield break window / trigger queue ───────────────────
    if state.effect_stack.shield_break_window is not None:
        return _generate_shield_break_window_actions(state)
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



# ─────────────────────────────────────────────────────────────────────────────
# Re-exports for backwards compatibility (tests/scripts import private
# functions directly from engine.action_generator).
# ─────────────────────────────────────────────────────────────────────────────
from engine.turn.action_gen import (  # noqa: F401,F403
    _actions_for_hand_card,
    _attack_chance_condition_met,
    _combinations_no_civ,
    _compute_effective_cost,
    _evaluate_condition,
    _g_zero_condition_met,
    _generate_main_actions,
    _get_mana_combinations,
    _get_ninja_strike_cost,
    _get_over_drive_requirements,
    _get_valid_evolution_bases,
    _king_combine_actions,
    _over_drive_mana_combos,
    can_attack,
    can_play_card,
)
