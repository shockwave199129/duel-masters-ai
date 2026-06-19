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
from core.cards import is_twinpact, is_forbidden, get_other_face, is_hyper_mode


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
    return creature


def move_zerom_to_battle(
    state: GameState,
    player: int,
    card_def: "CardDefinition",
) -> Creature:
    """
    Move a Zerom from its current zone to the battle zone as a flipped creature.
    (Rule 812)

    Creates a Creature from the card definition, sets the _zerom_flipped flag,
    adds it to the controller's battle zone, and applies static effects.
    """
    p_state = state.players[player]

    creature = Creature(
        definition=card_def,
        controller=player,
        owner=player,
        entered_turn=state.turn_number,
        has_summoning_sickness=True,
    )
    creature.temp_flags["_zerom_flipped"] = True
    p_state.battle_zone.append(creature)
    creature.apply_static_effects(state)
    return creature


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

    # ── Legacy hardcoded replacement checks (Psychic Release / Dragon Evasion) ──
    # These remain for backward compatibility with temp_flag-driven cards.
    # If the creature has such an ability, it flips to lower-cost face and stays in BZ
    # instead of leaving. The caller is responsible for providing the lower-face definition
    # via a temp_flag; here we just signal that the replacement was applied.
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
        move_battle_to_hyperspatial(state, player, creature_uid, reason=reason)
        # Return a minimal GraveyardCard as a non-None sentinel so callers
        # that store the return value don't crash; the card is NOT in GY.
        return GraveyardCard(
            definition=creature.definition,
            uid=creature.uid,
            died_from=f"{reason}_returned_to_hyperspatial",
            died_on_turn=state.turn_number,
        )

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

    # Now remove the creature from battle zone and move top card to graveyard
    creature.remove_static_effects(state)
    p_state.battle_zone.remove(creature)

    # Top card goes to graveyard as a creature (will fire creature-leave triggers)
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


def draw_card(state: GameState, player: int) -> Optional[HandCard]:
    """Draw the top card of the deck into hand. Empty deck draws do nothing.

    Checks the ReplacementEffectRegistry for any DRAW replacement before
    performing the draw. If a replacement applies, it is marked used and
    the draw proceeds normally (future: replacement may modify the draw).
    """
    # ── Replacement effect check (rule 609) ──────────────────────────────────
    from engine.replacement import EventType
    state.replacement_effects.check_and_apply(
        EventType.DRAW,
        state,
        controller=player,
    )

    p_state = state.players[player]
    if not p_state.deck:
        return None
    defn = p_state.deck.pop(0)
    hand_card = HandCard(definition=defn)
    p_state.hand.append(hand_card)
    p_state.has_drawn_this_turn = True
    return hand_card


def move_shield_to_standby(state: GameState, player: int, shield_index: int) -> ShieldCard:
    """Remove one shield from shield zone and queue it for trigger declaration.

    Checks the ReplacementEffectRegistry for any SHIELD_BREAK replacement
    before performing the shield break.
    """
    # ── Replacement effect check (rule 609) ──────────────────────────────────
    from engine.replacement import EventType
    state.replacement_effects.check_and_apply(
        EventType.SHIELD_BREAK,
        state,
        controller=player,
    )

    p_state = state.players[player]
    if shield_index < 0 or shield_index >= len(p_state.shield_zone):
        raise ValueError(f"Invalid shield index {shield_index}")
    shield = p_state.shield_zone.pop(shield_index)
    shield.reveal()
    state.effect_stack.add_shield_trigger(player, shield)
    return shield


def move_standby_shield_to_hand(state: GameState, player: int, shield_uid: str) -> HandCard:
    """Move a queued standby shield to its owner's hand."""
    for idx, (queued_player, shield) in enumerate(state.effect_stack.shield_trigger_queue):
        if queued_player == player and shield.uid == shield_uid:
            state.effect_stack.shield_trigger_queue.pop(idx)
            hand_card = HandCard(definition=shield.definition, uid=shield.uid)
            state.players[player].hand.append(hand_card)
            return hand_card
    raise ValueError(f"Standby shield {shield_uid} not found")


# ─────────────────────────────────────────────────────────────────────────────
# Star Evolution and G-NEO leave behavior (rules 813.1, 803.2)
# ─────────────────────────────────────────────────────────────────────────────

