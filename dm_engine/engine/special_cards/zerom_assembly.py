"""engine/special_cards/zerom_assembly.py — Zerom Birth assembly (701.31, 812)."""
from __future__ import annotations

from typing import Optional

from core.cards import CardDefinition, get_other_face, is_zerom
from core.enums import CardType, TriggerEvent
from core.state import GameState
from core.zones import Creature
from engine.trigger_registry import fire_trigger


ZEROM_RITUAL_COUNT = 1
ZEROM_NEBULA_COUNT = 4
ZEROM_COMPONENT_COUNT = ZEROM_RITUAL_COUNT + ZEROM_NEBULA_COUNT


def is_zerom_nebula(defn: CardDefinition) -> bool:
    """Nebula of Zerom (812)."""
    return defn.card_type == CardType.NEBULA


def is_zerom_ritual(defn: CardDefinition) -> bool:
    """Ritual of Zerom (812)."""
    return is_zerom(defn) and defn.card_type in (CardType.RITUAL, CardType.CREATURE)


def find_zerom_components(
    state: GameState,
    player: int,
    *,
    component_uids: list[str] | None = None,
) -> tuple[Creature | None, list[Creature]]:
    """
    Locate 1 Ritual of Zerom + 4 Nebulas of Zerom in the battle zone.

    Returns (ritual, nebulas) or (None, []) when incomplete/invalid.
    """
    p_state = state.players[player]

    if component_uids:
        ritual: Creature | None = None
        nebulas: list[Creature] = []
        for uid in component_uids:
            creature = p_state.find_creature(uid)
            if creature is None:
                return None, []
            if is_zerom_ritual(creature.definition):
                if ritual is not None:
                    return None, []
                ritual = creature
            elif is_zerom_nebula(creature.definition):
                nebulas.append(creature)
            else:
                return None, []
        if ritual is None or len(nebulas) != ZEROM_NEBULA_COUNT:
            return None, []
        return ritual, nebulas

    ritual_candidates = [
        c for c in p_state.battle_zone if is_zerom_ritual(c.definition)
    ]
    nebula_candidates = [
        c for c in p_state.battle_zone if is_zerom_nebula(c.definition)
    ]
    if len(ritual_candidates) != ZEROM_RITUAL_COUNT:
        return None, []
    if len(nebula_candidates) != ZEROM_NEBULA_COUNT:
        return None, []
    return ritual_candidates[0], list(nebula_candidates)


def _resolve_zerom_creature_face(
    ritual: Creature,
    nebulas: list[Creature],
    explicit: CardDefinition | None,
    db=None,
) -> CardDefinition | None:
    if isinstance(explicit, CardDefinition):
        return explicit
    for comp in [ritual, *nebulas]:
        other = get_other_face(comp.definition, db)
        if other is not None and other.card_type == CardType.CREATURE:
            return other
    return ritual.definition if is_zerom(ritual.definition) else None


def perform_zerom_birth(
    state: GameState,
    player: int,
    *,
    component_uids: list[str] | None = None,
    assembled_creature_def: CardDefinition | None = None,
    db=None,
) -> Optional[Creature]:
    """
    Rule 701.31a / 812.1a: flip Ritual + 4 Nebulas and reassemble into 1 Zerom Creature.
    """
    ritual, nebulas = find_zerom_components(
        state, player, component_uids=component_uids,
    )
    if ritual is None:
        return None

    creature_face = _resolve_zerom_creature_face(
        ritual, nebulas, assembled_creature_def, db,
    )
    if creature_face is None:
        return None

    p_state = state.players[player]
    components = [ritual, *nebulas]

    for comp in components:
        comp.face = 1
        comp.temp_flags["_zerom_flipped"] = True

    for comp in nebulas:
        if comp in p_state.battle_zone:
            p_state.battle_zone.remove(comp)
            state.global_effects.remove_by_source(comp.uid)

    ritual.remove_static_effects(state)
    ritual.definition = creature_face
    ritual.linked_cells = list(components)
    ritual.temp_flags["_zerom_birth"] = True
    ritual.has_summoning_sickness = False
    ritual.is_tapped = any(c.is_tapped for c in components)

    ritual.apply_static_effects(state)

    fire_trigger(state, TriggerEvent.ON_ZEROM_FLIP, {
        "source_uid": ritual.uid,
        "source_card_id": ritual.id,
        "controller": player,
        "zerom_birth": True,
    }, ritual.uid)

    return ritual
