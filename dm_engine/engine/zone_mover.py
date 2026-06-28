"""
engine/zone_mover.py — centralized card movement helpers.

These helpers mutate the GameState object they receive. Callers that expose a
public API should copy the state before using them, then return the copy.
"""

from __future__ import annotations

from typing import Optional

from core.enums import ManaUsage, CardSubtype
from core.state import GameState
from core.zones import Creature, EvolutionStackEntry, GraveyardCard, HandCard, ManaCard, ShieldCard, HyperspatialCard, _new_uid
from core.cards import CardDefinition, is_twinpact, is_forbidden, get_other_face, is_hyper_mode, is_g_castle


def creature_to_hyperspatial_card(creature: Creature) -> HyperspatialCard:
    """Convert a Creature object to a HyperspatialCard for return to hyperspatial zone."""
    return HyperspatialCard(
        definition=creature.definition,
        face=creature.face,
        uid=creature.uid,
    )


def tap_mana_for_payment(state: GameState, player: int, mana_used: tuple[ManaUsage, ...]) -> None:
    """Tap the exact mana cards selected to pay a cost."""
    p_state = state.players[player]
    for usage in mana_used:
        mana = p_state.find_mana(usage.mana_uid)
        if mana is None:
            raise ValueError(f"Mana card {usage.mana_uid} not found for player {player}")
        if mana.is_tapped:
            raise ValueError(f"Mana card {usage.mana_uid} is already tapped")
        mana.tap()


def move_hand_to_mana(state: GameState, player: int, card_uid: str) -> ManaCard:
    """Move a hand card into mana, applying tapped entry for multi-civ cards."""
    hand_card = _remove_from_hand(state, player, card_uid)
    mana = ManaCard.from_charge(hand_card.definition)
    state.players[player].mana_zone.append(mana)
    state.players[player].has_charged_mana_this_turn = True
    
    # Fire ON_MANA_CHARGE trigger
    from core.enums import TriggerEvent
    from engine.trigger_registry import fire_trigger
    fire_trigger(state, TriggerEvent.ON_MANA_CHARGE, {
        "source_uid": mana.uid,
        "source_card_id": mana.id,
        "controller": player,
        "zone": "mana_zone",
    }, mana.uid)
    
    return mana


def move_hand_to_battle(
    state: GameState,
    player: int,
    card_uid: str,
    *,
    evolution_base_uid: Optional[str] = None,
) -> Creature:
    """
    Move a creature from hand to battle, or evolve an existing base.
    
    Rule 801.2: The creature remains the same object; only the top card changes.
    Rule 801.3: Evolution creatures do not suffer summoning sickness.
    """
    hand_card = _remove_from_hand(state, player, card_uid)
    p_state = state.players[player]

    if evolution_base_uid:
        base = p_state.find_creature(evolution_base_uid)
        if base is None:
            raise ValueError(f"Evolution base {evolution_base_uid} not found")

        # Rule 801.2: Push the previous top card as a stack entry.
        # The creature UID remains constant (same creature). The stack entry gets a new uid.
        entry = EvolutionStackEntry(
            definition=base.definition,
            uid=_new_uid(),  # New uid for the pushed card in the stack
            owner=base.owner,
            entered_turn=base.entered_turn,
            neo_evolution_placed=False  # Only true if explicitly set by NEO Evolution logic
        )
        base.push_to_evolution_stack(entry)

        # Update the top card to the hand card being evolved.
        # Creature.uid remains unchanged (same creature per rule 801.2).
        base.definition = hand_card.definition

        # Rule 801.3: No summoning sickness on evolution.
        base.has_summoning_sickness = False

        # Fire ON_ENTER_BATTLE_ZONE trigger for evolution
        _fire_enter_battle_zone_trigger(state, base, player)
        return base

    creature = Creature(
        definition=hand_card.definition,
        uid=hand_card.uid,
        controller=player,
        owner=player,
        entered_turn=state.turn_number,
        has_summoning_sickness=True,
    )
    p_state.battle_zone.append(creature)
    creature.apply_static_effects(state)
    # Phase 5A: Twinpact flip on summon
    flip_twinpact(creature)
    
    # Fire ON_SUMMON and ON_ENTER_BATTLE_BATTLE_ZONE triggers
    _fire_enter_battle_zone_trigger(state, creature, player)
    
    return creature