def should_apply_star_evo_replacement(creature: Creature) -> bool:
    """
    Check if a creature should apply the Star Evolution top-only leave replacement.
    
    Rule 813.1: A Star Evolution Creature is an Evolution Creature where, when
    leaving the Battle Zone, only the topmost card leaves instead.
    
    Rule 813.1a: The "when leaving" effect is a replacement effect. If another
    replacement effect was applied first, this cannot be applied.
    
    NOTE: This is a placeholder for the replacement effect check. The actual
    Star Evolution subtype detection depends on the card database having the
    "Star Evolution" designation. For now, we check for the _star_evo_replacement
    flag that would be set by the card parser or manually in tests.
    """
    # Must be an Evolution Creature
    if not creature.is_evolution_creature():
        return False
    
    # Check if this creature is flagged as Star Evolution (set by card parser or tests)
    # This will be replaced with proper subtype checking once the DB parser is updated
    if not creature.temp_flags.get("_is_star_evolution", False):
        return False
    
    # Check if another replacement effect already applied (rule 813.1a)
    if creature.temp_flags.get("_replacement_already_applied", False):
        return False
    
    return True


def should_apply_gneo_all_leave_replacement(creature: Creature) -> bool:
    """
    Check if a creature should apply the G-NEO all-leave replacement.
    
    Rule 803.2: For a G-NEO Creature, when it leaves the Battle Zone while it has
    a card placed underneath it and is treated as a G-NEO Evolution Creature, all
    the cards placed under the G-NEO Creature leave instead.
    
    Rule 803.2a: The "when leaving" effect is a replacement effect. If another
    replacement effect was applied first, this cannot be applied.
    """
    # Must have an underlying card (G-NEO Evolution state)
    if not creature.is_evolution_creature():
        return False
    
    # Must be a G-NEO card (check subtype)
    if creature.definition.card_subtype != CardSubtype.G_NEO:
        return False
    
    # Check if another replacement effect already applied (rule 803.2a)
    if creature.temp_flags.get("_replacement_already_applied", False):
        return False
    
    return True




# ─────────────────────────────────────────────────────────────────────────────
# Psychic / Dragheart Flip, Link, and Release helpers (rules 805–808)
# ─────────────────────────────────────────────────────────────────────────────

def awaken_psychic_creature(
    state: GameState,
    player: int,
    creature_uid: str,
    awakened_face_defn: "CardDefinition",
) -> "Creature":
    """
    Flip a Psychic Creature to its awakened face in the Battle Zone (rule 805.1a).

    Rule 805.5: The creature is treated as the same creature — uid, tapped state,
    entered_turn, power_modifiers, and applied effects are all preserved.
    Rule 805.6: The awakened creature does not suffer from summoning sickness.
    """
    from core.cards import CardDefinition  # local import to avoid circular deps
    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Creature {creature_uid} not found for awaken")
    # Flip to the awakened definition (rule 805.2: each face has independent characteristics)
    creature.definition = awakened_face_defn
    creature.face = 1
    # Rule 805.6: no summoning sickness after awakening
    creature.has_summoning_sickness = False
    # uid, is_tapped, entered_turn, power_modifiers preserved (rule 805.5)
    return creature


def dragsolve_dragheart(
    state: GameState,
    player: int,
    creature_uid: str,
    creature_face_defn: "CardDefinition",
) -> "Creature":
    """
    Flip a Dragheart Weapon or Fortress to its Creature face via Dragsolve (rule 807.1a).

    Rule 807.5a: It does not matter which face was up at the beginning of the turn.
    If the card existed in the Battle Zone as a Weapon at the start of the turn and
    then Dragsolves, the Dragheart Creature is treated as having been in the BZ since
    the start of the turn (entered_turn is preserved, so can_attack() naturally works).
    Rule 807.5: Dragheart Creatures DO suffer from summoning sickness if they entered
    the BZ this turn as a Weapon (entered_turn == current turn).
    """
    from core.cards import CardDefinition
    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Creature {creature_uid} not found for dragsolve")
    # Flip to creature face (rule 807.2: each face has independent characteristics)
    creature.definition = creature_face_defn
    creature.face = 1
    # entered_turn is NOT changed — rule 807.5a: orientation at BZ entry time determines sickness
    # has_summoning_sickness is not changed here — it reflects whether the card entered this turn
    return creature


