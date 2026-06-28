"""engine/sba/checker.py — State-based action checker orchestrator.

Implements rule 703.4.
"""

from __future__ import annotations

from core.enums import (
    GameResult, CardType, CardSubtype, Phase, GlobalEffectType
)
from core.state import GameState
from core.zones import Creature, GraveyardCard, HandCard, HyperspatialCard
from core.cards import is_g_castle
from engine.zone_mover import move_battle_to_hyperspatial, should_apply_psychic_release, should_apply_dragon_evasion, creature_to_hyperspatial_card
from engine.sba.actions import (
    _sba_direct_attack,
    _sba_deck_empty,
    _sba_zero_power,
    _sba_battle_loser,
    _sba_evolution_reconstruction,
    _sba_smax_uniqueness,
    _sba_standalone_cell,
    _sba_invalid_type,
    _sba_seal_removal,
    _sba_castle_graveyard,
    _sba_d2_field,
    _sba_standalone_weapon,
    _sba_dream_rare_uniqueness,
    _sba_duel_mate_cleanup,
    _sba_g_castle_shield,
)


# ── Orchestrator ──────────────────────────────────────────────

def check_state_based_actions(state: GameState) -> GameState:
    """
    Rule 703.3: check all SBAs and repeat until none fire.
    Returns a new GameState (never mutates input).
    """
    while True:
        new_state, any_fired = _check_once(state)
        if not any_fired:
            return new_state
        state = new_state


def _check_once(state: GameState) -> tuple[GameState, bool]:
    """
    Run one simultaneous SBA check. Returns (new_state, any_fired).
    """
    events = _collect_sba_events(state)
    
    # Apply collected events first
    s = _apply_sba_events(state, events)
    any_fired = _has_sba_events(events)
    
    # Then check evolution-specific SBAs (rule 703.4h, 815.1a)
    # These must be checked after other removals to properly handle cascading effects
    if _sba_evolution_reconstruction(s):
        any_fired = True
    
    if _sba_smax_uniqueness(s):
        any_fired = True

    if _sba_dream_rare_uniqueness(s):
        any_fired = True

    if _sba_duel_mate_cleanup(s):
        any_fired = True

    if _sba_g_castle_shield(s):
        any_fired = True

    # Re-evaluate static effects for all creatures in battle zones.
    # Cascading SBA changes (e.g. a creature died, removing its aura) may cause
    # other creatures to gain/lose power or keywords, potentially triggering
    # further SBAs on the next loop iteration.
    _reevaluate_all_static_effects(s)

    return s, any_fired


def _reevaluate_all_static_effects(state: GameState) -> None:
    """
    Re-apply static effects from all creatures currently in battle zones.

    After SBA events destroy creatures, their static auras must be removed
    and remaining creatures' static effects must be re-applied to reflect
    the new board state. This handles cascading board-wide effect changes.

    This works by:
      1. Clearing all per-card static global effects (PER_CARD_POWER_MOD,
         PER_CARD_KEYWORD_GRANT) from the registry.
      2. Clearing each creature's static_effects tracking list.
      3. Re-applying static effects from every creature still in battle zones.
    """
    # Remove all per-card sourced global effects
    per_card_types = {
        GlobalEffectType.PER_CARD_POWER_MOD,
        GlobalEffectType.PER_CARD_KEYWORD_GRANT,
    }
    state.global_effects.effects = [
        e for e in state.global_effects.effects
        if e.effect_type not in per_card_types
    ]

    # Clear all creatures' static effect tracking and re-apply
    for player_idx in range(2):
        for creature in state.players[player_idx].battle_zone:
            creature.static_effects.clear()
            creature.apply_static_effects(state)


