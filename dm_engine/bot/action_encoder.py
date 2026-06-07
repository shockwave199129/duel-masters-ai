"""
bot/action_encoder.py — fixed-size numeric features for legal actions.
"""

from __future__ import annotations

from core.actions import Action
from core.enums import ActionType, Civilization


ACTION_VECTOR_SIZE = 14
_ACTION_TYPES = list(ActionType)
_CIVILIZATIONS = list(Civilization)
_TARGET_ZONES = {
    "hand": 1,
    "deck": 2,
    "mana_zone": 3,
    "battle_zone": 4,
    "graveyard": 5,
    "shield_zone": 6,
    "abyss_zone": 7,
    "hyperspatial_zone": 8,
    "ultra_gr_zone": 9,
}


def _enum_fraction(value, values: list) -> float:
    if value not in values or len(values) <= 1:
        return 0.0
    return float(values.index(value)) / float(len(values) - 1)


def _bool(value: object) -> float:
    return 1.0 if value else 0.0


def _count(value: int, maximum: int) -> float:
    return min(float(value), float(maximum)) / float(maximum)


def encode_action(action: Action) -> list[float]:
    """Encode an action without looking at hidden state."""
    card_id = action.card_id or 0
    shield_index = action.shield_index if action.shield_index is not None else -1
    selected_civ = action.selected_civ
    target_zone_index = _TARGET_ZONES.get(action.target_zone or "", 0)

    return [
        _enum_fraction(action.action_type, _ACTION_TYPES),
        float(action.player),
        min(float(card_id), 20000.0) / 20000.0,
        _bool(action.card_uid),
        _bool(action.target_uid),
        float(target_zone_index) / float(max(_TARGET_ZONES.values())),
        _count(len(action.mana_used), 10),
        _bool(action.evolution_base_uid),
        _bool(action.discard_uid),
        _count(len(action.selected_uids), 10),
        _enum_fraction(selected_civ, _CIVILIZATIONS),
        float(shield_index + 1) / 5.0 if shield_index >= 0 else 0.0,
        _bool(action.is_attack()),
        _bool(action.costs_mana() or action.is_free_execution()),
    ]
