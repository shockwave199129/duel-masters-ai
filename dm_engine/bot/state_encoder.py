"""
bot/state_encoder.py — simple fixed-order observation encoder scaffold.
"""

from __future__ import annotations

from core.observation import Observation
from core.state import GameState


OBSERVATION_VECTOR_SIZE = 14


def encode_observation(state: GameState, perspective: int) -> list[float]:
    """
    Encode public/allowed information for a player.

    This intentionally uses Observation instead of raw GameState so hidden
    shield, deck, and hand information does not leak into AI inputs.
    """
    obs = Observation.build(state, perspective)
    me = obs.self_state
    opp = obs.opponent_state

    return [
        float(obs.turn_number),
        float(obs.active_player == perspective),
        float(me.hand_count),
        float(me.deck_size),
        float(me.shield_count),
        float(me.mana_count),
        float(me.available_mana),
        float(len(me.battle_zone)),
        float(opp.hand_count),
        float(opp.deck_size),
        float(opp.shield_count),
        float(opp.mana_count),
        float(opp.available_mana),
        float(len(opp.battle_zone)),
    ]
