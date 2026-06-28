"""
engine/battle_resolver.py — resolve Duel Masters battles.
"""

from __future__ import annotations

from core.enums import Keyword, Phase, INFINITY, TriggerEvent
from core.state import GameState
from engine.sba_checker import check_state_based_actions


def resolve_battle(state: GameState) -> GameState:
    """Resolve the current battle, mark losers, then run SBAs.
    
    Rule 108.1c: ∞ power is larger than any finite number.
    ∞ vs ∞ is a tie (both lose).
    Creature with -∞ power is destroyed.
    """
    s = state.copy()
    ctx = s.attack_context
    if ctx is None:
        return s

    attacker_result = s.find_creature_anywhere(ctx.attacker_uid)
    if attacker_result is None:
        s.turn_info.phase = Phase.END_OF_ATTACK
        return s
    _, attacker = attacker_result

    defender_uid = ctx.blocker_uid if ctx.blocker_uid else ctx.target_uid
    defender_result = s.find_creature_anywhere(defender_uid or "")
    if defender_result is None:
        s.turn_info.phase = Phase.END_OF_ATTACK
        return s
    _, defender = defender_result

    # Fire ON_BATTLE trigger (both participants)
    from engine.trigger_registry import fire_trigger
    fire_trigger(s, TriggerEvent.ON_BATTLE, {
        "attacker_uid": attacker.uid,
        "defender_uid": defender.uid,
        "attacker_player": ctx.attacker_player,
        "defender_player": ctx.blocker_player if ctx.blocker_uid else ctx.attacker_player,
    }, attacker.uid)

    attacker_always_wins = _wins_battles(attacker)
    defender_always_wins = _wins_battles(defender)
    attacker_power = attacker.compute_power(s)
    defender_power = defender.compute_power(s)

    # Rule 108.1c: -∞ power means creature is destroyed
    if attacker_power == -INFINITY:
        attacker.set_flag("lost_battle", True)
    if defender_power == -INFINITY:
        defender.set_flag("lost_battle", True)

    # Rule 108.1b: when referencing a creature's power, a negative value is
    # treated as 0. So battle comparison uses effective_power, while the
    # underlying compute_power stays accurate (so the SBA can still detect
    # zero-or-negative-power creatures for destruction at 115.3b / SBA).
    if attacker_always_wins and defender_always_wins:
        pass  # both win = neither loses (rule 115.3c)
    elif attacker_always_wins:
        defender.set_flag("lost_battle", True)
    elif defender_always_wins:
        attacker.set_flag("lost_battle", True)
    else:
        eff_attacker = attacker.effective_power(s)
        eff_defender = defender.effective_power(s)
        if eff_attacker == eff_defender:
            # ∞ vs ∞ is a tie — both lose (rule 115.3b)
            attacker.set_flag("lost_battle", True)
            defender.set_flag("lost_battle", True)
        elif eff_attacker > eff_defender:
            # INFINITY > any finite number handles ∞ vs normal automatically
            defender.set_flag("lost_battle", True)
        else:
            attacker.set_flag("lost_battle", True)

    # Slayer revenge-on-loss (OCG rule): after power comparison, if a Slayer
    # creature lost the battle, the opposing creature is also destroyed.
    # Slayer does NOT auto-win or grant survival — it only triggers on loss.
    if attacker.temp_flags.get("lost_battle", False) and attacker.has_keyword(Keyword.SLAYER):
        defender.set_flag("lost_battle", True)
    if defender.temp_flags.get("lost_battle", False) and defender.has_keyword(Keyword.SLAYER):
        attacker.set_flag("lost_battle", True)

    # Fire ON_WIN_BATTLE triggers (recompute after Slayer)
    attacker_lost = attacker.temp_flags.get("lost_battle", False)
    defender_lost = defender.temp_flags.get("lost_battle", False)

    if not attacker_lost:
        fire_trigger(s, TriggerEvent.ON_WIN_BATTLE, {
            "source_uid": attacker.uid,
            "source_card_id": attacker.id,
            "controller": ctx.attacker_player,
            "opponent_uid": defender.uid,
        }, attacker.uid)
    
    if not defender_lost:
        fire_trigger(s, TriggerEvent.ON_WIN_BATTLE, {
            "source_uid": defender.uid,
            "source_card_id": defender.id,
            "controller": ctx.blocker_player if ctx.blocker_uid else ctx.attacker_player,
            "opponent_uid": attacker.uid,
        }, defender.uid)

    s.turn_info.phase = Phase.END_OF_ATTACK
    return check_state_based_actions(s)


def _wins_battles(creature) -> bool:
    """Temporary effect hook for abilities that say this creature wins battles."""
    return bool(
        creature.temp_flags.get("wins_battles", False)
        or creature.temp_flags.get("win_battle", False)
    )