def _collect_sba_events(state: GameState) -> dict:
    """
    Collect every currently applicable SBA from an unchanged snapshot.
    Rule 703.3 requires applying these simultaneously as one event.
    """
    events = {
        "losers": set(),
        "destroy": [],      # (player_idx, creature_uid, reason)
        "graveyard": [],    # (player_idx, card_uid, reason)
        "seal_removal": [], # (player_idx, sealed_creature_uid)
        "d2_remove": [],    # (player_idx, field_uid)
        "castles": [],      # (player_idx, castle_defn)
    }

    if state.is_terminal():
        return events

    ctx = state.attack_context
    if ctx is not None and ctx.received_direct_attack:
        events["losers"].add(ctx.defending_player)

    for player_idx in range(2):
        player = state.players[player_idx]
        if player.deck_size == 0:
            events["losers"].add(player_idx)

        for creature in player.battle_zone:
            is_creature = creature.definition.card_type == CardType.CREATURE
            if creature.is_ignored:
                continue
            if is_creature and creature.compute_power(state) <= 0 and creature.can_be_destroyed():
                events["destroy"].append((player_idx, creature.uid, "sba_zero_power"))
            if is_creature and creature.temp_flags.get("lost_battle", False) and creature.can_be_destroyed():
                events["destroy"].append((player_idx, creature.uid, "battle"))

            if creature.definition.card_type in {CardType.CELL, CardType.WEAPON}:
                reason = (
                    "sba_standalone_cell"
                    if creature.definition.card_type == CardType.CELL
                    else "sba_standalone_weapon"
                )
                events["graveyard"].append((player_idx, creature.uid, reason))

            if creature.definition.card_type in {CardType.SPELL, CardType.CASTLE, CardType.CORE}:
                events["graveyard"].append((player_idx, creature.uid, "sba_invalid_type"))

        if player.detached_castles:
            for castle_defn in player.detached_castles:
                events["castles"].append((player_idx, castle_defn))

        d2_fields = [
            c for c in player.battle_zone
            if (c.definition.card_type == CardType.FIELD
                and c.definition.card_subtype == CardSubtype.D2)
        ]
        if len(d2_fields) > 1:
            newest = next(
                (f for f in d2_fields if f.temp_flags.get("just_entered", False)),
                d2_fields[-1],
            )
            for old_field in d2_fields:
                if old_field.uid != newest.uid:
                    events["d2_remove"].append((player_idx, old_field.uid))

        for command in player.battle_zone:
            if not command.temp_flags.get("just_entered_as_command", False):
                continue
            cmd_civs = command.civilizations
            for target in player.battle_zone:
                if target.seals and target.civilizations.intersection(cmd_civs):
                    events["seal_removal"].append((player_idx, target.uid))
                    break

    return events


def _has_sba_events(events: dict) -> bool:
    return any(bool(value) for value in events.values())


def _apply_sba_events(state: GameState, events: dict) -> GameState:
    """Apply a collected SBA batch to a copied state."""
    s = state.copy()

    if events["losers"]:
        if len(events["losers"]) == 1:
            loser = next(iter(events["losers"]))
            winner = 1 - loser
            s.result = (
                GameResult.PLAYER_0_WINS if winner == 0
                else GameResult.PLAYER_1_WINS
            )
        else:
            s.result = GameResult.DRAW

    destroyed: set[tuple[int, str]] = set()
    for player_idx, creature_uid, reason in events["destroy"]:
        key = (player_idx, creature_uid)
        if key in destroyed:
            continue
        creature = s.players[player_idx].find_creature(creature_uid)
        if creature is None:
            continue
        creature.clear_flag("lost_battle")
        _destroy_creature(s, player_idx, creature, reason)
        destroyed.add(key)

    moved_to_graveyard: set[tuple[int, str]] = set()
    for player_idx, creature_uid, reason in events["graveyard"]:
        key = (player_idx, creature_uid)
        if key in moved_to_graveyard or key in destroyed:
            continue
        creature = s.players[player_idx].find_creature(creature_uid)
        if creature is None:
            continue
        s.players[player_idx].battle_zone.remove(creature)

        if reason == "sba_standalone_weapon":
            # Rule 807.4b: Dragheart Weapon must return to Hyperspatial, not graveyard.
            # Reset face/state and send to owner's hyperspatial zone.
            s.global_effects.remove_by_source(creature.uid)
            creature.face = 0
            creature.is_tapped = False
            creature.power_modifiers.clear()
            creature.temp_flags.clear()
            owner = creature.owner
            s.players[owner].hyperspatial_zone.append(creature_to_hyperspatial_card(creature))
        elif reason == "sba_standalone_cell":
            if creature.definition.is_king_cell():
                # Rule 814.1: King Cell alone in BZ → graveyard (not hyperspatial).
                s.players[player_idx].graveyard.insert(
                    0,
                    GraveyardCard(
                        definition=creature.definition,
                        uid=creature.uid,
                        died_from=reason,
                        died_on_turn=s.turn_number,
                    ),
                )
            else:
                # Rule 806.1a: Psychic Cell → graveyard first, then immediately to Hyperspatial.
                s.players[player_idx].graveyard.insert(
                    0,
                    GraveyardCard(
                        definition=creature.definition,
                        uid=creature.uid,
                        died_from=reason,
                        died_on_turn=s.turn_number,
                    )
                )
                # Immediately move out of graveyard and into owner's hyperspatial zone
                gy_card = s.players[player_idx].graveyard[0]
                if gy_card.uid == creature.uid:
                    s.players[player_idx].graveyard.pop(0)
                creature.face = 0
                creature.is_tapped = False
                creature.power_modifiers.clear()
                creature.temp_flags.clear()
                creature.is_psychic_cell = False
                creature.linked_cells.clear()
                owner = creature.owner
                s.players[owner].hyperspatial_zone.append(creature_to_hyperspatial_card(creature))
        else:
            s.players[player_idx].graveyard.insert(
                0,
                GraveyardCard(
                    definition=creature.definition,
                    uid=creature.uid,
                    died_from=reason,
                    died_on_turn=s.turn_number,
                )
            )
        moved_to_graveyard.add(key)

    for player_idx, target_uid in events["seal_removal"]:
        target = s.players[player_idx].find_creature(target_uid)
        if target is None or not target.seals:
            continue
        seal_defn = target.seals.pop(0)
        s.players[player_idx].graveyard.insert(
            0,
            GraveyardCard(
                definition=seal_defn,
                died_from="sba_seal_removal",
                died_on_turn=s.turn_number,
            )
        )

    for player_idx, castle_defn in events["castles"]:
        s.players[player_idx].graveyard.insert(
            0,
            GraveyardCard(
                definition=castle_defn,
                died_from="sba_castle_detach",
                died_on_turn=s.turn_number,
            )
        )
        s.players[player_idx].detached_castles = []

    for player_idx, field_uid in events["d2_remove"]:
        field = s.players[player_idx].find_creature(field_uid)
        if field is None:
            continue
        s.players[player_idx].battle_zone.remove(field)
        s.players[player_idx].graveyard.insert(
            0,
            GraveyardCard(
                definition=field.definition,
                uid=field.uid,
                died_from="sba_d2_field",
                died_on_turn=s.turn_number,
            )
        )
        s.global_effects.remove_by_source(field.uid)

    for player_idx in range(2):
        for creature in s.players[player_idx].battle_zone:
            creature.clear_flag("just_entered")
            creature.clear_flag("just_entered_as_command")

    return s


