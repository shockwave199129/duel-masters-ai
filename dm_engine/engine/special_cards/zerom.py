"""engine/special_cards/zerom.py — Zerom card mechanics."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature, EvolutionStackEntry, HandCard
from core.cards import CardDefinition


def move_zerom_to_battle(
    state: GameState,
    player: int,
    card_def: "CardDefinition",
) -> Creature:
    """
    Move a Zerom from its current zone to the battle zone as a flipped creature.
    (Rule 812)

    Creates a Creature from the card definition, sets the _zerom_flipped flag,
    adds it to the controller's battle zone, and applies static effects.
    """
    p_state = state.players[player]

    creature = Creature(
        definition=card_def,
        controller=player,
        owner=player,
        entered_turn=state.turn_number,
        has_summoning_sickness=True,
    )
    creature.temp_flags["_zerom_flipped"] = True
    p_state.battle_zone.append(creature)
    creature.apply_static_effects(state)
    # Fire ON_ENTER_BATTLE_ZONE trigger for the flipped creature
    from core.enums import TriggerEvent
    from engine.trigger_registry import fire_trigger
    fire_trigger(state, TriggerEvent.ON_ENTER_BATTLE_ZONE, {
        "source_uid": creature.uid,
        "source_card_id": creature.id,
        "controller": player,
        "zone": "battle_zone",
    }, creature.uid)
    return creature