def link_psychic_cells(
    state: GameState,
    player: int,
    cell_uids: list[str],
    super_creature_defn: "CardDefinition",
    *,
    primary_uid: Optional[str] = None,
) -> "Creature":
    """
    Perform Awakening Link — simultaneously flip and link multiple Psychic Creatures
    into a Psychic Super Creature (rule 805.1c).

    The combined creature uses the uid of the primary cell (or the first cell if
    primary_uid is None). All other cells are stored in linked_cells.

    Rule 806.1f: Each Psychic Cell possesses the civilizations of the Psychic Super Creature.
    Tapped state: the Super Creature is tapped if any constituent cell was tapped (rule 806.2).
    Power modifiers: the Super Creature inherits modifiers from the primary cell.
    """
    if not cell_uids:
        raise ValueError("link_psychic_cells requires at least one cell uid")

    p_state = state.players[player]

    # Collect all cell Creatures from the BZ
    cells: list[Creature] = []
    for uid in cell_uids:
        c = p_state.find_creature(uid)
        if c is None:
            raise ValueError(f"Psychic Cell {uid} not found in battle zone")
        cells.append(c)

    # Pick the primary cell (becomes the combined Creature object)
    if primary_uid is not None:
        primary = next((c for c in cells if c.uid == primary_uid), None)
        if primary is None:
            raise ValueError(f"primary_uid {primary_uid} not in cell_uids")
        others = [c for c in cells if c.uid != primary_uid]
    else:
        primary = cells[0]
        others = cells[1:]

    # Remove all non-primary cells from the battle zone
    for other in others:
        p_state.battle_zone.remove(other)
        state.global_effects.remove_by_source(other.uid)

    # Flip primary cell to the Super Creature definition
    primary.definition = super_creature_defn
    primary.face = 1

    # Rule 808.1a / link: Super Creature has no summoning sickness
    primary.has_summoning_sickness = False

    # Tapped if ANY cell was tapped (rule 806.2)
    primary.is_tapped = any(c.is_tapped for c in cells)

    # Store the constituent cells (mark them as cells of this super creature)
    for cell in cells:
        cell.is_psychic_cell = True
    primary.linked_cells = list(cells)  # includes primary itself

    return primary


def combine_king_cells(
    state: GameState,
    player: int,
    king_creature_defn: "CardDefinition",
    cell_uids: list[str],
) -> "Creature":
    """
    Combine King Cells from hand and/or mana zone into a King Creature (rule 814.1c).

    Payment must already be applied via tap_mana_for_payment before calling this.
    """
    if not cell_uids:
        raise ValueError("combine_king_cells requires at least one cell uid")

    p_state = state.players[player]
    cell_creatures: list[Creature] = []

    for uid in cell_uids:
        hand_card = p_state.find_in_hand(uid)
        if hand_card is not None:
            p_state.hand.remove(hand_card)
            cell = Creature(
                definition=hand_card.definition,
                uid=uid,
                controller=player,
                owner=player,
            )
            cell.is_king_cell = True
            cell_creatures.append(cell)
            continue

        mana_card = p_state.find_mana(uid)
        if mana_card is not None:
            p_state.mana_zone.remove(mana_card)
            cell = Creature(
                definition=mana_card.definition,
                uid=uid,
                controller=player,
                owner=player,
                is_tapped=mana_card.is_tapped,
            )
            cell.is_king_cell = True
            cell_creatures.append(cell)
            continue

        raise ValueError(f"King Cell {uid} not found in hand or mana zone")

    primary = cell_creatures[0]
    primary.definition = king_creature_defn
    primary.has_summoning_sickness = True
    primary.entered_turn = state.turn_number
    primary.is_tapped = any(c.is_tapped for c in cell_creatures)
    primary.linked_cells = list(cell_creatures)
    p_state.battle_zone.append(primary)
    return primary


