"""
engine/zone_mover.py — centralized card movement helpers.

These helpers mutate the GameState object they receive. Callers that expose a
public API should copy the state before using them, then return the copy.
"""

from __future__ import annotations

from typing import Optional

from core.enums import ManaUsage, CardSubtype, CardType, Keyword
from core.card_scripts import EX_LIFE_SHIELD_LEFT_KEY
from core.continuous_rules import has_abilities, queue_ability, sources_of
from core.state import GameState, PendingTrigger
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
    summoned: bool = True,
    paid_mana: bool = False,
    from_graveyard: bool = False,
) -> Creature:
    """
    Move a creature from hand to battle, or evolve an existing base.
    
    Rule 801.2: The creature remains the same object; only the top card changes.
    Rule 801.3: Evolution creatures do not suffer summoning sickness.
    """
    hand_card = _remove_from_hand(state, player, card_uid)
    p_state = state.players[player]

    from engine.entry_rules import entry_redirect
    redirect = entry_redirect(state, player, hand_card.definition, summoned=summoned)
    if redirect is not None:
        place_in_zone(state, player, redirect, "redirected")(hand_card.definition, hand_card.uid)
        return Creature(definition=hand_card.definition, uid=hand_card.uid,
                        controller=player, owner=player)  # never entered

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
        if base.definition.card_subtype in (CardSubtype.NEO, CardSubtype.G_NEO):
            base.temp_flags["neo_evolved_turn"] = state.turn_number

        # The same creature (rule 801.2), now showing the new card: its abilities
        # replace the old ones, and its "when you put this creature" abilities trigger
        base.remove_static_effects(state)
        base.apply_static_effects(state)
        _fire_enter_battle_zone_trigger(state, base, player, summoned=summoned)
        from engine.entry_rules import after_enter
        after_enter(state, base, player, summoned=summoned, paid=paid_mana,
                    from_graveyard=from_graveyard)
        return base

    creature = Creature(
        definition=hand_card.definition,
        uid=hand_card.uid,
        controller=player,
        owner=player,
        entered_turn=state.turn_number,
        has_summoning_sickness=hand_card.definition.card_subtype != CardSubtype.STAR_MAX,
    )
    p_state.battle_zone.append(creature)
    creature.apply_static_effects(state)
    # Phase 5A: Twinpact flip on summon
    flip_twinpact(creature)
    
    # Fire ON_SUMMON and ON_ENTER_BATTLE_BATTLE_ZONE triggers
    _fire_enter_battle_zone_trigger(state, creature, player, summoned=summoned)
    from engine.entry_rules import after_enter
    after_enter(state, creature, player, summoned=summoned, paid=paid_mana,
                from_graveyard=from_graveyard)

    return creature


def move_hand_to_field(
    state: GameState,
    player: int,
    card_uid: str,
    *,
    target_player: int | None = None,
) -> Creature:
    """
    Deploy a Field from hand into the battle zone (rules 308.2, 701.27).

    Fields enter the battle zone upright, without summoning sickness, and are
    flagged just_entered for D2 Field SBA (703.4l).
    """
    hand_card = _remove_from_hand(state, player, card_uid)
    deploy_player = target_player if target_player is not None else player
    p_state = state.players[deploy_player]

    field_creature = Creature(
        definition=hand_card.definition,
        uid=hand_card.uid,
        controller=deploy_player,
        owner=player,
        entered_turn=state.turn_number,
        has_summoning_sickness=False,
        field_orientation="upright",
    )
    field_creature.set_flag("just_entered", True)
    p_state.battle_zone.append(field_creature)
    field_creature.apply_static_effects(state)
    return field_creature


def _fire_enter_battle_zone_trigger(
    state: GameState, creature, player: int, *, summoned: bool = True,
) -> None:
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
    
    # "When you summon" is only for creatures that were summoned, not just put
    if summoned:
        fire_trigger(state, TriggerEvent.ON_SUMMON, trigger_data, creature.uid)


