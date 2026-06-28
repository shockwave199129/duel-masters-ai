"""engine/sba/actions/standalone_weapon.py — State-based action: Rule 305.3 — standalone weapon."""
from __future__ import annotations
from core.enums import CardType, CardSubtype, GlobalEffectType
from core.state import GameState
from core.zones import Creature
from core.zones import GraveyardCard


def _sba_standalone_weapon(state: GameState) -> bool:
    """
    Rule 703.4m: A standalone Weapon in the Battle Zone is placed in the Graveyard.
    Weapons (Dragheart Weapons) must be equipped to a creature.
    """
    fired = False
    for player_idx in range(2):
        to_remove = [
            c for c in state.players[player_idx].battle_zone
            if c.definition.card_type == CardType.WEAPON
        ]
        for weapon in to_remove:
            state.players[player_idx].battle_zone.remove(weapon)
            state.players[player_idx].graveyard.insert(
                0, GraveyardCard(definition=weapon.definition,
                                  died_from="sba_standalone_weapon",
                                  died_on_turn=state.turn_number)
            )
            fired = True

    return fired


# ─────────────────────────────────────────────────────────────────────────────
# Turn limit helper (deprecated; max_steps handles training cutoffs)
# ─────────────────────────────────────────────────────────────────────────────

def check_turn_limit(state: GameState) -> GameState:
    """
    No-op compatibility helper.

    Duel Masters games should end by win/loss conditions. Training runners use
    max_steps to stop long simulations and mark them unfinished instead of
    turning them into game draws.
    """
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Helper: destroy a creature (move to graveyard, remove global effects)
# ─────────────────────────────────────────────────────────────────────────────

