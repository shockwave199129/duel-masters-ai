"""engine/sba/actions/evolution.py — State-based action: Rule 801.4 — evolution reconstruction."""
from __future__ import annotations
from core.enums import CardType, CardSubtype, GlobalEffectType
from core.state import GameState
from core.zones import Creature
from core.zones import GraveyardCard


def _sba_evolution_reconstruction(state: GameState) -> bool:
    """
    Rule 703.4h: When only the top card of an Evolution Creature leaves the
    Battle Zone, the underlying cards remain and reconstruct.
    
    Rule 801.4a-d: Reconstruction loop:
      1. If the next card in the stack can exist standalone, it becomes the new top
      2. If not, it goes to graveyard and we check the next one
      3. Repeat until a valid card is the new top or stack is exhausted
      4. Reconstructed creature does not re-enter; inherits effects; keeps orientation
    
    This SBA is triggered when a creature has the "_pending_reconstruction" flag
    set by remove_top_evolution_card_and_reconstruct in zone_mover.py.
    """
    # Invalid card types that cannot exist standalone in the Battle Zone
    INVALID_STANDALONE = {
        CardType.SPELL,
        CardType.CASTLE,
        CardType.CORE,
        CardType.CELL,
        CardType.WEAPON,
    }

    fired = False
    for player_idx in range(2):
        creatures_needing_reconstruction = [
            c for c in state.players[player_idx].battle_zone
            if c.temp_flags.get("_pending_reconstruction", False)
        ]

        for creature in creatures_needing_reconstruction:
            creature.clear_flag("_pending_reconstruction")

            # Rule 801.4b: Loop through underlying cards
            while creature.is_evolution_creature():
                under_entry = creature.peek_under_card()
                if under_entry is None:
                    # No more cards underneath — remove entire creature
                    state.players[player_idx].battle_zone.remove(creature)
                    state.global_effects.remove_by_source(creature.uid)
                    fired = True
                    break

                # Rule 801.4a: Check if this card can exist standalone
                if under_entry.definition.card_type in INVALID_STANDALONE:
                    # Cannot exist standalone → move to graveyard and continue loop
                    popped = creature.pop_from_evolution_stack()
                    if popped:
                        state.players[player_idx].graveyard.insert(
                            0,
                            GraveyardCard(
                                definition=popped.definition,
                                uid=popped.uid,
                                died_from="sba_evolution_reconstruct_invalid",
                                died_on_turn=state.turn_number,
                            )
                        )
                    fired = True
                    continue  # Check next card in stack

                # Rule 801.4c-d: Valid card — promote it to be the new top
                # The creature stays the same (same uid), just updates the definition
                popped = creature.pop_from_evolution_stack()
                if popped:
                    # Rule 801.4c: reconstructed card inherits effects and does not re-enter
                    # Rule 801.4d: orientation is inherited from the creature's current state
                    creature.definition = popped.definition
                    # Note: We keep creature.uid unchanged (same creature, rule 801.2)
                    # Note: We keep creature.is_tapped as is (rule 801.4d)
                    # Note: We keep creature.power_modifiers as is (rule 801.4c)
                    fired = True

                # After reconstruction, stop and let SBA re-check (rule 703.3)
                break

    return fired