# ─────────────────────────────────────────────────────────────────────────────
# Individual SBA implementations
# ─────────────────────────────────────────────────────────────────────────────


# ── Turn limit & utilities ───────────────────────────────────

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

def _destroy_creature(
    state: GameState,
    player_idx: int,
    creature: Creature,
    reason: str,
) -> None:
    """
    Move a creature from the battle zone to its destination zone.

    - Normal creatures → graveyard.
    - Psychic / Dragheart creatures → owner's Hyperspatial Zone (rules 805.4b, 807.4b).
      Movement to any non-Battle Zone zone cannot be prevented for these card types.

    Does NOT trigger "when destroyed" effects — those are queued by
    trigger_resolver.py after the destroy action is applied.
    """
    p = state.players[player_idx]

    if creature not in p.battle_zone:
        return

    # ── Replacement effect check (rule 609, centralized registry) ──────────────
    # Check the ReplacementEffectRegistry for any DESTROY replacement before
    # falling through to the legacy hardcoded checks below.
    from engine.replacement import EventType
    replacement = state.replacement_effects.check_and_apply(
        EventType.DESTROY,
        state,
        target_uid=creature.uid,
        controller=player_idx,
    )
    if replacement is not None:
        creature.temp_flags["_replacement_already_applied"] = True
        return

    # ── Legacy hardcoded replacement checks (Psychic Release / Dragon Evasion) ──
    # These remain for backward compatibility with temp_flag-driven cards.
    if should_apply_psychic_release(creature) or should_apply_dragon_evasion(creature):
        creature.temp_flags["_replacement_already_applied"] = True
        return

    _HYPERSPATIAL_SUBTYPES = (CardSubtype.PSYCHIC, CardSubtype.PSYCHIC_SUPER, CardSubtype.DRAGHEART)
    if creature.definition.card_subtype in _HYPERSPATIAL_SUBTYPES:
        # Rules 805.4b / 807.4b: return to Hyperspatial instead of graveyard
        move_battle_to_hyperspatial(state, player_idx, creature.uid, reason=reason)
        return

    p.battle_zone.remove(creature)
    creature.remove_static_effects(state)

    # Move to graveyard (newest first)
    p.graveyard.insert(
        0,
        GraveyardCard(
            definition=creature.definition,
            uid=creature.uid,
            died_from=reason,
            died_on_turn=state.turn_number,
        )
    )

# ─────────────────────────────────────────────────────────────────────────────
# Dream Rare uniqueness (rule 817)
# ─────────────────────────────────────────────────────────────────────────────

