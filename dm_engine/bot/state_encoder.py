"""State encoders for neural bots."""

from __future__ import annotations

from collections.abc import Iterable
from math import prod

from core.enums import CardType, Civilization, Keyword, Phase
from core.observation import Observation
from core.state import GameState


OBSERVATION_VECTOR_SIZE = 14
OBSERVATION_ENCODER_VERSION = 2

_CIVILIZATIONS = [
    Civilization.FIRE,
    Civilization.WATER,
    Civilization.NATURE,
    Civilization.LIGHT,
    Civilization.DARKNESS,
]
_PHASES = list(Phase)
_COST_BUCKETS = [(0, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 999)]
_TYPE_BUCKETS = [
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
_ROLE_KEYWORDS = {
    "blocker": Keyword.BLOCKER,
    "speed_attacker": Keyword.SPEED_ATTACKER,
    "shield_trigger": Keyword.SHIELD_TRIGGER,
}
_TOP_CREATURE_SLOTS_PER_SIDE = 2


def _norm(value: float, maximum: float) -> float:
    if maximum <= 0:
        return 0.0
    return max(0.0, min(float(value), float(maximum))) / float(maximum)


def _bool(value: object) -> float:
    return 1.0 if value else 0.0


def _one_hot(value: object, values: list) -> list[float]:
    return [1.0 if value == item else 0.0 for item in values]


def _card_definition(card):
    return getattr(card, "definition", card)


def _card_cost(card) -> int:
    return int(getattr(_card_definition(card), "cost", 0) or 0)


def _card_civs(card) -> frozenset[Civilization]:
    return frozenset(getattr(_card_definition(card), "civilizations", frozenset()) or frozenset())


def _card_type(card):
    return getattr(_card_definition(card), "card_type", None)


def _card_keywords(card) -> frozenset[Keyword]:
    return frozenset(getattr(_card_definition(card), "keywords", frozenset()) or frozenset())


def _card_power(card, state: GameState | None = None) -> int:
    if hasattr(card, "compute_power"):
        try:
            return int(card.compute_power(state) or 0)
        except TypeError:
            return int(card.compute_power() or 0)
    return int(getattr(_card_definition(card), "power", 0) or 0)


def _zone_summary(cards: Iterable, *, state: GameState | None = None) -> list[float]:
    cards = list(cards)
    total = max(len(cards), 1)
    features: list[float] = [_norm(len(cards), 20)]

    for low, high in _COST_BUCKETS:
        count = sum(1 for card in cards if low <= _card_cost(card) <= high)
        features.append(float(count) / float(total))

    for civ in _CIVILIZATIONS:
        count = sum(1 for card in cards if civ in _card_civs(card))
        features.append(float(count) / float(total))

    for card_type in _TYPE_BUCKETS:
        count = sum(1 for card in cards if _card_type(card) == card_type)
        features.append(float(count) / float(total))

    other_types = sum(1 for card in cards if _card_type(card) not in _TYPE_BUCKETS)
    features.append(float(other_types) / float(total))

    for keyword in _KEYWORDS:
        count = sum(1 for card in cards if keyword in _card_keywords(card))
        features.append(float(count) / float(total))

    powers = [_card_power(card, state) for card in cards]
    features.append(_norm(sum(powers), 50000))
    features.append(_norm(max(powers, default=0), 20000))
    return features


def _mana_color_features(mana_zone) -> list[float]:
    features: list[float] = []
    for untapped_only in (False, True):
        pool = [m for m in mana_zone if not untapped_only or not m.is_tapped]
        for civ in _CIVILIZATIONS:
            features.append(_norm(sum(1 for mana in pool if civ in mana.civilizations), 10))
    return features


def _creature_slot(creature, state: GameState) -> list[float]:
    keywords = set(getattr(creature, "keywords", []))
    definition_keywords = _card_keywords(creature)
    return [
        _norm(_card_power(creature, state), 20000),
        _bool(getattr(creature, "is_tapped", False)),
        _bool(getattr(creature, "has_summoning_sickness", False)),
        *_one_hot(None, []),  # keeps the structure explicit; no-op.
        *[1.0 if civ in _card_civs(creature) else 0.0 for civ in _CIVILIZATIONS],
        1.0 if (Keyword.BLOCKER in definition_keywords or "blocker" in keywords) else 0.0,
        1.0 if (Keyword.SPEED_ATTACKER in definition_keywords or "speed_attacker" in keywords) else 0.0,
        1.0 if Keyword.DOUBLE_BREAKER in definition_keywords else 0.0,
        1.0 if Keyword.TRIPLE_BREAKER in definition_keywords else 0.0,
        1.0 if Keyword.WORLD_BREAKER in definition_keywords else 0.0,
    ]


_CREATURE_SLOT_SIZE = len(_creature_slot(type("_Empty", (), {
    "definition": type("_Def", (), {
        "power": 0,
        "civilizations": frozenset(),
        "keywords": frozenset(),
    })(),
    "is_tapped": False,
    "has_summoning_sickness": False,
})(), None))  # type: ignore[arg-type]


def _top_creature_features(creatures, state: GameState) -> list[float]:
    ordered = sorted(creatures, key=lambda c: _card_power(c, state), reverse=True)
    features: list[float] = []
    for creature in ordered[:_TOP_CREATURE_SLOTS_PER_SIDE]:
        features.extend(_creature_slot(creature, state))
    missing = _TOP_CREATURE_SLOTS_PER_SIDE - len(ordered[:_TOP_CREATURE_SLOTS_PER_SIDE])
    features.extend([0.0] * missing * _CREATURE_SLOT_SIZE)
    return features


def _has_keyword_text(card, keyword: Keyword) -> bool:
    return keyword in _card_keywords(card)


def _draw_probability(remaining_count: int, deck_size: int, draws: int) -> float:
    if remaining_count <= 0 or deck_size <= 0 or draws <= 0:
        return 0.0
    misses = prod((deck_size - remaining_count - i) / (deck_size - i) for i in range(min(draws, deck_size)))
    return max(0.0, min(1.0, 1.0 - misses))


def _deck_memory_features(state: GameState, perspective: int) -> list[float]:
    player = state.players[perspective]
    remaining = player.cards_remaining_in_deck_by_id(include_hidden_shields=False)
    deck_size = max(player.deck_size, 0)
    definitions = {
        card.id: _card_definition(card)
        for zone in (player.hand, player.mana_zone, player.battle_zone, player.graveyard)
        for card in zone
    }
    for card in player.deck_composition:
        if card not in definitions:
            found = next((d for d in player.deck if d.id == card), None)
            if found is not None:
                definitions[card] = found

    role_counts = {
        "low_cost": 0,
        "shield_trigger": 0,
        "blocker": 0,
        "speed_attacker": 0,
        "finisher": 0,
    }
    for card_id, count in remaining.items():
        definition = definitions.get(card_id)
        if definition is None:
            continue
        if definition.cost <= 3:
            role_counts["low_cost"] += count
        if _has_keyword_text(definition, Keyword.SHIELD_TRIGGER):
            role_counts["shield_trigger"] += count
        if _has_keyword_text(definition, Keyword.BLOCKER):
            role_counts["blocker"] += count
        if _has_keyword_text(definition, Keyword.SPEED_ATTACKER):
            role_counts["speed_attacker"] += count
        if definition.cost >= 7 or (definition.power or 0) >= 9000:
            role_counts["finisher"] += count

    features: list[float] = []
    for count in role_counts.values():
        features.append(_norm(count, 16))
        for draws in (1, 2, 3):
            features.append(_draw_probability(count, deck_size, draws))
    return features


def _playable_hand_features(state: GameState, perspective: int) -> list[float]:
    if state.active_player != perspective or state.current_phase != Phase.MAIN:
        return [0.0] * (1 + len(_COST_BUCKETS) + len(_CIVILIZATIONS))
    from engine.action_generator import get_legal_actions

    actions = [a for a in get_legal_actions(state) if a.player == perspective and a.costs_mana()]
    card_ids = {a.card_id for a in actions if a.card_id is not None}
    cards = [card for card in state.players[perspective].hand if card.id in card_ids]
    features = [_norm(len(card_ids), 10)]
    total = max(len(cards), 1)
    for low, high in _COST_BUCKETS:
        features.append(sum(1 for card in cards if low <= card.cost <= high) / total)
    for civ in _CIVILIZATIONS:
        features.append(sum(1 for card in cards if civ in card.civilizations) / total)
    return features


def feature_schema_v2() -> dict[str, object]:
    return {
        "version": OBSERVATION_ENCODER_VERSION,
        "civilizations": [c.value for c in _CIVILIZATIONS],
        "cost_buckets": [f"{low}-{high if high < 999 else '+'}" for low, high in _COST_BUCKETS],
        "type_buckets": [t.value for t in _TYPE_BUCKETS] + ["Other"],
        "keywords": [k.value for k in _KEYWORDS],
        "top_creature_slots_per_side": _TOP_CREATURE_SLOTS_PER_SIDE,
        "vector_size": OBSERVATION_VECTOR_SIZE_V2,
    }


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


def encode_observation_v2(state: GameState, perspective: int) -> list[float]:
    """
    Encode public/allowed information for v2 neural training.

    Opponent hand contents, hidden shields, exact deck order, and opponent deck
    composition are intentionally excluded.
    """
    obs = Observation.build(state, perspective)
    me = obs.self_state
    opp = obs.opponent_state
    p_me = state.players[perspective]
    p_opp = state.players[1 - perspective]

    features: list[float] = [
        _norm(obs.turn_number, 30),
        _bool(obs.active_player == perspective),
        _bool(obs.is_my_turn),
        *_one_hot(obs.current_phase, _PHASES),
        _norm(me.shield_count, 10),
        _norm(me.hand_count, 15),
        _norm(me.deck_size, 40),
        _norm(me.mana_count, 20),
        _norm(me.available_mana, 20),
        _norm(len(me.battle_zone), 12),
        _norm(opp.shield_count, 10),
        _norm(opp.hand_count, 15),
        _norm(opp.deck_size, 40),
        _norm(opp.mana_count, 20),
        _norm(opp.available_mana, 20),
        _norm(len(opp.battle_zone), 12),
        _bool(state.current_phase == Phase.MANA_CHARGE),
        _bool(state.current_phase == Phase.MAIN),
        _bool(p_me.has_charged_mana_this_turn),
        _bool(state.current_phase == Phase.MANA_CHARGE and not p_me.has_charged_mana_this_turn),
    ]
    features.extend(_mana_color_features(p_me.mana_zone))
    features.extend(_mana_color_features(p_opp.mana_zone))

    zone_groups = [
        p_me.hand,
        p_me.mana_zone,
        p_me.battle_zone,
        p_me.graveyard,
        p_me.hyperspatial_zone,
        p_opp.mana_zone,
        p_opp.battle_zone,
        p_opp.graveyard,
        p_opp.hyperspatial_zone,
    ]
    for zone in zone_groups:
        features.extend(_zone_summary(zone, state=state))

    features.extend(_top_creature_features(p_me.battle_zone, state))
    features.extend(_top_creature_features(p_opp.battle_zone, state))
    features.extend(_deck_memory_features(state, perspective))
    features.extend(_playable_hand_features(state, perspective))
    return features


OBSERVATION_VECTOR_SIZE_V2 = len(encode_observation_v2.__annotations__)  # placeholder overwritten below


def _compute_vector_size_v2() -> int:
    return (
        1 + 2 + len(_PHASES) + 12 + 4
        + 20
        + 9 * len(_zone_summary([]))
        + 2 * _TOP_CREATURE_SLOTS_PER_SIDE * _CREATURE_SLOT_SIZE
        + 5 * 4
        + (1 + len(_COST_BUCKETS) + len(_CIVILIZATIONS))
    )


OBSERVATION_VECTOR_SIZE_V2 = _compute_vector_size_v2()
