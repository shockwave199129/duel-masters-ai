"""engine/special_cards/forbidden_explosion.py — Final Forbidden Field explosion (701.29, 309)."""
from __future__ import annotations

from typing import Optional

from core.cards import CardDefinition, get_other_face
from core.enums import CardSubtype, CardType, TriggerEvent
from core.state import GameState
from core.zones import Creature
from engine.trigger_registry import fire_trigger


FINAL_FORBIDDEN_FIELD_COUNT = 5


def is_final_forbidden_field(defn: CardDefinition) -> bool:
    """Final Forbidden Field in the battle zone (308.2, 204.3c)."""
    return (
        defn.card_type == CardType.FIELD
        and defn.card_subtype == CardSubtype.FINAL_FORBIDDEN
    )


def find_final_forbidden_fields(
    state: GameState,
    player: int,
    *,
    field_uids: list[str] | None = None,
) -> list[Creature]:
    """Locate Final Forbidden Field creatures in the controller's battle zone."""
    p_state = state.players[player]
    if field_uids:
        found: list[Creature] = []
        for uid in field_uids:
            creature = p_state.find_creature(uid)
            if creature is None or not is_final_forbidden_field(creature.definition):
                return []
            found.append(creature)
        return found

    return [
        c for c in p_state.battle_zone
        if is_final_forbidden_field(c.definition)
    ]


def _resolve_assembled_creature_face(
    fields: list[Creature],
    explicit: CardDefinition | None,
    db=None,
) -> CardDefinition | None:
    if isinstance(explicit, CardDefinition):
        return explicit
    for field in fields:
        other = get_other_face(field.definition, db)
        if other is not None and other.card_type == CardType.CREATURE:
            return other
    return None


def _core_from_components(components: list[Creature], db=None) -> CardDefinition | None:
    for comp in components:
        other = get_other_face(comp.definition, db)
        if other is not None and other.card_type == CardType.CORE:
            return other
        if comp.definition.card_type == CardType.CORE:
            return comp.definition
    return None


def perform_forbidden_explosion(
    state: GameState,
    player: int,
    *,
    field_uids: list[str] | None = None,
    assembled_creature_def: CardDefinition | None = None,
    forbidden_core_def: CardDefinition | None = None,
    db=None,
) -> Optional[Creature]:
    """
    Rule 701.29a: flip 5 Final Forbidden Fields and reassemble into
    1 Final Forbidden Creature (Forbidden Core stacks underneath, 309.1/309.6).
    """
    fields = find_final_forbidden_fields(state, player, field_uids=field_uids)
    if len(fields) != FINAL_FORBIDDEN_FIELD_COUNT:
        return None

    creature_face = _resolve_assembled_creature_face(fields, assembled_creature_def, db)
    if creature_face is None:
        return None

    p_state = state.players[player]
    primary = fields[0]
    components: list[Creature] = []

    for field in fields:
        field.face = 1
        field.temp_flags["_forbidden_flipped"] = True
        components.append(field)

    for comp in components:
        if comp is primary:
            continue
        if comp in p_state.battle_zone:
            p_state.battle_zone.remove(comp)
            state.global_effects.remove_by_source(comp.uid)

    primary.remove_static_effects(state)
    primary.definition = creature_face
    primary.linked_cells = list(components)
    primary.temp_flags["_forbidden_explosion"] = True
    primary.has_summoning_sickness = False
    primary.is_tapped = any(c.is_tapped for c in components)

    core_def = forbidden_core_def or _core_from_components(components, db)
    if core_def is not None:
        primary.attached_cards.append(core_def)

    primary.apply_static_effects(state)

    fire_trigger(state, TriggerEvent.ON_ENTER_BATTLE_ZONE, {
        "source_uid": primary.uid,
        "source_card_id": primary.id,
        "controller": player,
        "zone": "battle_zone",
        "forbidden_explosion": True,
    }, primary.uid)

    return primary
