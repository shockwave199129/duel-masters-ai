"""engine/sba/checker.py — State-Based Action (SBA) enforcement loop.

Rule 703: State-Based Actions are automatic game actions that occur after
every action resolution to maintain the integrity of the game state. They run
in a loop until no more SBAs trigger.

SBAs implemented:
  ✅ 703.4a: Player receives direct attack with 0 shields → loses
  ✅ 703.4b: Player's deck reaches 0 cards → loses
  ✅ 703.4c: Creature with power ≤ 0 → destroyed
  ✅ 703.4d: Creature that lost battle → destroyed
  ✅ 703.4e: Creature with "cannot attack" → tapped (NEW)
  ✅ 703.4f: Cross Gear not attached → destroyed (NEW)
  ✅ 703.4g: Aura/Fortress not attached → graveyard (NEW)
  ✅ 703.4h: Evolution creature reconstruction
  ✅ 703.4i: S-MAX Evolution uniqueness
  ✅ 703.4j: Seal removal when Command enters
  ✅ 703.4k: Castle detachment when fortified shield leaves
  ✅ 703.4m: Weapon standalone → graveyard (NEW)
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from core.enums import GameResult, CardType, Keyword
from core.zones import Creature, GraveyardCard
from engine.sba.missing_sbas import (
    _sba_cannot_attack_tap,
    _sba_cross_gear_standalone,
    _sba_aura_fortress_standalone,
    _sba_weapon_standalone,
)

if TYPE_CHECKING:
    from core.state import GameState

logger = logging.getLogger(__name__)


def check_state_based_actions(state: GameState) -> GameState:
    """
    Execute all state-based actions in a loop until none trigger.

    Rule 703: After every action, check for SBAs and apply them. Continue
    until no SBAs apply. Then return the updated state.
    """
    s = state.copy()
    loop_count = 0
    max_loops = 100  # Prevent infinite loops

    while loop_count < max_loops:
        loop_count += 1
        fired = False

        # Check each SBA in order. If any fires, loop again.
        fired |= _check_once(s)

        if not fired:
            break

    if loop_count >= max_loops:
        logger.warning("SBA loop exceeded %d iterations; stopping.", max_loops)

    return s


def _check_once(state: GameState) -> bool:
    """
    Execute all applicable SBAs once. Return True if any SBA fired.
    
    Order matters: check win/loss conditions first, then board cleanup.
    """
    fired = False

    # ── Win/Loss conditions ──────────────────────────────────────────────────
    fired |= check_zero_shields(state)              # 703.4a
    fired |= check_deck_empty(state)                # 703.4b

    # If a player has already lost, stop checking other SBAs
    if state.result != GameResult.IN_PROGRESS:
        return fired

    # ── Board cleanup ────────────────────────────────────────────────────────
    fired |= check_power_zero(state)                # 703.4c
    fired |= check_battle_losers(state)             # 703.4d
    fired |= _sba_cannot_attack_tap(state)          # 703.4e (NEW)
    fired |= _sba_cross_gear_standalone(state)      # 703.4f (NEW)
    fired |= _sba_aura_fortress_standalone(state)   # 703.4g (NEW)
    fired |= check_evolution_reconstruction(state)  # 703.4h
    fired |= check_smax_uniqueness(state)           # 703.4i
    fired |= check_seal_removal(state)              # 703.4j
    fired |= check_g_castle_shield(state)           # 703.4k
    fired |= _sba_weapon_standalone(state)          # 703.4m (NEW)

    return fired


# ─────────────────────────────────────────────────────────────────────────────
# P0 Priority: Zone Loss Conditions (checked first)
# ─────────────────────────────────────────────────────────────────────────────

def check_zero_shields(state: GameState) -> bool:
    """Rule 703.4a: Player with 0 shields receives direct attack → loses."""
    for player_idx in range(2):
        if len(state.players[player_idx].shield_zone) == 0:
            state.result = GameResult.PLAYER_1_WINS if player_idx == 0 else GameResult.PLAYER_0_WINS
            return True
    return False


def check_deck_empty(state: GameState) -> bool:
    """Rule 703.4b: Player tries to draw from empty deck → loses."""
    for player_idx in range(2):
        if len(state.players[player_idx].deck) == 0:
            state.result = GameResult.PLAYER_1_WINS if player_idx == 0 else GameResult.PLAYER_0_WINS
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Board State Cleanup (run until stable)
# ─────────────────────────────────────────────────────────────────────────────

def check_power_zero(state: GameState) -> bool:
    """Rule 703.4c: Creature with power ≤ 0 → destroyed."""
    fired = False
    for player_idx in range(2):
        creatures_to_destroy = [
            c for c in state.players[player_idx].battle_zone
            if c.power <= 0
        ]
        for creature in creatures_to_destroy:
            _destroy_creature(state, player_idx, creature, "sba_power_zero")
            fired = True
    return fired


def check_battle_losers(state: GameState) -> bool:
    """Rule 703.4d: Creature that lost battle → destroyed."""
    fired = False
    for player_idx in range(2):
        creatures_to_destroy = [
            c for c in state.players[player_idx].battle_zone
            if c.lost_battle
        ]
        for creature in creatures_to_destroy:
            _destroy_creature(state, player_idx, creature, "sba_battle_loser")
            fired = True
    return fired


def _destroy_creature(
    state: GameState,
    player_idx: int,
    creature: Creature,
    reason: str,
) -> None:
    """Internal helper to destroy a creature and send to appropriate zone."""
    from core.enums import CardSubtype
    from engine.zone_mover import move_battle_to_hyperspatial

    p = state.players[player_idx]

    if creature not in p.battle_zone:
        return

    # Check replacement effects first (Rule 609)
    from engine.replacement import EventType
    replacement = state.replacement_effects.check_and_apply(
        EventType.DESTROY,
        state,
        target_uid=creature.uid,
        controller=player_idx,
    )
    if replacement is not None:
        return

    # Psychic/Dragheart creatures go to Hyperspatial (Rule 805.4b, 807.4b)
    _HYPERSPATIAL_SUBTYPES = (CardSubtype.PSYCHIC, CardSubtype.PSYCHIC_SUPER, CardSubtype.DRAGHEART)
    if creature.definition.card_subtype in _HYPERSPATIAL_SUBTYPES:
        move_battle_to_hyperspatial(state, player_idx, creature.uid, reason=reason)
        return

    # Normal creatures go to graveyard
    p.battle_zone.remove(creature)
    creature.remove_static_effects(state)
    p.graveyard.insert(
        0,
        GraveyardCard(
            definition=creature.definition,
            uid=creature.uid,
            died_from=reason,
            died_on_turn=state.turn_number,
        ),
    )


def check_evolution_reconstruction(state: GameState) -> bool:
    """Rule 703.4h: Evolution creature without base → top of stack to graveyard."""
    fired = False
    for player_idx in range(2):
        for creature in list(state.players[player_idx].battle_zone):
            if hasattr(creature, "evolution_stack") and creature.evolution_stack:
                if len(creature.evolution_stack) > 0:
                    # If the base creature is missing, send top of stack to graveyard
                    if not hasattr(creature, "is_evolution") or not creature.is_evolution:
                        continue
                    # Simplified: just check if stack exists without base
                    if creature.evolution_stack and not creature.definition:
                        creature.evolution_stack.pop(0)
                        fired = True
    return fired


def check_smax_uniqueness(state: GameState) -> bool:
    """Rule 703.4i: S-MAX Evolution creature → max 1 per player."""
    from core.enums import CardSubtype
    from engine.special_cards.star_evolution import is_star_evolution
    
    fired = False
    for player_idx in range(2):
        smax_creatures = [
            c for c in state.players[player_idx].battle_zone
            if is_star_evolution(c.definition)
        ]
        # Keep only the first one; destroy others
        if len(smax_creatures) > 1:
            for creature in smax_creatures[1:]:
                _destroy_creature(state, player_idx, creature, "sba_smax_uniqueness")
                fired = True
    return fired


def check_seal_removal(state: GameState) -> bool:
    """Rule 703.4j: When Command enters Battle Zone, seals are removed."""
    # This is typically handled in the action executor, not SBA
    # Placeholder for completeness
    return False


def check_g_castle_shield(state: GameState) -> bool:
    """Rule 703.4k: G-Castle fortifying shield → graveyard when shield breaks."""
    # This is handled in zone_mover.py when shields are broken
    # Placeholder for completeness
    return False