def link_release_psychic_super(
    state: GameState,
    player: int,
    super_uid: str,
    returning_cell_idx: int = 0,
) -> list["Creature"]:
    """
    Perform Link Release — a Psychic Super Creature separates back into its component
    Psychic Creatures (rule 806.1b).

    One Psychic Cell (chosen by returning_cell_idx into linked_cells) is returned to the
    owner's Hyperspatial Zone. The remaining cells are placed in the Battle Zone face=0
    (lower-cost face), inheriting tapped state and power modifiers from the Super Creature
    (rule 806.2).

    Rule 806.1b: This is NOT a replacement effect.
    Rule 806.1e: Cell leaving the BZ is not treated as a creature leaving.
    Rule 806.2: Tapped state and applied effects are inherited by each separated creature.
    Rule 806.2a: If 3+ cells, the active player chooses one to continue any ongoing attack.
    """
    p_state = state.players[player]
    super_creature = p_state.find_creature(super_uid)
    if super_creature is None:
        raise ValueError(f"Psychic Super Creature {super_uid} not found")

    cells = list(super_creature.linked_cells)
    if not cells:
        raise ValueError(f"Super Creature {super_uid} has no linked_cells")

    if returning_cell_idx < 0 or returning_cell_idx >= len(cells):
        raise ValueError(f"returning_cell_idx {returning_cell_idx} out of range for {len(cells)} cells")

    returning_cell = cells[returning_cell_idx]
    remaining_cells = [c for i, c in enumerate(cells) if i != returning_cell_idx]

    # Inherit tapped state and power modifiers from the Super Creature (rule 806.2)
    was_tapped = super_creature.is_tapped
    inherited_mods = list(super_creature.power_modifiers)

    # Remove the Super Creature from the battle zone
    p_state.battle_zone.remove(super_creature)
    state.global_effects.remove_by_source(super_creature.uid)

    # Return one cell to Hyperspatial (rule 806.1b)
    returning_cell.face = 0
    returning_cell.is_tapped = False
    returning_cell.has_summoning_sickness = True
    returning_cell.entered_turn = 0
    returning_cell.power_modifiers.clear()
    returning_cell.temp_flags.clear()
    returning_cell.is_psychic_cell = False
    returning_cell.linked_cells.clear()
    owner = returning_cell.owner
    state.players[owner].hyperspatial_zone.append(creature_to_hyperspatial_card(returning_cell))

    # Place remaining cells back in the Battle Zone (flipped to face=0)
    surviving: list[Creature] = []
    for cell in remaining_cells:
        # Flip to lower-cost face (face=0), inherit super's tapped/effect state (rule 806.2)
        cell.face = 0
        cell.is_tapped = was_tapped
        cell.has_summoning_sickness = False  # already was in BZ this turn
        cell.is_psychic_cell = False
        cell.linked_cells.clear()
        # Inherit power modifiers from the Super Creature
        cell.power_modifiers = [m for m in inherited_mods]
        p_state.battle_zone.append(cell)
        surviving.append(cell)

    return surviving


def dragon_soul_evasion(
    state: GameState,
    player: int,
    super_uid: str,
    returning_cell_idx: int = 0,
) -> list["Creature"]:
    """
    Perform Dragon Soul Evasion — a Dragheart Super Creature that would leave the
    Battle Zone instead returns one Dragheart Cell to Hyperspatial and flips the
    remaining cells to their lower-cost face (rule 808.1b).

    Rule 808.1b: This IS a replacement effect.
    Rule 808.1d: Dragheart Cells leaving the BZ are NOT treated as creatures leaving.
    Replacement effects applied to the creature also apply when a Dragheart Cell leaves.
    """
    p_state = state.players[player]
    super_creature = p_state.find_creature(super_uid)
    if super_creature is None:
        raise ValueError(f"Dragheart Super Creature {super_uid} not found")

    cells = list(super_creature.linked_cells)
    if not cells:
        raise ValueError(f"Dragheart Super Creature {super_uid} has no linked_cells")

    if returning_cell_idx < 0 or returning_cell_idx >= len(cells):
        raise ValueError(f"returning_cell_idx {returning_cell_idx} out of range for {len(cells)} cells")

    returning_cell = cells[returning_cell_idx]
    remaining_cells = [c for i, c in enumerate(cells) if i != returning_cell_idx]

    # Mark as replacement applied (rule 808.1b: this is a replacement effect)
    super_creature.temp_flags["_replacement_already_applied"] = True

    was_tapped = super_creature.is_tapped
    inherited_mods = list(super_creature.power_modifiers)

    # Remove the Super Creature from the battle zone
    p_state.battle_zone.remove(super_creature)
    state.global_effects.remove_by_source(super_creature.uid)

    # Return chosen cell to Hyperspatial (rule 808.1b)
    returning_cell.face = 0
    returning_cell.is_tapped = False
    returning_cell.has_summoning_sickness = True
    returning_cell.entered_turn = 0
    returning_cell.power_modifiers.clear()
    returning_cell.temp_flags.clear()
    returning_cell.is_psychic_cell = False
    returning_cell.linked_cells.clear()
    owner = returning_cell.owner
    state.players[owner].hyperspatial_zone.append(creature_to_hyperspatial_card(returning_cell))

    # Flip remaining cells back to lower-cost face, inheriting state (rule 808.1b)
    surviving: list[Creature] = []
    for cell in remaining_cells:
        cell.face = 0
        cell.is_tapped = was_tapped
        # Dragheart Super Creatures have no sickness (rule 808.1a) so the surviving
        # cells were already able to act; preserve that (no sickness reset here)
        cell.has_summoning_sickness = False
        cell.is_psychic_cell = False
        cell.linked_cells.clear()
        cell.power_modifiers = [m for m in inherited_mods]
        p_state.battle_zone.append(cell)
        surviving.append(cell)

    return surviving


