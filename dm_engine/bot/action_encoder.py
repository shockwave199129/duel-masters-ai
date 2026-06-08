"""Action encoders for neural bots."""

from __future__ import annotations

from typing import Any

from core.actions import Action
from core.enums import ActionType, CardType, Civilization, Keyword, Phase


ACTION_VECTOR_SIZE_V1 = 14
ACTION_ENCODER_VERSION = 2
_ACTION_TYPES = list(ActionType)
_CIVILIZATIONS = list(Civilization)
_CARD_TYPES = [
    CardType.CREATURE,
    CardType.SPELL,
    CardType.CROSS_GEAR,
    CardType.CASTLE,
    CardType.TAMASEED,
]
_KEYWORDS = [
    Keyword.BLOCKER,
    Keyword.SPEED_ATTACKER,
    Keyword.DOUBLE_BREAKER,
    Keyword.TRIPLE_BREAKER,
    Keyword.SHIELD_TRIGGER,
]
_ACTION_CATEGORIES = [
    "pass",
    "mana_charge",
    "skip_mana_charge",
    "play_card",
    "attack",
    "block",
    "trigger_free",
    "choice",
]
_TARGET_TYPES = [
    "none",
    "player",
    "creature",
    "shield",
    "hand",
    "mana",
    "graveyard",
    "other",
]
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


def _norm(value: float, maximum: float) -> float:
    if maximum <= 0:
        return 0.0
    return max(0.0, min(float(value), float(maximum))) / float(maximum)


def _one_hot(value: object, values: list) -> list[float]:
    return [1.0 if value == item else 0.0 for item in values]


def _extra(action: Action) -> dict[str, Any]:
    return dict(action.extra)


def _action_category(action: Action) -> str:
    if action.action_type == ActionType.PASS:
        step = _extra(action).get("step")
        return "skip_mana_charge" if step == "mana_charge" else "pass"
    if action.action_type == ActionType.CHARGE_MANA:
        return "mana_charge"
    if action.action_type in {
        ActionType.SUMMON_CREATURE,
        ActionType.CAST_SPELL,
        ActionType.GENERATE_CROSS_GEAR,
        ActionType.CROSS_GEAR,
        ActionType.FORTIFY_CASTLE,
        ActionType.DEPLOY_FIELD,
        ActionType.EXECUTE_TAMASEED,
    }:
        return "play_card"
    if action.action_type in {ActionType.ATTACK_PLAYER, ActionType.ATTACK_CREATURE}:
        return "attack"
    if action.action_type in {ActionType.DECLARE_BLOCKER, ActionType.DECLARE_GUARDMAN}:
        return "block"
    if action.is_free_execution():
        return "trigger_free"
    if action.action_type in {
        ActionType.SELECT_TARGET,
        ActionType.SELECT_MANA,
        ActionType.SELECT_CARD,
        ActionType.SELECT_YES_NO,
        ActionType.SELECT_ATTACK_ORDER,
        ActionType.SELECT_EVOLUTION_BASE,
    }:
        return "choice"
    return "choice"


def _card_from_db(card_id: int | None, db):
    if card_id is None or db is None:
        return None
    try:
        return db.get(card_id)
    except Exception:
        return None


def _card_metadata(card) -> list[float]:
    if card is None:
        return [0.0] * (5 + len(_CIVILIZATIONS) + len(_CARD_TYPES) + 1 + len(_KEYWORDS))
    civs = getattr(card, "civilizations", frozenset()) or frozenset()
    keywords = getattr(card, "keywords", frozenset()) or frozenset()
    card_type = getattr(card, "card_type", None)
    return [
        _bool(card),
        _norm(getattr(card, "id", 0) or 0, 20000),
        _norm(getattr(card, "cost", 0) or 0, 15),
        _norm(getattr(card, "power", 0) or 0, 20000),
        _bool(getattr(card, "is_multiface", False)),
        *[1.0 if civ in civs else 0.0 for civ in _CIVILIZATIONS],
        *_one_hot(card_type, _CARD_TYPES),
        0.0 if card_type in _CARD_TYPES else 1.0,
        *[1.0 if keyword in keywords else 0.0 for keyword in _KEYWORDS],
    ]


def _target_type(action: Action) -> str:
    if not action.target_uid and action.target_zone is None:
        return "none"
    if action.target_uid and action.target_uid.startswith("player_"):
        return "player"
    zone = action.target_zone or ""
    if zone == "battle_zone" or action.action_type in {ActionType.ATTACK_CREATURE, ActionType.DECLARE_BLOCKER}:
        return "creature"
    if "shield" in zone:
        return "shield"
    if "hand" in zone:
        return "hand"
    if "mana" in zone:
        return "mana"
    if "graveyard" in zone:
        return "graveyard"
    return "other"


def _find_target_creature(state, target_uid: str | None):
    if state is None or not target_uid:
        return None, None
    try:
        return state.find_creature_anywhere(target_uid) or (None, None)
    except Exception:
        for index, player in enumerate(getattr(state, "players", [])):
            for creature in getattr(player, "battle_zone", []):
                if creature.uid == target_uid:
                    return index, creature
    return None, None


