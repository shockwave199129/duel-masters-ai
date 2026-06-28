"""engine/sba/actions/smax.py — State-based action: Rule 815 — S-MAX uniqueness."""
from __future__ import annotations
from core.enums import CardType, CardSubtype, GlobalEffectType
from core.state import GameState
from core.zones import Creature
from core.zones import HandCard


def _sba_smax_uniqueness(state: GameState) -> bool:
    """
    Rule 815.1a: Only 1 S-MAX Evolution Creature can exist in the Battle Zone
    per player. If there are 2 or more S-MAX creatures, keep 1 and return the
    others to the owner's hand.
    
    This is a static ability (rule 815.1a), but we check it here as an SBA
    to ensure it runs after each state change.
    """
    fired = False
    
    for player_idx in range(2):
        smax_creatures = [
            c for c in state.players[player_idx].battle_zone
            if c.definition.card_subtype == CardSubtype.STAR_MAX
        ]
        
        if len(smax_creatures) > 1:
            # Keep the most recently entered; return others to hand
            # (In practice, the first one entered should be kept, but we use a simple heuristic)
            for idx, creature in enumerate(smax_creatures[1:], start=1):
                # Return this creature to the owner's hand
                state.players[player_idx].battle_zone.remove(creature)
                state.global_effects.remove_by_source(creature.uid)
                
                hand_card = HandCard(
                    definition=creature.definition,
                    uid=creature.uid
                )
                state.players[player_idx].hand.append(hand_card)
                fired = True
    
    return fired