def _fire_enter_battle_zone_trigger(state: GameState, creature, player: int) -> None:
    """Fire ON_ENTER_BATTLE_ZONE and ON_SUMMON triggers for a creature entering BZ."""
    from core.enums import TriggerEvent
    from engine.trigger_registry import fire_trigger
    
    trigger_data = {
        "source_uid": creature.uid,
        "source_card_id": creature.id,
        "controller": player,
        "zone": "battle_zone",
    }
    
    # Fire ON_ENTER_BATTLE_ZONE (always for entering BZ)
    fire_trigger(state, TriggerEvent.ON_ENTER_BATTLE_ZONE, trigger_data, creature.uid)
    
    # Fire ON_SUMMON (for summons from hand/mana/etc., not for evolution)
    # The caller can distinguish by checking if it was an evolution
    # For now, we fire both - the condition system can filter
    fire_trigger(state, TriggerEvent.ON_SUMMON, trigger_data, creature.uid)


def _fire_leave_battle_zone_triggers(state: GameState, creature, player: int, reason: str) -> None:
    """Fire ON_DESTROY and ON_LEAVE_BATTLE_ZONE triggers for a creature leaving BZ."""
    from core.enums import TriggerEvent
    from engine.trigger_registry import fire_trigger
    
    trigger_data = {
        "source_uid": creature.uid,
        "source_card_id": creature.id,
        "controller": player,
        "zone": "battle_zone",
        "reason": reason,
        "from_zone": "battle_zone",
        "to_zone": "graveyard" if reason != "returned_to_hyperspatial" else "hyperspatial_zone",
    }
    
    # Fire ON_DESTROY (for destruction)
    if reason in ("destroyed", "battle", "sacrificed", "effect"):
        fire_trigger(state, TriggerEvent.ON_DESTROY, trigger_data, creature.uid)
    
    # Fire ON_LEAVE_BATTLE_ZONE (always when leaving BZ)
    fire_trigger(state, TriggerEvent.ON_LEAVE_BATTLE_ZONE, trigger_data, creature.uid)


def move_hand_to_graveyard(
    state: GameState,
    player: int,
    card_uid: str,
    *,
    reason: str = "discarded",
) -> GraveyardCard:
    """Move a card from hand to the graveyard."""
    hand_card = _remove_from_hand(state, player, card_uid)
    graveyard_card = GraveyardCard(
        definition=hand_card.definition,
        uid=hand_card.uid,
        died_from=reason,
        died_on_turn=state.turn_number,
    )
    state.players[player].graveyard.insert(0, graveyard_card)
    return graveyard_card


def move_hand_to_shield(
    state: GameState,
    player: int,
    card_uid: str,
) -> ShieldCard:
    """Move a card from hand into the shield zone face-down."""
    hand_card = _remove_from_hand(state, player, card_uid)
    shield_card = ShieldCard(definition=hand_card.definition, uid=hand_card.uid)
    state.players[player].shield_zone.append(shield_card)
    return shield_card


def move_battle_to_hyperspatial(
    state: GameState,
    player: int,
    creature_uid: str,
    *,
    reason: str = "returned",
) -> Creature:
    """
    Return a Psychic or Dragheart creature from the Battle Zone to its owner's
    Hyperspatial Zone (rules 805.4b, 807.4b).

    - The card returns to the OWNER's hyperspatial zone, not the controller's.
    - face resets to 0 (lower-cost face).
    - All temp state (flags, power modifiers, tapped, etc.) is cleared.
    - Global effects sourced from this creature are removed.
    - This movement cannot be prevented by card effects (rules 805.4b, 807.4b).
    """
    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Battle-zone card {creature_uid} not found")

    # Phase 5A: Forbidden flip before leaving battle zone
    flip_forbidden(creature)

    creature.remove_static_effects(state)
    state.players[player].battle_zone.remove(creature)

    # Reset in-play state before returning to hyperspatial
    creature.is_tapped = False
    creature.has_summoning_sickness = True
    creature.entered_turn = 0
    creature.face = 0
    creature.power_modifiers.clear()
    creature.attached_cards.clear()
    creature.temp_flags.clear()
    creature.has_attacked_this_turn = False
    creature.is_blocking = False
    creature.blocking_uid = None
    creature.hyper_mode_released = False
    creature.seals.clear()
    creature.linked_cells.clear()
    creature.is_psychic_cell = False

    # Return to owner's hyperspatial zone (rule 219: "returned to Hyperspatial Zone")
    # Convert Creature to HyperspatialCard for type safety
    owner = creature.owner
    state.players[owner].hyperspatial_zone.append(creature_to_hyperspatial_card(creature))
    return creature