def should_apply_psychic_release(creature: "Creature") -> bool:
    """
    Check whether a Psychic Creature's Release replacement effect (rule 805.1b) should
    apply when it would leave the Battle Zone.

    Rule 805.1b: Some Psychic Creatures have a Release ability that flips them to the
    lower-cost face instead of leaving the Battle Zone. This is a replacement effect.

    The engine sets temp_flag "_has_psychic_release" (via effect parser or tests) to
    indicate the card has this ability. Only fires once per leave attempt (rule 805.1b:
    replacement effect — cannot stack with another replacement effect).
    """
    if creature.definition.card_subtype not in (CardSubtype.PSYCHIC, CardSubtype.PSYCHIC_SUPER):
        return False
    if not creature.temp_flags.get("_has_psychic_release", False):
        return False
    if creature.temp_flags.get("_replacement_already_applied", False):
        return False
    return True


def should_apply_dragon_evasion(creature: "Creature") -> bool:
    """
    Check whether a Dragheart Creature's Dragon Evasion replacement effect (rule 807.1b)
    should apply when it would leave the Battle Zone.

    Rule 807.1b: Some Dragheart Creatures have a Dragon Evasion ability that flips them
    to the lower-cost face instead of leaving the Battle Zone. This is a replacement effect.
    """
    if creature.definition.card_subtype not in (CardSubtype.DRAGHEART,):
        return False
    if not creature.temp_flags.get("_has_dragon_evasion", False):
        return False
    if creature.temp_flags.get("_replacement_already_applied", False):
        return False
    return True


def apply_psychic_release(
    state: GameState,
    player: int,
    creature_uid: str,
    lower_face_defn: "CardDefinition",
) -> "Creature":
    """
    Apply the Psychic Release replacement effect: flip to lower-cost face, stay in BZ.
    (Rule 805.1b)
    """
    from core.cards import CardDefinition
    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Creature {creature_uid} not found for psychic_release")
    creature.definition = lower_face_defn
    creature.face = 0
    creature.temp_flags["_replacement_already_applied"] = True
    return creature


def apply_dragon_evasion(
    state: GameState,
    player: int,
    creature_uid: str,
    lower_face_defn: "CardDefinition",
) -> "Creature":
    """
    Apply the Dragon Evasion replacement effect: flip to lower-cost face, stay in BZ.
    (Rule 807.1b)
    """
    from core.cards import CardDefinition
    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Creature {creature_uid} not found for dragon_evasion")
    creature.definition = lower_face_defn
    creature.face = 0
    creature.temp_flags["_replacement_already_applied"] = True
    return creature


# ─────────────────────────────────────────────────────────────────────────────
# Twinpact and Forbidden flip logic (Phase 5A)
# ─────────────────────────────────────────────────────────────────────────────

