"""engine/sba/actions/cannot_attack.py — State-based action: Rule 703.4e.

A creature with the 'cannot attack' keyword must be tapped.
If such a creature is currently untapped, tap it immediately.
"""
from __future__ import annotations
from core.enums import CardType, Keyword
from core.state import GameState


def _sba_cannot_attack_tap(state: GameState) -> bool:
    """
    Rule 703.4e: As soon as a creature has 'cannot attack', it becomes tapped.

    This enforces the rule globally — if a static effect grants CANNOT_ATTACK
    mid-turn (after the creature has already attacked), the creature is tapped
    on the next SBA check.
    """
    fired = False
    for player_idx in range(2):
        for creature in state.players[player_idx].battle_zone:
            if creature.is_ignored:
                continue
            if creature.has_keyword(Keyword.CANNOT_ATTACK) and not creature.is_tapped:
                creature.is_tapped = True
                fired = True
    return fired