def move_hand_to_hyperspatial(
    state: GameState,
    player: int,
    hand_card_uid: str,
) -> HyperspatialCard:
    """
    Move a card from the hand directly into the Hyperspatial Zone (rule 820).

    Used for Duel Mate cards returned to the Hyperspatial Zone, as well as
    other effects that place hand cards into hyperspatial.
    """
    hand_card = _remove_from_hand(state, player, hand_card_uid)
    h_card = HyperspatialCard(
        definition=hand_card.definition,
        uid=hand_card.uid,
    )
    state.players[player].hyperspatial_zone.append(h_card)
    return h_card


def move_battle_to_graveyard(
    state: GameState,
    player: int,
    creature_uid: str,
    *,
    reason: str = "destroyed",
) -> GraveyardCard:
    """
    Move a battle-zone card to the graveyard.

    Rules 805.4b / 807.4b: If the card is Psychic or Dragheart, it instead
    returns to the owner's Hyperspatial Zone immediately. In that case a
    sentinel GraveyardCard is NOT returned; callers must handle None.
    """
    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Battle-zone card {creature_uid} not found")

    # Phase 5A: Forbidden flip before leaving battle zone
    flip_forbidden(creature)

    # ── Replacement effect check (rule 609, centralized registry) ──────────────
    # Check the ReplacementEffectRegistry for any applicable replacement before
    # falling through to the legacy hardcoded checks below.
    from engine.replacement import EventType
    replacement = state.replacement_effects.check_and_apply(
        EventType.LEAVE_BATTLE_ZONE,
        state,
        target_uid=creature_uid,
        controller=player,
    )
    if replacement is not None:
        # A replacement effect was found and applied. The caller is responsible
        # for interpreting replacement_action (e.g. "flip_face", "banish").
        # Mark the legacy flag so downstream code knows a replacement fired.
        creature.temp_flags["_replacement_already_applied"] = True
        return GraveyardCard(
            definition=creature.definition,
            uid=creature.uid,
            died_from=f"{reason}_replacement_{replacement.replacement_action}",
            died_on_turn=state.turn_number,
        )

    # ── Legacy hardcoded replacement checks (Psychic Release / Dragon Evasion / G-NEO) ──
    # These remain for backward compatibility with temp_flag-driven cards.
    # G-NEO all-leave replacement (rule 803.2): when a G-NEO creature with
    # placed cards leaves BZ, ALL placed cards leave instead of just the top card.
    if should_apply_gneo_all_leave_replacement(creature):
        # Move the entire evolution stack to graveyard
        creature.temp_flags["_replacement_already_applied"] = True
        # Move underlying cards first (they leave as cards, not creatures)
        for entry in creature.evolution_stack:
            state.players[player].graveyard.insert(0, GraveyardCard(
                definition=entry.definition,
                uid=entry.uid if hasattr(entry, 'uid') else creature.uid,
                died_from=f"{reason}_gneo_all_leave",
                died_on_turn=state.turn_number,
            ))
        # Move the top card to graveyard
        creature.remove_static_effects(state)
        state.players[player].battle_zone.remove(creature)
        graveyard_card = GraveyardCard(
            definition=creature.definition,
            uid=creature.uid,
            died_from=f"{reason}_gneo_all_leave",
            died_on_turn=state.turn_number,
        )
        state.players[player].graveyard.insert(0, graveyard_card)
        return graveyard_card

    if should_apply_psychic_release(creature) or should_apply_dragon_evasion(creature):
        # Mark replacement applied — actual flip is handled by the caller via the
        # apply_psychic_release / apply_dragon_evasion helpers.
        creature.temp_flags["_replacement_already_applied"] = True
        return GraveyardCard(
            definition=creature.definition,
            uid=creature.uid,
            died_from=f"{reason}_release_replacement",
            died_on_turn=state.turn_number,
        )

    # Rules 805.4b / 807.4b — Psychic/Dragheart must return to Hyperspatial
    _HYPERSPATIAL_SUBTYPES = (CardSubtype.PSYCHIC, CardSubtype.PSYCHIC_SUPER, CardSubtype.DRAGHEART)
    if creature.definition.card_subtype in _HYPERSPATIAL_SUBTYPES:
        # Fire leave triggers before moving to hyperspatial
        _fire_leave_battle_zone_triggers(state, creature, player, reason)
        move_battle_to_hyperspatial(state, player, creature_uid, reason=reason)
        # Return a minimal GraveyardCard as a non-None sentinel so callers
        # that store the return value don't crash; the card is NOT in GY.
        return GraveyardCard(
            definition=creature.definition,
            uid=creature.uid,
            died_from=f"{reason}_returned_to_hyperspatial",
            died_on_turn=state.turn_number,
        )

    # Fire leave triggers before removing from battle zone
    _fire_leave_battle_zone_triggers(state, creature, player, reason)
    
    creature.remove_static_effects(state)
    state.players[player].battle_zone.remove(creature)
    graveyard_card = GraveyardCard(
        definition=creature.definition,
        uid=creature.uid,
        died_from=reason,
        died_on_turn=state.turn_number,
    )
    state.players[player].graveyard.insert(0, graveyard_card)
    return graveyard_card


