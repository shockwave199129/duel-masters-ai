"""engine/special_cards/gr_creature.py — GR Creature card mechanics."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature, _new_uid


def move_ultra_gr_to_battle(
    state: GameState,
    player: int,
    card_def: CardDefinition,
) -> Creature:
    """
    Summon a GR creature from the Ultra GR zone into the battle zone.

    Steps:
      1. Remove the CardDefinition from state.players[player].ultra_gr_zone
      2. Create a new Creature from the definition
      3. Add to battle_zone with has_summoning_sickness = True
      4. Apply static effects

    GR creatures have summoning sickness unless they have Speed Attacker.
    """
    p_state = state.players[player]

    # Remove from Ultra GR zone
    p_state.ultra_gr_zone = [c for c in p_state.ultra_gr_zone if c.id != card_def.id]

    # Create the creature
    creature = Creature(
        definition=card_def,
        uid=_new_uid(),
        controller=player,
        owner=player,
        entered_turn=state.turn_number,
        has_summoning_sickness=True,
    )
    p_state.battle_zone.append(creature)
    creature.apply_static_effects(state)
    return creature