def _fire_leave_battle_zone_triggers(
    state: GameState,
    creature,
    player: int,
    reason: str,
    to_zone: str = "graveyard",
) -> None:
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
        "to_zone": to_zone if reason != "returned_to_hyperspatial" else "hyperspatial_zone",
    }
    
    destroyed = (trigger_data["to_zone"] == "graveyard"
                 and reason in ("destroyed", "battle", "sacrificed", "effect"))
    burst = has_abilities(creature) and (
        (destroyed and creature.has_keyword(Keyword.LAST_BURST))
        or (trigger_data["to_zone"] in ("hand", "mana_zone", "graveyard")
            and creature.has_keyword(Keyword.MEGA_LAST_BURST))
    )
    if burst:  # resolves once the card has arrived where it is going
        from engine.shield_break_window import rules_trigger
        state.effect_stack.add_trigger(rules_trigger("last_burst", creature.uid, creature.id, player))

    # Fire ON_DESTROY (for destruction)
    if reason in ("destroyed", "battle", "sacrificed", "effect"):
        fire_trigger(state, TriggerEvent.ON_DESTROY, trigger_data, creature.uid)
    
    # Fire ON_LEAVE_BATTLE_ZONE (always when leaving BZ)
    fire_trigger(state, TriggerEvent.ON_LEAVE_BATTLE_ZONE, trigger_data, creature.uid)


_HYPERSPATIAL_SUBTYPES = (CardSubtype.PSYCHIC, CardSubtype.PSYCHIC_SUPER, CardSubtype.DRAGHEART)


def release_attached_cards(
    state: GameState,
    player: int,
    creature: Creature,
    *,
    reason: str,
    place_under=None,
) -> None:
    """
    Release every card that was under or attached to a creature that is leaving.

    - Cards underneath follow the creature, because they are part of the same
      element (rule 316.3). ``place_under(definition, uid)`` says where; the
      default is the graveyard. Psychic and Dragheart cards return to the
      Hyperspatial Zone instead (rules 805.4b, 807.4b).
    - Cross Gear stays in the Battle Zone (rule 303.5).
    - Other attached cards and seals go to the graveyard.
    """
    p_state = state.players[player]

    def to_graveyard(definition, uid=None):
        p_state.graveyard.insert(0, GraveyardCard(
            definition=definition,
            uid=uid or _new_uid(),
            died_from=reason,
            died_on_turn=state.turn_number,
        ))

    under, attached, seals = list(creature.evolution_stack), list(creature.attached_cards), list(creature.seals)
    creature.evolution_stack.clear()
    creature.attached_cards.clear()
    creature.seals.clear()

    for entry in under:
        if entry.definition.card_subtype in _HYPERSPATIAL_SUBTYPES:
            p_state.hyperspatial_zone.append(
                HyperspatialCard(definition=entry.definition, face=0, uid=entry.uid)
            )
        else:
            (place_under or to_graveyard)(entry.definition, entry.uid)
    for definition in attached:
        if definition.card_type == CardType.CROSS_GEAR:
            # Static effects were registered when it was generated and never removed
            p_state.battle_zone.append(Creature(
                definition=definition,
                controller=player,
                owner=player,
                entered_turn=state.turn_number,
                has_summoning_sickness=False,
            ))
        else:
            to_graveyard(definition)
    for definition in seals:
        to_graveyard(definition)


def spend_ex_life(state: GameState, player: int, creature: Creature) -> bool:
    """
    EX Life: when the creature would leave, put its EX Life shield into the
    graveyard instead. Returns True if the creature stays.
    """
    shield_uid = creature.temp_flags.get("ex_life_shield")
    if creature.temp_flags.get("abilities_ignored"):
        return False
    p_state = state.players[player]
    shield = next((s for s in p_state.shield_zone if s.uid == shield_uid), None)
    if shield is None:
        return False
    p_state.shield_zone.remove(shield)
    p_state.graveyard.insert(0, GraveyardCard(
        definition=shield.definition, uid=shield.uid,
        died_from="ex_life", died_on_turn=state.turn_number,
    ))
    ex_life_shield_left(state, player, creature)
    return True