def move_evolution_whole_stack_to_graveyard(
    state: GameState,
    player: int,
    creature_uid: str,
    *,
    reason: str = "destroyed",
) -> None:
    """
    Move an Evolution Creature and all its underlying cards to the graveyard.
    
    Rule 316.3: When an element (including Evolution Creature) is chosen by an
    effect, all cards comprising that element are affected.
    Rule 109.2b: The top card leaves the Battle Zone as a creature; the
    underlying cards simply leave as cards. Only the top card fires creature-leave
    / on-destroy triggers.
    
    This function:
      1. Moves the top card to graveyard as a creature (fires triggers)
      2. Moves all evolution stack entries to graveyard as cards (no triggers)
      3. Removes the creature from the battle zone
    """
    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Battle-zone card {creature_uid} not found")

    p_state = state.players[player]

    # Move all underlying cards to graveyard (as cards, not creatures)
    # Do this first so the top card is cleared
    for under_entry in creature.get_all_under_cards():
        graveyard_card = GraveyardCard(
            definition=under_entry.definition,
            uid=under_entry.uid,
            died_from=reason,
            died_on_turn=state.turn_number,
        )
        p_state.graveyard.insert(0, graveyard_card)

    # Fire leave triggers for the top card ONLY (per rule 109.2b)
    _fire_leave_battle_zone_triggers(state, creature, player, reason)
    
    # Now remove the creature from battle zone and move top card to graveyard
    creature.remove_static_effects(state)
    p_state.battle_zone.remove(creature)

    # Top card goes to graveyard as a creature
    graveyard_card = GraveyardCard(
        definition=creature.definition,
        uid=creature.uid,
        died_from=reason,
        died_on_turn=state.turn_number,
    )
    p_state.graveyard.insert(0, graveyard_card)


def remove_top_evolution_card_and_reconstruct(
    state: GameState,
    player: int,
    creature_uid: str,
    *,
    reason: str = "destroyed",
) -> Optional[Creature]:
    """
    Remove only the top card of an Evolution Creature, leaving it to reconstruct
    from its underlying stack per rule 801.4.
    
    This implements the "top-only leave" mechanic for:
      - Star Evolution (rule 813.1): when leaving, only top leaves
      - Forbidden Star Evolution (rule 813.1b): same top-only behavior
      - Direct bounce/removal of only the top card
    
    Rule 801.4a-d: Reconstruction loop:
      1. Check the top card in the stack
      2. If it can exist standalone in the Battle Zone, leave it as the new top
      3. If not, move it to graveyard and repeat
      4. Reconstruction card does not re-enter; inherits effects; keeps orientation
    
    Returns:
      - The reconstructed Creature if one remains in the battle zone
      - None if all cards from the stack were invalid and moved to graveyard
    
    NOTE: The actual SBA reconstruction loop (checking if underlying cards are valid)
    happens in sba_checker.py after this function returns. This function just:
      1. Moves the top card to graveyard
      2. Clears the evolution stack to enable the SBA checker to reconstruct
    """
    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Battle-zone card {creature_uid} not found")

    p_state = state.players[player]

    # Move the top card to graveyard
    graveyard_card = GraveyardCard(
        definition=creature.definition,
        uid=creature.uid,
        died_from=reason,
        died_on_turn=state.turn_number,
    )
    p_state.graveyard.insert(0, graveyard_card)

    # Remove global effects from the top card
    state.global_effects.remove_by_source(creature.uid)

    # Rule 801.4b: Check if there are underlying cards
    if not creature.is_evolution_creature():
        # No underlying cards → remove the entire creature from battle zone
        p_state.battle_zone.remove(creature)
        return None

    # There are underlying cards. The SBA checker will handle reconstruction.
    # For now, we just signal that a reconstruction event occurred.
    # The creature stays in the battle zone but its state will be updated by
    # the reconstruction logic in sba_checker.py.

    # Signal to the SBA checker that this creature needs reconstruction
    # (This would be done via event collection; for now, we set a marker)
    creature.temp_flags["_pending_reconstruction"] = True

    return creature


