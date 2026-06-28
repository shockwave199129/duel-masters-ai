"""engine/sba/actions/d2_field.py — State-based action: Rule 822 — D2 field."""
from __future__ import annotations
from core.enums import CardType, CardSubtype, GlobalEffectType
from core.state import GameState
from core.zones import Creature
from core.zones import GraveyardCard


def _sba_d2_field(state: GameState) -> bool:
    """
    Rule 703.4l: When another D2 Field enters the Battle Zone, the D2 Field
    that was previously in the Battle Zone is placed in its owner's Graveyard.

    Only ONE D2 Field can exist per player. If a second one is played,
    the old one is immediately destroyed.
    """
    fired = False
    for player_idx in range(2):
        d2_fields = [
            c for c in state.players[player_idx].battle_zone
            if (c.definition.card_type == CardType.FIELD
                and c.definition.card_subtype == CardSubtype.D2)
        ]
        if len(d2_fields) > 1:
            # Keep the most recently entered (flagged by just_entered)
            # If no flag, keep the last in the list (most recent)
            newest = None
            for f in d2_fields:
                if f.temp_flags.get("just_entered", False):
                    newest = f
                    break
            if newest is None:
                newest = d2_fields[-1]

            for old_field in d2_fields:
                if old_field is not newest:
                    state.players[player_idx].battle_zone.remove(old_field)
                    state.players[player_idx].graveyard.insert(
                        0, GraveyardCard(definition=old_field.definition,
                                          died_from="sba_d2_field",
                                          died_on_turn=state.turn_number)
                    )
                    # Remove global effects from the old field
                    state.global_effects.remove_by_source(old_field.uid)
                    fired = True

            # Clear the just_entered flag
            if newest:
                newest.clear_flag("just_entered")

    return fired


