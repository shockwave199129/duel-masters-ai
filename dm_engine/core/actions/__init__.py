"""core/actions — Action dataclass and constructor helpers.

Every possible player decision in a Duel Masters game is represented
as an Action object. The engine takes (GameState, Action) → GameState.

Design rules:
  1. Actions are IMMUTABLE (frozen dataclass) — safe to store in MCTS trees.
  2. Actions are HASHABLE — MCTS nodes use them as dict keys.
  3. Actions carry ALL information needed to execute — no ambiguity.
  4. Constructor functions (one per action type) enforce correct shape.

Rule references are cited throughout from DM Comprehensive Rules Ver. 1.50.
"""

from .base import Action
# Re-export enums for backwards compatibility (tests import these from core.actions)
from ..enums import ActionType, Civilization, ManaUsage
from .constructors import (
    charge_mana,
    pass_charge,
    summon_creature,
    cast_spell,
    activate_ability,
    generate_cross_gear,
    cross_gear,
    fortify_castle,
    deploy_field,
    execute_tamaseed,
    combine_king_creature,
    pass_main,
    attack_player,
    attack_creature,
    pass_attack,
    declare_blocker,
    declare_guardman,
    pass_block,
    select_shield_to_break,
    use_shield_trigger,
    use_s_back,
    use_ninja_strike,
    use_g_zero,
    use_attack_chance,
    use_g_strike,
    use_over_drive,
    hyperize,
    use_sabaki_z,
    select_yes_no,
    select_target,
    select_targets,
    select_mana,
    select_card,
    select_evolution_base,
    select_civilization,
    select_cards_from_list,
    pass_action,
    actions_equal,
    ACTION_TYPE_INDEX,
    NUM_ACTION_TYPES,
)