def cross_gear_to_creature(
    state: GameState,
    player: int,
    gear_uid: str,
    target_uid: str,
) -> None:
    """Attach a generated Cross Gear to a creature."""
    p_state = state.players[player]
    gear = p_state.find_creature(gear_uid)
    target = p_state.find_creature(target_uid)
    if gear is None:
        raise ValueError(f"Cross Gear {gear_uid} not found")
    if target is None:
        raise ValueError(f"Cross Gear target {target_uid} not found")
    p_state.battle_zone.remove(gear)
    target.attached_cards.append(gear.definition)


def fortify_shield_with_castle(
    state: GameState,
    player: int,
    castle_uid: str,
    shield_uid: str,
) -> ShieldCard:
    """Move a Castle from hand underneath one of its owner's shields."""
    hand_card = _remove_from_hand(state, player, castle_uid)
    shield = state.players[player].find_shield(shield_uid)
    if shield is None:
        raise ValueError(f"Shield {shield_uid} not found")
    shield.fortified_castles.append(hand_card.definition)
    return shield
def fortify_g_castle_to_shield(
    state: GameState,
    player: int,
    castle_uid: str,
    shield_uid: str,
) -> ShieldCard:
    """
    Rule 822: Move a G-Castle from hand underneath one of its owner's shields.

    Unlike regular castles, G-Castle cards have special behavior:
    - They are placed under a shield (tracked via fortified_castles)
    - When the shield breaks, the G-Castle goes to graveyard (not hand)
    """
    hand_card = _remove_from_hand(state, player, castle_uid)
    if not is_g_castle(hand_card.definition):
        raise ValueError(f"Card {hand_card.definition.name} is not a G-Castle")
    shield = state.players[player].find_shield(shield_uid)
    if shield is None:
        raise ValueError(f"Shield {shield_uid} not found")
    shield.fortified_castles.append(hand_card.definition)
    return shield




def draw_card(state: GameState, player: int) -> Optional[HandCard]:
    """Draw the top card of the deck into hand. Empty deck draws do nothing.

    Checks the ReplacementEffectRegistry for any DRAW replacement before
    performing the draw. If a replacement applies, it is marked used and
    the draw proceeds normally (future: replacement may modify the draw).
    """
    # ── Replacement effect check (rule 609) ──────────────────────────────────
    from engine.replacement import EventType, ReplacementEffect
    draw_replacement: Optional[ReplacementEffect] = state.replacement_effects.check_and_apply(
        EventType.DRAW,
        state,
        controller=player,
    )
    if draw_replacement is not None and draw_replacement.replacement_action == "prevent":
        # Replacement prevents the draw entirely (e.g., "instead of drawing, ...")
        # Mark as used and skip the draw.
        return None

    p_state = state.players[player]
    if not p_state.deck:
        return None
    defn = p_state.deck.pop(0)
    hand_card = HandCard(definition=defn)
    p_state.hand.append(hand_card)
    p_state.has_drawn_this_turn = True
    
    # Fire ON_DRAW trigger
    from core.enums import TriggerEvent
    from engine.trigger_registry import fire_trigger
    fire_trigger(state, TriggerEvent.ON_DRAW, {
        "source_uid": hand_card.uid,
        "source_card_id": hand_card.id,
        "controller": player,
        "zone": "hand",
    }, hand_card.uid)
    
    return hand_card