def _sba_dream_rare_uniqueness(state: GameState) -> bool:
    """
    Rule 817: Dream Rare creatures must be unique per player.
    Only one of each Dream Rare card_id per player is allowed.

    If duplicates are found, keep the one that entered most recently,
    send extras to the graveyard.
    """
    fired = False
    for player_idx in range(2):
        dream_rares = [
            c for c in state.players[player_idx].battle_zone
            if c.definition.card_subtype == CardSubtype.DREAM
        ]

        # Group by card_id
        by_id: dict[int, list] = {}
        for creature in dream_rares:
            cid = creature.definition.id
            by_id.setdefault(cid, []).append(creature)

        for cid, creatures in by_id.items():
            if len(creatures) <= 1:
                continue
            # Keep the one with the highest entered_turn (most recent)
            creatures.sort(key=lambda c: c.entered_turn, reverse=True)
            for creature in creatures[1:]:
                state.players[player_idx].battle_zone.remove(creature)
                state.global_effects.remove_by_source(creature.uid)
                state.players[player_idx].graveyard.insert(
                    0,
                    GraveyardCard(
                        definition=creature.definition,
                        uid=creature.uid,
                        died_from="sba_dream_rare_duplicate",
                        died_on_turn=state.turn_number,
                    ),
                )
                fired = True

    return fired


# ─────────────────────────────────────────────────────────────────────────────
# Duel Mate cleanup (rule 820)
# ─────────────────────────────────────────────────────────────────────────────

def _sba_duel_mate_cleanup(state: GameState) -> bool:
    """
    Rule 820: Duel Mates that are in the Battle Zone but not properly
    summoned should be moved to the Hyperspatial Zone.
    Established Duel Mates (those that survived a full turn cycle) stay.

    A Duel Mate is properly summoned if it came via the Duel Mate summon
    action path (temp_flags["properly_summoned_as_duel_mate"]).
    Otherwise, evict freshly-arrived Duel Mates that still have summoning sickness.
    """
    from core.cards import is_duel_mate

    fired = False
    for player_idx in range(2):
        duel_mates = [
            c for c in state.players[player_idx].battle_zone
            if is_duel_mate(c.definition)
        ]

        for creature in duel_mates:
            # Evict Duel Mates that still have summoning sickness AND were not
            # properly summoned. Established Duel Mates (no sickness) are fine.
            properly_summoned = creature.temp_flags.get("properly_summoned_as_duel_mate", False)
            if creature.has_summoning_sickness and not properly_summoned:
                creature.remove_static_effects(state)
                state.players[player_idx].battle_zone.remove(creature)
                state.global_effects.remove_by_source(creature.uid)
                state.players[creature.owner].hyperspatial_zone.append(
                    creature_to_hyperspatial_card(creature)
                )
                fired = True

    return fired


# ─────────────────────────────────────────────────────────────────────────────
# G-Castle shield zone (rule 822)
# ─────────────────────────────────────────────────────────────────────────────

def _sba_g_castle_shield(state: GameState) -> bool:
    """
    Rule 822: G-Castle cards that leave the Shield Zone go to the Graveyard
    instead of the hand.

    This SBA acts as a safety net:
    1. Checks the shield trigger queue for G-Castle cards that should go to
       graveyard instead of hand.
    2. Checks for G-Castle cards in the shield zone that are somehow orphaned
       (e.g., a G-Castle that was placed in the shield zone directly rather
       than being fortified under a shield).
    """
    fired = False
    for player_idx in range(2):
        # ── 1. Check shield trigger queue for G-Castles ────────────────────
        queue = state.effect_stack.shield_trigger_queue
        remaining_queue: list[tuple[int, "ShieldCard"]] = []
        for queued_player, shield in queue:
            if queued_player == player_idx and is_g_castle(shield.definition):
                # G-Castle in trigger queue → send to graveyard, not hand
                state.players[player_idx].graveyard.insert(
                    0,
                    GraveyardCard(
                        definition=shield.definition,
                        uid=shield.uid,
                        died_from="g_castle_shield_break",
                        died_on_turn=state.turn_number,
                    ),
                )
                fired = True
                # Do NOT add back to queue — it's been resolved
            else:
                remaining_queue.append((queued_player, shield))
        if fired:
            queue.clear()
            queue.extend(remaining_queue)

        # ── 2. Check shield zone for orphaned G-Castles ─────────────────────
        # A G-Castle should normally only be in the shield zone as a fortified
        # card under a shield, not as a shield itself. If one ends up as a
        # shield directly (e.g., due to an effect), it stays in the shield
        # zone but we track it here for safety.
        shield_zone = state.players[player_idx].shield_zone
        g_castle_shields = [
            s for s in shield_zone
            if s.definition.card_subtype == CardSubtype.G_CASTLE
        ]
        # G-Castle cards in the shield zone are valid as shields. They will
        # be properly handled when they break (via move_standby_shield_to_hand
        # or the trigger queue check above). We just verify they exist here.
        for gc_shield in g_castle_shields:
            # Ensure the G-Castle shield is properly tracked
            # (no-op if already correct; this is a validation pass)
            if not gc_shield.is_revealed:
                # Unrevealed G-Castle shields are fine — they're just shields
                pass

    return fired
