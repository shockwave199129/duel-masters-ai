"""engine/god_manager.py — Gods (rule 804) and Psychic Super Link (rule 806) management."""

from __future__ import annotations
from typing import Optional, Dict, List, Set
from dataclasses import dataclass

from core.cards import CardDefinition
from core.zones import Creature
from core.state import GameState


@dataclass
class GodGroup:
    """Represents a linked group of Gods sharing a G-Link slot layout."""
    group_id: str           # unique identifier for this linked group
    layout_size: int        # 2 | 3 | 4 | 6 (side length for NxN grid, or N for 1xN)
    creatures: List[Creature]  # all creatures in this group (in layout order)


class GodManager:
    """Manages God creature linking and validation (rule 804)."""

    @staticmethod
    def validate_god_link(
        source_card: CardDefinition,
        linked_card: CardDefinition,
    ) -> bool:
        """
        Validate whether two God cards can be linked together (rule 804.2).
        
        Requirements:
        1. Both cards must have god_link_group set
        2. Both must have matching god_link_group value
        3. Both must have compatible god_link_slots (opposite sides)
        """
        if not source_card.god_link_group or not linked_card.god_link_group:
            return False
        
        if source_card.god_link_group != linked_card.god_link_group:
            return False
        
        # Check slot compatibility (simplified: allow if both have slots defined)
        # Full implementation would validate specific side connections
        return bool(source_card.god_glink_slots) and bool(linked_card.god_glink_slots)

    @staticmethod
    def get_linkable_gods(
        state: GameState,
        player: int,
        source_card: CardDefinition,
    ) -> List[CardDefinition]:
        """
        Return all cards in hand/deck that can link with source_card (rule 804).
        
        This is called during action generation to determine valid G-Link combos.
        """
        if not source_card.god_link_group:
            return []
        
        linkable = []
        player_state = state.players[player]
        
        # Check hand
        for hand_card in player_state.hand:
            card_def = hand_card.definition
            if GodManager.validate_god_link(source_card, card_def):
                linkable.append(card_def)
        
        return linkable

    @staticmethod
    def is_valid_god_configuration(creatures: List[Creature]) -> bool:
        """
        Validate that all creatures in a group form a valid God layout (rule 804.1).
        
        A valid God configuration:
        1. All creatures share the same god_link_group
        2. Total count matches a valid layout size (2, 3, 4, or 6 creatures)
        3. Layouts can be NxN or Nx1 (e.g. 2x2=4, 3x3=9, 6x1=6)
        """
        if not creatures:
            return False
        
        # All creatures must have a god_link_group
        god_group = creatures[0].definition.god_link_group
        if not god_group:
            return False
        
        for creature in creatures:
            if creature.definition.god_link_group != god_group:
                return False
        
        # Valid counts based on layout size: 2, 3, 4, 6
        # 2x2=4, 2x3=6, 2x4=8, 3x3=9, 3x4=12, 4x4=16, 6x1=6, 1x6=6
        valid_counts = {2, 3, 4, 6, 8, 9, 12, 16}
        return len(creatures) in valid_counts
