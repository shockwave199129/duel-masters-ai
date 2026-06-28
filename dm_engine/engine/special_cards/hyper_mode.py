"""engine/special_cards/hyper_mode.py — Hyper Mode card mechanics."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature, EvolutionStackEntry, HandCard
from core.cards import CardDefinition
from core.cards import is_hyper_mode


def swap_hyper_mode(creature: Creature) -> Creature:
    """
    Swap a Hyper Mode creature to its released face (rule 816).

    If the creature has an other_face_id and is currently in the
    released state (hyper_mode_released=True), swap its definition
    to the other face. This changes the creature's abilities and
    potentially its power.

    Returns the modified creature (same object, mutated).
    """
    if not is_hyper_mode(creature.definition):
        return creature
    if creature.definition.other_face_id is None:
        return creature
    if creature.hyper_mode_released:
        # Already released — no swap needed
        return creature

    old_def = creature.definition
    from core.cards import CardDefinition as _CD
    new_def = _CD(
        id=old_def.other_face_id,
        slug=old_def.slug,
        name=old_def.name,
        cost=old_def.cost,
        power=old_def.power,
        card_type=old_def.card_type,
        card_subtype=old_def.card_subtype,
        civilizations=old_def.civilizations,
        races=old_def.races,
        keywords=old_def.keywords,
        effects=old_def.effects,
        evolution_source_races=old_def.evolution_source_races,
        evolution_source_types=old_def.evolution_source_types,
        is_multiface=old_def.is_multiface,
        other_face_id=old_def.id,  # point back to the original
    )
    creature.definition = new_def
    creature.hyper_mode_released = True
    return creature


def move_ultra_gr_to_battle(state: GameState, controller: int, creature_uid: str) -> "Creature":
    """
    Move an Ultra GR creature face-up into the battle zone (rule 408).
    Stub — full Ultra GR logic is in development.
    """
    p_state = state.players[controller]
    creature = p_state.find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Ultra GR creature {creature_uid} not found")
    creature.face = 1
    return creature


def _remove_from_hand(state: GameState, player: int, card_uid: str) -> HandCard:
    p_state = state.players[player]
    hand_card = p_state.find_in_hand(card_uid)
    if hand_card is None:
        raise ValueError(f"Hand card {card_uid} not found for player {player}")
    p_state.hand.remove(hand_card)
    return hand_card


