"""engine/sba/actions/god_link.py — State-based action: Rule 804.2b invalid G-Link detach."""
from __future__ import annotations

from core.state import GameState
from engine.god_manager import GodManager


def _sba_god_link_invalid_detach(state: GameState) -> bool:
    """
    Rule 804.2b: If a God link configuration becomes invalid, detach all members
    into separate Gods in the battle zone.
    """
    fired = False
    for player_idx in range(2):
        for creature in list(state.players[player_idx].battle_zone):
            if not GodManager.is_god_link(creature):
                continue
            members = GodManager.get_god_members(creature)
            if GodManager.is_valid_god_configuration(members):
                continue
            GodManager.detach_god_link(state, player_idx, creature)
            fired = True
    return fired
