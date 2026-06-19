"""Action encoders for neural bots."""

from __future__ import annotations

from typing import Any

from core.actions import Action
from core.enums import ActionType, CardType, Civilization, Keyword, Phase
from rules import RuleKnowledgeService


ACTION_VECTOR_SIZE_V1 = 14
ACTION_ENCODER_VERSION = 2
ACTION_ENCODER_VERSION_V3 = 3
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
_RULE_CATEGORIES = [
    "win_loss",
    "turn_structure",
    "cost_payment",
    "trigger",
    "replacement",
    "keyword",
    "zone_rule",
    "special_card",
    "state_based",
    "general",
]
_ACTION_RULE_REFS = {
    ActionType.CHARGE_MANA: ("503.1",),
    ActionType.SUMMON_CREATURE: ("112.2a", "301.1"),
    ActionType.CAST_SPELL: ("112.2a", "302.1"),
    ActionType.GENERATE_CROSS_GEAR: ("112.2a", "303.1"),
    ActionType.CROSS_GEAR: ("112.2a", "303.3b"),
    ActionType.FORTIFY_CASTLE: ("304.1",),
    ActionType.DEPLOY_FIELD: ("308.1",),
    ActionType.EXECUTE_TAMASEED: ("315.1",),
    ActionType.ATTACK_PLAYER: ("104.2a", "506.1", "509.1"),
    ActionType.ATTACK_CREATURE: ("506.1", "506.3", "115.3"),
    ActionType.DECLARE_BLOCKER: ("507.1",),
    ActionType.DECLARE_GUARDMAN: ("507.1",),
    ActionType.USE_SHIELD_TRIGGER: ("112.3a", "113.6"),
    ActionType.USE_S_BACK: ("112.3b", "113.6"),
    ActionType.USE_NINJA_STRIKE: ("112.3c",),
    ActionType.USE_G_ZERO: ("112.3e",),
    ActionType.USE_ATTACK_CHANCE: ("112.3f",),
    ActionType.USE_G_STRIKE: ("101.4b", "113.6"),
    ActionType.SELECT_ATTACK_ORDER: ("509.2",),
    ActionType.PASS: ("500.1",),
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


def _safe_rule_service(rule_service) -> RuleKnowledgeService | None:
    return rule_service if isinstance(rule_service, RuleKnowledgeService) else rule_service


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
        ActionType.COMBINE_KING_CREATURE,
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


def _find_card_in_hand(state, player: int, card_uid: str | None):
    if state is None or not card_uid:
        return None
    return next((card for card in state.players[player].hand if card.uid == card_uid), None)


def _find_source_creature(state, action: Action):
    if state is None or not action.card_uid:
        return None
    result = state.find_creature_anywhere(action.card_uid)
    if result is None:
        return None
    return result[1]


def _card_keywords(card) -> frozenset[Keyword]:
    definition = getattr(card, "definition", card)
    return frozenset(getattr(definition, "keywords", frozenset()) or frozenset())


def _card_cost(card) -> int:
    definition = getattr(card, "definition", card)
    return int(getattr(definition, "cost", 0) or 0)


def _card_power(card, state=None) -> int:
    if card is None:
        return 0
    if hasattr(card, "compute_power"):
        try:
            return int(card.compute_power(state) or 0)
        except TypeError:
            return int(card.compute_power() or 0)
    definition = getattr(card, "definition", card)
    return int(getattr(definition, "power", 0) or 0)


def _break_count(card) -> int:
    definition = getattr(card, "definition", card)
    try:
        shields = int(definition.shields_broken())
    except Exception:
        shields = 1
    return 5 if shields >= 999 else max(1, shields)


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
        0.0,  # Seat-neutral: state features are already encoded from this player's perspective.
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


def _charge_action_features(action: Action, state, db) -> list[float]:
    if action.action_type != ActionType.CHARGE_MANA or state is None:
        return [0.0] * 6
    card = _find_card_in_hand(state, action.player, action.card_uid)
    if card is None:
        card = _card_from_db(action.card_id, db)
    if card is None:
        return [0.0] * 6
    player_state = state.players[action.player]
    currently_playable = _bool(_card_cost(card) <= player_state.available_mana)
    current_civs = player_state.all_mana_civilizations()
    fixes_color = any(civ not in current_civs for civ in getattr(card, "civilizations", frozenset()))
    keywords = _card_keywords(card)
    return [
        currently_playable,
        _bool(fixes_color),
        _bool(Keyword.SHIELD_TRIGGER in keywords),
        _bool(Keyword.BLOCKER in keywords),
        _bool(Keyword.SPEED_ATTACKER in keywords),
        _bool(len(getattr(card, "civilizations", frozenset())) > 1),
    ]


def _play_action_features(action: Action, state, db) -> list[float]:
    if state is None or not (action.costs_mana() or action.is_free_execution()):
        return [0.0] * 8
    card = _card_from_db(action.card_id, db)
    if card is None:
        hand_card = _find_card_in_hand(state, action.player, action.card_uid)
        card = getattr(hand_card, "definition", None)
    keywords = _card_keywords(card)
    remaining = _remaining_mana_features(action, state)
    cost = _card_cost(card)
    power = _card_power(card)
    spent_ratio = _norm(len(action.mana_used), 10)
    return [
        spent_ratio,
        remaining[1],
        _has_followup_play(action, state, db),
        _norm(cost, 15),
        _norm(power, 20000),
        _bool(Keyword.SHIELD_TRIGGER in keywords),
        _bool(Keyword.SPEED_ATTACKER in keywords),
        _bool(Keyword.BLOCKER in keywords),
    ]


def _attack_action_features(action: Action, state) -> list[float]:
    if state is None or not action.is_attack():
        return [0.0] * 9
    attacker = _find_source_creature(state, action)
    controller, target = _find_target_creature(state, action.target_uid)
    defender = 1 - action.player
    defender_shields = state.players[defender].shield_count
    attacker_power = _card_power(attacker, state)
    target_power = _card_power(target, state)
    breaks = _break_count(attacker)
    direct_win = action.action_type == ActionType.ATTACK_PLAYER and defender_shields == 0
    breaks_all = action.action_type == ActionType.ATTACK_PLAYER and defender_shields > 0 and breaks >= defender_shields
    trade_delta = 0.0
    if target is not None:
        trade_delta = max(-1.0, min(1.0, (attacker_power - target_power) / 20000.0))
    opp_blockers = sum(
        1
        for creature in state.players[defender].battle_zone
        if getattr(creature, "is_blocker", lambda: False)() and not creature.is_tapped
    )
    return [
        _norm(attacker_power, 20000),
        _norm(target_power, 20000),
        _norm(breaks, 5),
        _bool(direct_win),
        _bool(breaks_all),
        trade_delta,
        _norm(opp_blockers, 8),
        _bool(target is not None and attacker_power >= target_power),
        _bool(controller == defender if controller is not None else False),
    ]


def _block_action_features(action: Action, state) -> list[float]:
    if state is None or action.action_type not in {ActionType.DECLARE_BLOCKER, ActionType.DECLARE_GUARDMAN}:
        return [0.0] * 7
    blocker = _find_source_creature(state, action)
    ctx = state.attack_context
    attacker = None
    if ctx is not None:
        result = state.find_creature_anywhere(ctx.attacker_uid)
        attacker = result[1] if result is not None else None
    blocker_power = _card_power(blocker, state)
    attacker_power = _card_power(attacker, state)
    saves_direct_loss = bool(ctx and ctx.is_attacking_player and state.players[action.player].shield_count == 0)
    attacker_breaks = _break_count(attacker)
    trade_delta = max(-1.0, min(1.0, (blocker_power - attacker_power) / 20000.0)) if attacker else 0.0
    return [
        _norm(blocker_power, 20000),
        _norm(attacker_power, 20000),
        _bool(saves_direct_loss),
        _norm(attacker_breaks, 5),
        trade_delta,
        _bool(blocker is not None and attacker is not None and blocker_power >= attacker_power),
        _bool(action.action_type == ActionType.DECLARE_GUARDMAN),
    ]


def _choice_action_features(action: Action) -> list[float]:
    return [
        _bool(action.choice is True),
        _bool(action.choice is False),
        _norm(len(action.selected_uids), 10),
        _bool(action.selected_civ is not None),
        _bool(action.target_zone),
    ]


def _rule_action_features(action: Action, rule_service=None) -> list[float]:
    refs = _ACTION_RULE_REFS.get(action.action_type, ())
    categories = set()
    priorities: list[int] = []
    service = _safe_rule_service(rule_service)
    if service is not None and refs:
        for fact in service.get_rules(refs).values():
            categories.add(fact.rule_category)
            priorities.append(fact.priority)
    fallback_categories = {
        ActionType.CHARGE_MANA: {"turn_structure"},
        ActionType.PASS: {"turn_structure"},
        ActionType.ATTACK_PLAYER: {"turn_structure", "win_loss"},
        ActionType.ATTACK_CREATURE: {"turn_structure"},
        ActionType.DECLARE_BLOCKER: {"keyword", "turn_structure"},
        ActionType.USE_SHIELD_TRIGGER: {"trigger"},
        ActionType.USE_G_STRIKE: {"trigger"},
        ActionType.USE_NINJA_STRIKE: {"keyword"},
    }
    categories.update(fallback_categories.get(action.action_type, set()))
    if action.costs_mana():
        categories.add("cost_payment")
    if action.is_free_execution():
        categories.add("trigger")
    priority = min(priorities, default=100)
    return [
        _norm(len(refs), 4),
        _norm(priority, 100),
        *[1.0 if category in categories else 0.0 for category in _RULE_CATEGORIES],
    ]


def encode_action_v3(action: Action, state=None, db=None, *, rule_service=None) -> list[float]:
    """Encode a legal action with v2 features plus rule-aware tactical context."""
    features = encode_action_v2(action, state=state, db=db)
    features.extend(_charge_action_features(action, state, db))
    features.extend(_play_action_features(action, state, db))
    features.extend(_attack_action_features(action, state))
    features.extend(_block_action_features(action, state))
    features.extend(_choice_action_features(action))
    features.extend(_rule_action_features(action, rule_service))
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


def feature_schema_v3() -> dict[str, object]:
    return {
        "version": ACTION_ENCODER_VERSION_V3,
        "base_version": ACTION_ENCODER_VERSION,
        "action_types": [action_type.value for action_type in _ACTION_TYPES],
        "rule_categories": list(_RULE_CATEGORIES),
        "vector_size": ACTION_VECTOR_SIZE_V3,
    }


ACTION_VECTOR_SIZE_V2 = len(encode_action_v2(Action(player=0, action_type=ActionType.PASS)))
ACTION_VECTOR_SIZE_V3 = len(encode_action_v3(Action(player=0, action_type=ActionType.PASS)))
ACTION_VECTOR_SIZE = ACTION_VECTOR_SIZE_V3