def move_shield_to_standby(state: GameState, player: int, shield_index: int) -> ShieldCard:
    """Remove one shield from shield zone and queue it for trigger declaration.

    Checks the ReplacementEffectRegistry for any SHIELD_BREAK replacement
    before performing the shield break.
    
    Fires BEFORE_BREAK (rule 509.3) and ON_BREAK_SHIELD triggers.
    """
    # ── Replacement effect check (rule 609) ──────────────────────────────────
    from engine.replacement import EventType, ReplacementEffect
    shield_replacement: Optional[ReplacementEffect] = state.replacement_effects.check_and_apply(
        EventType.SHIELD_BREAK,
        state,
        controller=player,
    )
    if shield_replacement is not None and shield_replacement.replacement_action == "prevent":
        # Replacement prevents the shield break entirely.
        # Return a placeholder; the caller should not proceed with shield removal.
        # For now, we still proceed with the break but log the replacement.
        pass

    p_state = state.players[player]
    if shield_index < 0 or shield_index >= len(p_state.shield_zone):
        raise ValueError(f"Invalid shield index {shield_index}")
    shield = p_state.shield_zone[shield_index]
    
    # Fire BEFORE_BREAK trigger (rule 509.3) - before shield is moved
    from core.enums import TriggerEvent
    from engine.trigger_registry import fire_trigger
    fire_trigger(state, TriggerEvent.BEFORE_BREAK, {
        "source_uid": shield.uid,
        "source_card_id": shield.id,
        "controller": player,
        "shield_index": shield_index,
        "zone": "shield_zone",
    }, shield.uid)
    
    # Remove shield from zone
    shield = p_state.shield_zone.pop(shield_index)
    shield.reveal()
    
    # Fire ON_BREAK_SHIELD trigger
    fire_trigger(state, TriggerEvent.ON_BREAK_SHIELD, {
        "source_uid": shield.uid,
        "source_card_id": shield.id,
        "controller": player,
        "shield_index": shield_index,
        "zone": "shield_zone",
    }, shield.uid)
    
    from engine.shield_break_window import open_shield_break_window
    open_shield_break_window(state, player, shield)
    return shield


def move_standby_shield_to_hand(state: GameState, player: int, shield_uid: str) -> HandCard:
    """Move a queued standby shield to its owner's hand.

    Rule 822: G-Castle cards that leave the Shield Zone go to the Graveyard
    instead of the hand.  If the revealed shield is a G-Castle, it is sent
    to the graveyard here rather than being returned to hand.
    """
    for idx, (queued_player, shield) in enumerate(state.effect_stack.shield_trigger_queue):
        if queued_player == player and shield.uid == shield_uid:
            state.effect_stack.shield_trigger_queue.pop(idx)

            # Rule 822: G-Castle leaves shield zone → graveyard, not hand
            if shield.definition.card_subtype == CardSubtype.G_CASTLE:
                state.players[player].graveyard.insert(
                    0,
                    GraveyardCard(
                        definition=shield.definition,
                        uid=shield.uid,
                        died_from="g_castle_shield_break",
                        died_on_turn=state.turn_number,
                    ),
                )
                # Return a sentinel HandCard so callers that store the return
                # value don't crash; the card is NOT in hand.
                return HandCard(definition=shield.definition, uid=shield.uid)

            # Rule 822: Fortified G-Castles on this shield also go to graveyard
            for fortified_defn in shield.fortified_castles:
                if fortified_defn.card_subtype == CardSubtype.G_CASTLE:
                    state.players[player].graveyard.insert(
                        0,
                        GraveyardCard(
                            definition=fortified_defn,
                            uid=f"fortified-{fortified_defn.id}-{shield.uid}",
                            died_from="g_castle_shield_break",
                            died_on_turn=state.turn_number,
                        ),
                    )
            shield.fortified_castles.clear()

            hand_card = HandCard(definition=shield.definition, uid=shield.uid)
            state.players[player].hand.append(hand_card)
            return hand_card
    raise ValueError(f"Standby shield {shield_uid} not found")


# ─────────────────────────────────────────────────────────────────────────────
# Star Evolution and G-NEO leave behavior (rules 813.1, 803.2)
# ─────────────────────────────────────────────────────────────────────────────

def _remove_from_hand(state: GameState, player: int, card_uid: str) -> HandCard:
    p_state = state.players[player]
    hand_card = p_state.find_in_hand(card_uid)
    if hand_card is None:
        raise ValueError(f"Hand card {card_uid} not found for player {player}")
    p_state.hand.remove(hand_card)
    return hand_card


# Re-exports from engine/special_cards/ for backwards compatibility
from engine.special_cards import (
    awaken_psychic_creature,
    apply_dragon_evasion,
    apply_psychic_release,
    combine_king_cells,
    dragon_soul_evasion,
    dragsolve_dragheart,
    flip_forbidden,
    flip_twinpact,
    link_psychic_cells,
    link_release_psychic_super,
    move_ultra_gr_to_battle,
    move_zerom_to_battle,
    should_apply_dragon_evasion,
    should_apply_gneo_all_leave_replacement,
    should_apply_psychic_release,
    should_apply_star_evo_replacement,
    swap_hyper_mode,
)