def ex_life_shield_left(state: GameState, player: int, creature: Creature) -> bool:
    """
    The creature's EX Life shield has left the shield zone: queue any ability
    that watches for it. The flag is cleared, so it happens only once.
    """
    if creature.temp_flags.pop("ex_life_shield", None) is None:
        return False
    if creature.temp_flags.get("abilities_ignored"):
        return True
    for effect in creature.definition.effects:
        if effect.effect_value.get("script", "").endswith(f"#{EX_LIFE_SHIELD_LEFT_KEY}"):
            state.effect_stack.add_trigger(PendingTrigger(
                effect=effect, source_uid=creature.uid,
                source_card_id=creature.id, controller=player,
            ))
    return True


def gneo_leave(state: GameState, player: int, creature: Creature, *, reason: str, place) -> bool:
    """
    G-NEO (rule 803.2): when it would leave while it has cards underneath, those
    cards leave instead. Returns True if the creature stays.

    Another replacement applied first takes its place (rule 803.2a), so callers
    try those before this.
    """
    if not should_apply_gneo_all_leave_replacement(creature) or not has_abilities(creature):
        return False
    under = list(creature.evolution_stack)
    creature.evolution_stack.clear()
    for entry in under:  # they leave as cards, not creatures (rule 109.2b)
        if entry.definition.card_subtype in _HYPERSPATIAL_SUBTYPES:
            state.players[player].hyperspatial_zone.append(
                HyperspatialCard(definition=entry.definition, face=0, uid=entry.uid))
        else:
            place(entry.definition, entry.uid)
        queue_ability(state, creature.definition, "left_under", source_uid=creature.uid,
                      controller=player, data={})
    lost_cards_underneath(state, creature)
    return True


def lost_cards_underneath(state: GameState, creature: Creature) -> None:
    """Rule 802.3: no longer an evolution on the turn it evolved, so it is sick."""
    if not creature.evolution_stack and creature.temp_flags.get("neo_evolved_turn") == state.turn_number:
        creature.has_summoning_sickness = True


def place_in_zone(state: GameState, player: int, to_zone: str, reason: str):
    """How one card is put into a zone when it leaves the battle zone."""
    p_state = state.players[player]

    def place(definition, uid):
        if to_zone == "hand":
            p_state.hand.append(HandCard(definition=definition, uid=uid))
        elif to_zone == "mana_zone":
            p_state.mana_zone.append(ManaCard(
                definition=definition, uid=uid, is_tapped=len(definition.civilizations) > 1))
        elif to_zone == "shield_zone":
            p_state.shield_zone.append(ShieldCard(definition=definition, uid=uid))
        elif to_zone == "deck":
            p_state.deck.append(definition)  # the bottom
        elif to_zone == "abyss_zone":
            p_state.abyss_zone.append(definition)
        else:
            p_state.graveyard.insert(0, GraveyardCard(
                definition=definition, uid=uid, died_from=reason,
                died_on_turn=state.turn_number))

    return place


def leave_battle_zone(
    state: GameState, player: int, creature: Creature, *, reason: str, to_zone: str, place=None,
) -> None:
    """The creature leaves. Replacements have already had their chance."""
    creature.temp_flags.pop("leaving", None)
    place = place or place_in_zone(state, player, to_zone, reason)
    _fire_leave_battle_zone_triggers(state, creature, player, reason, to_zone)
    if creature.definition.card_subtype in _HYPERSPATIAL_SUBTYPES:
        move_battle_to_hyperspatial(state, player, creature.uid, reason=reason)
        return
    creature.remove_static_effects(state)
    state.players[player].battle_zone.remove(creature)
    place(creature.definition, creature.uid)
    release_attached_cards(state, player, creature, reason=reason, place_under=place)