def _target_metadata(action: Action, state) -> list[float]:
    target_type = _target_type(action)
    controller, creature = _find_target_creature(state, action.target_uid)
    card = getattr(creature, "definition", None)
    return [
        *_one_hot(target_type, _TARGET_TYPES),
        _norm(float(controller), 1.0) if controller is not None else 0.0,
        _bool(creature),
        _norm(getattr(card, "id", 0) if card is not None else 0, 20000),
        _norm(creature.compute_power(state) if creature is not None else 0, 20000),
        _bool(getattr(creature, "is_tapped", False)),
    ]


def _remaining_mana_features(action: Action, state) -> list[float]:
    if state is None:
        return [0.0] * (2 + len(_CIVILIZATIONS))
    player_state = state.players[action.player]
    used_uids = {usage.mana_uid for usage in action.mana_used}
    remaining = [mana for mana in player_state.mana_zone if not mana.is_tapped and mana.uid not in used_uids]
    features = [
        _norm(len(action.mana_used), 10),
        _norm(len(remaining), 20),
    ]
    for civ in _CIVILIZATIONS:
        features.append(_norm(sum(1 for mana in remaining if civ in mana.civilizations), 10))
    return features


def _has_followup_play(action: Action, state, db) -> float:
    if state is None or db is None or state.current_phase != Phase.MAIN:
        return 0.0
    if not action.costs_mana() and not action.is_free_execution():
        return 0.0
    from engine.action_generator import _get_mana_combinations

    player_state = state.players[action.player]
    used_uids = {usage.mana_uid for usage in action.mana_used}
    remaining_mana = [mana for mana in player_state.mana_zone if not mana.is_tapped and mana.uid not in used_uids]
    for hand_card in player_state.hand:
        if hand_card.uid == action.card_uid:
            continue
        definition = hand_card.definition
        if _get_mana_combinations(remaining_mana, definition.cost, definition.civilizations):
            return 1.0
    return 0.0


def _payment_combo_count(action: Action, state, db) -> float:
    if state is None or db is None or action.card_id is None:
        return 0.0
    card = _card_from_db(action.card_id, db)
    if card is None or not action.costs_mana():
        return 0.0
    from engine.action_generator import _get_mana_combinations

    combos = _get_mana_combinations(state.players[action.player].mana_zone, card.cost, card.civilizations)
    return _norm(len(combos), 50)


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


def encode_action_v2(action: Action, state=None, db=None) -> list[float]:
    """Encode a legal action with card, target, resource, and phase context."""
    card = _card_from_db(action.card_id, db)
    required_civs = getattr(card, "civilizations", frozenset()) or frozenset()
    selected_civ = action.selected_civ
    shield_index = action.shield_index if action.shield_index is not None else -1
    extra = _extra(action)

    features: list[float] = [
        *_one_hot(action.action_type, _ACTION_TYPES),
        *_one_hot(_action_category(action), _ACTION_CATEGORIES),
        _norm(action.player, 1),
        _bool(action.card_uid),
        _bool(action.target_uid),
        _bool(action.evolution_base_uid),
        _bool(action.discard_uid),
        _norm(len(action.selected_uids), 10),
        _norm(shield_index + 1, 5) if shield_index >= 0 else 0.0,
        _bool(action.choice),
        _bool(action.is_attack()),
        _bool(action.costs_mana()),
        _bool(action.is_free_execution()),
        _bool(extra.get("step") == "mana_charge"),
        _bool(extra.get("step") == "main"),
    ]
    features.extend(_card_metadata(card))
    features.extend(_target_metadata(action, state))
    features.extend([1.0 if civ in required_civs else 0.0 for civ in _CIVILIZATIONS])
    features.extend(_one_hot(selected_civ, _CIVILIZATIONS))
    features.extend([
        _norm(getattr(card, "cost", 0) if card is not None else 0, 15),
        _bool(len(action.mana_used) >= len(required_civs) if required_civs else bool(action.mana_used) or not action.costs_mana()),
        _payment_combo_count(action, state, db),
    ])
    features.extend(_remaining_mana_features(action, state))
    features.append(_has_followup_play(action, state, db))
    return features


def feature_schema_v2() -> dict[str, object]:
    return {
        "version": ACTION_ENCODER_VERSION,
        "action_types": [action_type.value for action_type in _ACTION_TYPES],
        "categories": list(_ACTION_CATEGORIES),
        "civilizations": [civ.value for civ in _CIVILIZATIONS],
        "card_types": [card_type.value for card_type in _CARD_TYPES],
        "keywords": [keyword.value for keyword in _KEYWORDS],
        "target_types": list(_TARGET_TYPES),
        "vector_size": ACTION_VECTOR_SIZE_V2,
    }


ACTION_VECTOR_SIZE_V2 = len(encode_action_v2(Action(player=0, action_type=ActionType.PASS)))
ACTION_VECTOR_SIZE = ACTION_VECTOR_SIZE_V2
