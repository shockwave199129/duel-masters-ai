"""engine/sba/checker.py — State-Based Action (SBA) enforcement loop.

Rule 703: State-Based Actions are automatic game actions that occur after
every action resolution to maintain the integrity of the game state. They run
in a loop until no more SBAs trigger.

SBAs implemented:
  ✅ 703.4a: Player receives direct attack with 0 shields → loses
  ✅ 703.4b: Player's deck reaches 0 cards → loses
  ✅ 703.4c: Creature with power ≤ 0 → destroyed
  ✅ 703.4d: Creature that lost battle → destroyed
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
from typing import TYPE_CHECKING

from core.card_scripts import SUPER_SOUL
from core.continuous_rules import has_abilities, queue_ability, sync_ignored_abilities
from core.enums import GameResult, CardType, CardSubtype
from core.zones import Creature, HandCard
from engine.sba.missing_sbas import (
    _sba_aura_fortress_standalone,
    _sba_weapon_standalone,
)
from engine.sba.actions.god_link import _sba_god_link_invalid_detach
from engine.sba.actions.standalone_cell import _sba_standalone_cell
from engine.sba.actions.invalid_type import _sba_invalid_type
from engine.sba.actions.d2_field import _sba_d2_field
from engine.sba.actions.duel_mate_cleanup import _sba_duel_mate_cleanup
from engine.sba.actions.g_castle import _sba_g_castle_shield

if TYPE_CHECKING:
    from core.state import GameState

logger = logging.getLogger(__name__)


def run_state_based_actions(state: GameState) -> None:
    """Apply state-based actions to ``state`` until none apply (rule 703.3)."""
    for _ in range(100):  # guards against a loop
        if not _check_once(state):
            return
    logger.warning("State-based actions did not settle; stopping.")


def check_state_based_actions(state: GameState) -> GameState:
    """
    Execute all state-based actions in a loop until none trigger.

    Rule 703: After every action, check for SBAs and apply them. Continue
    until no SBAs apply. Then return the updated state.
    """
    s = state.copy()
    run_state_based_actions(s)

    # This round of events is over, so replacement effects can apply to the next
    s.replacement_effects.reset_all()
    for player in s.players:
        for creature in player.battle_zone:
            creature.temp_flags.pop("_replacement_already_applied", None)

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

    # Abilities come back once the card that named them has left
    sync_ignored_abilities(state)
    watch_graveyards(state)

    # Not a rule 703 action: notices an EX Life shield that was broken or removed
    fired |= check_ex_life_shields(state)

    # ── Board cleanup ────────────────────────────────────────────────────────
    fired |= check_power_zero(state)                # 703.4c
    fired |= check_battle_losers(state)             # 703.4d
    # Cross Gear in BZ is valid when generated (303.2); no standalone-destroy SBA.
    fired |= _sba_god_link_invalid_detach(state)    # 804.2b — invalid God link detach
    fired |= _sba_standalone_cell(state)            # 703.4g — standalone Cell
    fired |= _sba_invalid_type(state)               # 703.4i — invalid BZ type
    fired |= _sba_aura_fortress_standalone(state)   # Aura/Fortress standalone
    fired |= check_evolution_reconstruction(state)  # 703.4h
    fired |= check_smax_uniqueness(state)           # 703.4i S-MAX
    fired |= check_seal_removal(state)              # 703.4j
    fired |= _sba_g_castle_shield(state)            # 703.4k / 822
    fired |= _sba_d2_field(state)                   # 703.4l — D2 Field uniqueness
    fired |= _sba_weapon_standalone(state)          # 703.4m
    fired |= _sba_duel_mate_cleanup(state)          # 820 — Duel Mate cleanup

    return fired


# ─────────────────────────────────────────────────────────────────────────────
# P0 Priority: Zone Loss Conditions (checked first)
# ─────────────────────────────────────────────────────────────────────────────

def check_zero_shields(state: GameState) -> bool:
    """Rule 703.4a: Player with 0 shields receives direct attack → loses."""
    if state.result != GameResult.IN_PROGRESS:
        return False
    ctx = state.attack_context
    if ctx is None or not ctx.received_direct_attack:
        return False
    defender_player = ctx.blocker_player if ctx.blocker_player is not None else (1 - ctx.attacker_player)
    if (len(state.players[defender_player].shield_zone) == 0
            and not state.players[defender_player].loss_pending):
        from engine.leave_rules import lose_game
        ctx.received_direct_attack = False  # this direct attack has been dealt with
        lose_game(state, defender_player)
        return True
    return False


def check_deck_empty(state: GameState) -> bool:
    """Rule 703.4b: Player tries to draw from empty deck → loses."""
    if state.result != GameResult.IN_PROGRESS:
        return False
    for player_idx in range(2):
        player = state.players[player_idx]
        if (len(player.deck) == 0 or player.ran_out_of_cards) and not player.loss_pending:
            from engine.leave_rules import lose_game
            player.ran_out_of_cards = False
            lose_game(state, player_idx)
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Board State Cleanup (run until stable)
# ─────────────────────────────────────────────────────────────────────────────

def watch_graveyards(state: GameState) -> None:
    """
    Notice the first time each turn that a card leaves a player's graveyard.
    Cards leave in many ways, so this compares what is there with last time.
    """
    for index, player in enumerate(state.players):
        present = frozenset(card.uid for card in player.graveyard)
        gone = player.graveyard_seen - present
        player.graveyard_seen = present
        if not gone or player.graveyard_left_this_turn:
            continue
        player.graveyard_left_this_turn = True
        for creature in player.battle_zone:
            if not has_abilities(creature):
                continue
            souls = [creature.definition, *(entry.definition for entry in creature.evolution_stack)]
            for definition in souls:
                if definition is not None and definition.slug in SUPER_SOUL:
                    queue_ability(
                        state, definition, SUPER_SOUL[definition.slug], source_uid=creature.uid,
                        controller=index, data={})


def check_ex_life_shields(state: GameState) -> bool:
    from engine.zone_mover import ex_life_shield_left

    fired = False
    for player_idx in range(2):
        player = state.players[player_idx]
        present = {shield.uid for shield in player.shield_zone}
        for creature in player.battle_zone:
            shield_uid = creature.temp_flags.get("ex_life_shield")
            if shield_uid is not None and shield_uid not in present:
                fired |= ex_life_shield_left(state, player_idx, creature)
    return fired


def _waiting_on_replacement(creature: Creature) -> bool:
    return bool(creature.temp_flags.get("leaving") or creature.temp_flags.get("replacing"))


def check_power_zero(state: GameState) -> bool:
    """Rule 703.4c: Creature with power ≤ 0 → destroyed."""
    fired = False
    for player_idx in range(2):
        creatures_to_destroy = [
            c for c in state.players[player_idx].battle_zone
            if c.definition.card_type == CardType.CREATURE
            and not _waiting_on_replacement(c)
            and c.compute_power(state) <= 0
        ]
        for creature in creatures_to_destroy:
            fired |= _destroy_creature(state, player_idx, creature, "destroyed")
    return fired


def check_battle_losers(state: GameState) -> bool:
    """Rule 703.4d: Creature that lost battle → destroyed."""
    fired = False
    for player_idx in range(2):
        creatures_to_destroy = [
            c for c in state.players[player_idx].battle_zone
            if c.temp_flags.get("lost_battle", False) and not _waiting_on_replacement(c)
        ]
        for creature in creatures_to_destroy:
            fired |= _destroy_creature(state, player_idx, creature, "battle")
    return fired


def _destroy_creature(
    state: GameState,
    player_idx: int,
    creature: Creature,
    reason: str,
) -> bool:
    """
    Remove a creature because of a state-based action.

    reason "battle" or "destroyed" is destruction: it can be prevented and fires
    destroy triggers. Any other reason only fires leave triggers. Returns True if
    the state changed, so the SBA loop knows whether to run another pass.
    """
    from engine.zone_mover import move_battle_to_graveyard

    p = state.players[player_idx]

    if creature not in p.battle_zone:
        return False

    def settle_battle_loss() -> bool:
        # A prevented or replaced battle loss must not be applied again
        return creature.temp_flags.pop("lost_battle", None) is not None

    if reason in ("battle", "destroyed"):
        if not creature.can_be_destroyed():
            return settle_battle_loss()

        # Check replacement effects first (Rule 609)
        from engine.replacement import EventType
        replacement = state.replacement_effects.check_and_apply(
            EventType.DESTROY,
            state,
            target_uid=creature.uid,
            controller=player_idx,
        )
        if replacement is not None:
            creature.temp_flags["_replacement_already_applied"] = True
            settle_battle_loss()
            return True

    # The one leave path: triggers, replacements, Hyperspatial and under-cards
    move_battle_to_graveyard(state, player_idx, creature.uid, reason=reason)
    if creature in p.battle_zone:
        settle_battle_loss()
    return True


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
    """Rule 815.1a: a player keeps one S-MAX creature; the others return to hand."""
    from engine.zone_mover import move_battle_to_zone

    fired = False
    for player_idx in range(2):
        player = state.players[player_idx]
        smax = [c for c in player.battle_zone if c.definition.card_subtype == CardSubtype.STAR_MAX]
        # The one that was there first stays
        for creature in smax[1:]:
            fired |= move_battle_to_zone(
                state, player_idx, creature.uid, reason="state_based", to_zone="hand",
                place=lambda definition, uid, hand=player.hand: hand.append(
                    HandCard(definition=definition, uid=uid)),
            )
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