def move_battle_to_zone(
    state: GameState,
    player: int,
    creature_uid: str,
    *,
    reason: str,
    to_zone: str,
    place=None,
) -> bool:
    """
    Move a battle-zone card somewhere other than by destroying it.

    Returns True if it has left. False means a replacement kept it, or its
    controller still has to choose one. A move that waits for that choice goes to
    the standard place in ``to_zone``.
    """
    creature = state.players[player].find_creature(creature_uid)
    if creature is None:
        raise ValueError(f"Battle-zone card {creature_uid} not found")
    flip_forbidden(creature)

    from engine.replacement import EventType
    if state.replacement_effects.check_and_apply(
        EventType.LEAVE_BATTLE_ZONE, state, target_uid=creature_uid, controller=player,
    ) is not None:
        return False
    from engine.leave_rules import intercept_leave
    if intercept_leave(state, player, creature, reason=reason, to_zone=to_zone):
        return False
    leave_battle_zone(state, player, creature, reason=reason, to_zone=to_zone, place=place)
    return True


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
    if reason not in ("cast", "s_back") and hand_card.definition.is_spell():
        # "Whenever your opponent discards a spell"
        for watcher in sources_of(state, "opponent_discarded_spell"):
            if watcher.controller != player:
                queue_ability(
                    state, watcher.definition, "opponent_discarded_spell",
                    source_uid=watcher.uid, controller=watcher.controller,
                    data={"card": hand_card.uid, "owner": player})
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
    release_attached_cards(state, player, creature, reason=reason)

    # Reset in-play state before returning to hyperspatial
    creature.is_tapped = False
    creature.has_summoning_sickness = True
    creature.entered_turn = 0
    creature.face = 0
    creature.power_modifiers.clear()
    creature.temp_flags.clear()
    creature.has_attacked_this_turn = False
    creature.is_blocking = False
    creature.blocking_uid = None
    creature.hyper_mode_released = False
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
    replacements_used: tuple[str, ...] = (),
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

    # EX Life, G-NEO, Saver and every other "instead" (rules 101.5, 609)
    from engine.leave_rules import intercept_leave
    if intercept_leave(state, player, creature, reason=reason, to_zone="graveyard",
                       used=replacements_used):
        return GraveyardCard(
            definition=creature.definition,
            uid=creature.uid,
            died_from=f"{reason}_replaced",
            died_on_turn=state.turn_number,
        )

    # ── Legacy hardcoded replacement checks (Psychic Release / Dragon Evasion) ──
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

    # Rule 804.7: linked God — only the anchor card leaves; others detach in BZ
    from engine.god_manager import GodManager
    if GodManager.is_god_link(creature):
        return GodManager.move_linked_god_to_graveyard(
            state, player, creature, reason=reason,
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
    release_attached_cards(state, player, creature, reason=reason)
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

    # Fire leave triggers for the top card ONLY (per rule 109.2b)
    _fire_leave_battle_zone_triggers(state, creature, player, reason)
    
    # Now remove the creature from battle zone and move top card to graveyard
    creature.remove_static_effects(state)
    p_state.battle_zone.remove(creature)
    # Underlying cards leave as cards, not creatures (rule 109.2b)
    release_attached_cards(state, player, creature, reason=reason)

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
    p_state.note_deck()
    hand_card = HandCard(definition=defn)
    p_state.hand.append(hand_card)
    first_on_own_turn = player == state.active_player and not p_state.has_drawn_this_turn
    p_state.has_drawn_this_turn = True
    if first_on_own_turn:
        for field_card in sources_of(state, "denjara_switch"):
            if field_card.field_orientation == "upright":
                queue_ability(
                    state, field_card.definition, "denjara_switch", source_uid=field_card.uid,
                    controller=field_card.controller, data={})
    
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
    p_state.shields_broken_this_turn += 1
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
