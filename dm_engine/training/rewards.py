"""Heuristic reward shaping for v2 neural self-play rows."""

from __future__ import annotations

from core.state import GameState


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _battle_power(state: GameState, player: int) -> int:
    return sum(creature.compute_power(state) for creature in state.players[player].battle_zone)


def heuristic_state_value(state: GameState, perspective: int) -> float:
    """
    Estimate state quality from one player's perspective.

    This is intentionally simple and public-state friendly. It gives every
    non-terminal decision a learning signal while terminal win/loss still
    remains the strongest target.
    """
    me = state.players[perspective]
    opp = state.players[1 - perspective]

    shield_adv = (me.shield_count - opp.shield_count) / 5.0
    hand_adv = (me.hand_count - opp.hand_count) / 10.0
    mana_adv = (me.mana_count - opp.mana_count) / 10.0
    available_mana_adv = (me.available_mana - opp.available_mana) / 10.0
    creature_adv = (me.battle_zone_count - opp.battle_zone_count) / 8.0
    power_adv = (_battle_power(state, perspective) - _battle_power(state, 1 - perspective)) / 30000.0
    deck_pressure = (me.deck_size - opp.deck_size) / 40.0

    value = (
        0.25 * shield_adv
        + 0.15 * hand_adv
        + 0.15 * mana_adv
        + 0.05 * available_mana_adv
        + 0.20 * creature_adv
        + 0.15 * power_adv
        + 0.05 * deck_pressure
    )
    return _clamp(value)


def blend_targets(value_target: float, heuristic_target: float, terminal_weight: float = 0.65) -> float:
    """Blend eventual win/loss target with shaped state value."""
    weight = _clamp(terminal_weight, 0.0, 1.0)
    return _clamp(weight * value_target + (1.0 - weight) * heuristic_target)