def flip_twinpact(creature: Creature, card_db=None) -> Creature:
    """
    Flip a Twinpact card to its other face when it enters the battle zone.

    If the creature is a multi-face card and has a valid other_face_id,
    this swaps to a new CardDefinition with the other_face_id as the card_id
    (simplified flip — full card_db resolution is a stub for later).
    Toggles the _twinpact_flipped temp flag.
    """
    if not is_twinpact(creature.definition):
        return creature
    if creature.definition.other_face_id is None:
        return creature

    # Simplified flip: create a new CardDefinition with the other_face_id
    # Full card_db resolution is deferred (see get_other_face stub)
    old_def = creature.definition
    from core.cards import CardDefinition as _CD
    new_def = _CD(
        id=old_def.other_face_id,
        slug=old_def.slug,
        name=old_def.name,
        cost=old_def.cost,
        power=old_def.power,
        card_type=old_def.card_type,
        card_subtype=old_def.card_subtype,
        civilizations=old_def.civilizations,
        races=old_def.races,
        keywords=old_def.keywords,
        effects=old_def.effects,
        evolution_source_races=old_def.evolution_source_races,
        evolution_source_types=old_def.evolution_source_types,
        is_multiface=old_def.is_multiface,
        other_face_id=old_def.id,  # point back to the original
    )
    creature.definition = new_def
    creature.temp_flags["_twinpact_flipped"] = not creature.temp_flags.get("_twinpact_flipped", False)
    return creature


def flip_forbidden(creature: Creature) -> Creature:
    """
    Flip a Forbidden or Final Forbidden card when it leaves the battle zone.

    Toggles the _forbidden_flipped temp flag and flips the face field (0→1 or 1→0).
    """
    if not is_forbidden(creature.definition):
        return creature
    creature.temp_flags["_forbidden_flipped"] = not creature.temp_flags.get("_forbidden_flipped", False)
    creature.face = 1 - creature.face
    return creature

def swap_hyper_mode(creature: Creature) -> Creature:
    """
    Swap a Hyper Mode creature to its released face (rule 816).

    If the creature has an other_face_id and is currently in the
    released state (hyper_mode_released=True), swap its definition
    to the other face. This changes the creature's abilities and
    potentially its power.

    Returns the modified creature (same object, mutated).
    """
    if not is_hyper_mode(creature.definition):
        return creature
    if creature.definition.other_face_id is None:
        return creature
    if creature.hyper_mode_released:
        # Already released — no swap needed
        return creature

    old_def = creature.definition
    from core.cards import CardDefinition as _CD
    new_def = _CD(
        id=old_def.other_face_id,
        slug=old_def.slug,
        name=old_def.name,
        cost=old_def.cost,
        power=old_def.power,
        card_type=old_def.card_type,
        card_subtype=old_def.card_subtype,
        civilizations=old_def.civilizations,
        races=old_def.races,
        keywords=old_def.keywords,
        effects=old_def.effects,
        evolution_source_races=old_def.evolution_source_races,
        evolution_source_types=old_def.evolution_source_types,
        is_multiface=old_def.is_multiface,
        other_face_id=old_def.id,  # point back to the original
    )
    creature.definition = new_def
    creature.hyper_mode_released = True
    return creature


def move_ultra_gr_to_battle(state: GameState, controller: int, creature_uid: str) -> "Creature":
    """
    Move an Ultra GR creature face-up into the battle zone (rule 408).
    Stub — full Ultra GR logic is in development.
    """
    p_state = state.players[controller]
    creature = p_state.find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Ultra GR creature {creature_uid} not found")
    creature.face = 1
    return creature


def _remove_from_hand(state: GameState, player: int, card_uid: str) -> HandCard:
    p_state = state.players[player]
    hand_card = p_state.find_in_hand(card_uid)
    if hand_card is None:
        raise ValueError(f"Hand card {card_uid} not found for player {player}")
    p_state.hand.remove(hand_card)
    return hand_card

def move_ultra_gr_to_battle(
    state: GameState,
    player: int,
    card_def: CardDefinition,
) -> Creature:
    """
    Summon a GR creature from the Ultra GR zone into the battle zone.

    Steps:
      1. Remove the CardDefinition from state.players[player].ultra_gr_zone
      2. Create a new Creature from the definition
      3. Add to battle_zone with has_summoning_sickness = True
      4. Apply static effects

    GR creatures have summoning sickness unless they have Speed Attacker.
    """
    p_state = state.players[player]

    # Remove from Ultra GR zone
    p_state.ultra_gr_zone = [c for c in p_state.ultra_gr_zone if c.id != card_def.id]

    # Create the creature
    creature = Creature(
        definition=card_def,
        uid=_new_uid(),
        controller=player,
        owner=player,
        entered_turn=state.turn_number,
        has_summoning_sickness=True,
    )
    p_state.battle_zone.append(creature)
    creature.apply_static_effects(state)
    return creature

