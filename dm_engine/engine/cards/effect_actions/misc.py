"""engine/cards/effect_actions/misc.py — Miscellaneous and control effects."""
from __future__ import annotations

import random

from core.state import GameState, PendingTrigger
from core.cards import CardDefinition
from core.zones import Creature, HandCard, ManaCard, ShieldCard
from engine.zone_mover import (
    flip_forbidden,
    flip_twinpact,
    move_battle_to_hyperspatial,
)

# ── Shared helpers ──────────────────────────────────────────────────────────

def _effect_value(trigger: PendingTrigger) -> dict:
    return dict(trigger.effect.effect_value)



def _find_creature(state: GameState, uid: str) -> tuple[int, Creature] | None:
    if not uid:
        return None
    return state.find_creature_anywhere(uid)



def _trigger_data(trigger: PendingTrigger) -> dict:
    return dict(trigger.trigger_data)



# ── Effect action implementations ──────────────────────────────────────────

def _do_protection(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle PROTECTION effect: creature gains protection from specific civilizations or races.
    
    Effect value can contain:
    - protect_from_civ: Civilization name or list (e.g., "fire", ["fire", "light"])
    - protect_from_race: Race name or list (e.g., "Dragon", ["Dragon", "Armored Dragon"])
    - duration: "until_end_of_turn" (default) or "while_in_play"
    """
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    
    protect_from_civ = effect.get("protect_from_civ") or effect.get("protect_from")
    protect_from_race = effect.get("protect_from_race")
    duration = effect.get("duration", "until_end_of_turn")
    
    # Store protection info in temp_flags
    protection_data = {
        "from_civ": protect_from_civ if isinstance(protect_from_civ, list) else [protect_from_civ] if protect_from_civ else [],
        "from_race": protect_from_race if isinstance(protect_from_race, list) else [protect_from_race] if protect_from_race else [],
        "duration": duration,
    }
    creature.temp_flags["protection"] = protection_data



def _do_gain_control(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle GAIN_CONTROL effect: take control of opponent's creature.
    
    Effect value:
    - target_uid: UID of creature to gain control of
    - duration: "until_end_of_turn" (default) or "permanent"
    """
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    target_uid = data.get("target_uid") or effect.get("target_uid")
    if not target_uid:
        return
    
    # Find the creature (could be anywhere)
    found = _find_creature(state, target_uid)
    if not found:
        return
    player_idx, creature = found
    
    # Don't allow gaining control of your own creature
    if player_idx == controller:
        return
    
    duration = effect.get("duration", "until_end_of_turn")
    
    # Remove from opponent's battle zone
    opponent_state = state.players[player_idx]
    opponent_state.battle_zone = [c for c in opponent_state.battle_zone if c.uid != target_uid]
    
    # Add to controller's battle zone
    creature.controller = controller
    creature.temp_flags["gained_control"] = {
        "original_controller": player_idx,
        "duration": duration,
    }
    creature.entered_turn = state.turn_number
    creature.has_summoning_sickness = True  # New controller gets summoning sickness
    
    state.players[controller].battle_zone.append(creature)
    
    # Remove static effects from old controller's perspective
    creature.remove_static_effects(state)
    # Apply static effects for new controller
    creature.apply_static_effects(state)



def _do_swap_zones(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle SWAP_ZONES effect: swap cards between zones (Revolution Change).

    Effect value:
    - from_zone_a: first zone (e.g., "hand", "battle_zone")
    - from_zone_b: second zone
    - card_uid_a: UID of card in first zone
    - card_uid_b: UID of card in second zone (optional, if swapping specific cards)
    - target_player: player whose zones to swap (default: controller)
    """
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    from_zone_a = effect.get("from_zone_a")
    from_zone_b = effect.get("from_zone_b")
    card_uid_a = data.get("card_uid_a") or effect.get("card_uid_a")
    card_uid_b = data.get("card_uid_b") or effect.get("card_uid_b")
    target_player = effect.get("target_player", controller)

    if not from_zone_a or not from_zone_b or not card_uid_a:
        return

    p_state = state.players[target_player]

    # Get the two cards
    zone_a = getattr(p_state, from_zone_a, None)
    zone_b = getattr(p_state, from_zone_b, None)
    if zone_a is None or zone_b is None:
        return

    card_a = None
    for c in zone_a:
        if getattr(c, "uid", None) == card_uid_a:
            card_a = c
            break
    if not card_a:
        return

    card_b = None
    if card_uid_b:
        for c in zone_b:
            if getattr(c, "uid", None) == card_uid_b:
                card_b = c
                break
    else:
        # If no specific card_b, pick first valid card in zone_b
        if zone_b:
            card_b = zone_b[0]

    if not card_b:
        return

    # Perform the swap
    zone_a.remove(card_a)
    zone_b.remove(card_b)

    # Add to opposite zones
    if from_zone_b == "battle_zone" and isinstance(card_b, Creature):
        state.global_effects.remove_by_source(card_b.uid)
    if from_zone_a == "battle_zone" and isinstance(card_a, Creature):
        state.global_effects.remove_by_source(card_a.uid)

    # Convert to appropriate zone types
    def _add_to_zone(state, player_idx, zone_name, card_def, uid):
        p = state.players[player_idx]
        if zone_name == "hand":
            p.hand.append(HandCard(definition=card_def, uid=uid))
        elif zone_name == "battle_zone":
            if isinstance(card_def, CardDefinition):
                p.battle_zone.append(
                    Creature(
                        definition=card_def,
                        uid=uid,
                        controller=player_idx,
                        owner=player_idx,
                        entered_turn=state.turn_number,
                        has_summoning_sickness=True,
                    )
                )
        elif zone_name == "mana_zone":
            p.mana_zone.append(ManaCard.from_charge(card_def))
        elif zone_name == "shield_zone":
            p.shield_zone.append(ShieldCard(definition=card_def, uid=uid))
        elif zone_name == "graveyard":
            p.graveyard.insert(0, card_def)

    _add_to_zone(state, target_player, from_zone_b, card_a.definition, card_a.uid)
    _add_to_zone(state, target_player, from_zone_a, card_b.definition, card_b.uid)



def _do_turn_upside_down(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle TURN_UPSIDE_DOWN effect: flip a Field card upside down (Rule 701.28).

    Effect value:
    - field_uid: UID of the Field card in the field zone
    """
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    field_uid = data.get("field_uid") or effect.get("field_uid")
    if not field_uid:
        return

    p_state = state.players[controller]
    for idx, field_def in enumerate(p_state.field_zone):
        if getattr(field_def, "uid", None) == field_uid or getattr(field_def, "id", None) == field_uid:
            # Flip the field - replace with its flipped face if available
            if hasattr(field_def, "flipped_definition") and field_def.flipped_definition:
                p_state.field_zone[idx] = field_def.flipped_definition
            else:
                # Mark as flipped for visual/logic purposes
                field_def.flipped = not getattr(field_def, "flipped", False)
            break



def _do_shieldify(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle SHIELDIFY effect: turn a card into a face-down shield.
    
    Effect value:
    - from_zone: "hand" (default) or "deck"
    - card_uid: specific card to shieldify (optional, otherwise random)
    - target_player: 0 or 1 (who gets the shield, default: controller)
    """
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    from_zone = effect.get("from_zone", "hand")
    target_player = effect.get("target_player", controller)
    card_uid = data.get("card_uid") or effect.get("card_uid")
    
    p_state = state.players[controller]
    target_p_state = state.players[target_player]
    
    if from_zone == "hand":
        if card_uid:
            # Find specific card
            hand_card = p_state.find_in_hand(card_uid)
            if not hand_card:
                return
            p_state.hand.remove(hand_card)
            target_p_state.shield_zone.append(ShieldCard(definition=hand_card.definition, uid=hand_card.uid))
        else:
            # Random card from hand
            if not p_state.hand:
                return
            hand_card = random.choice(p_state.hand)
            p_state.hand.remove(hand_card)
            target_p_state.shield_zone.append(ShieldCard(definition=hand_card.definition, uid=hand_card.uid))
    
    elif from_zone == "deck":
        deck = p_state.deck
        if not deck:
            return
        if card_uid:
            # Find specific card in deck
            for i, defn in enumerate(deck):
                if defn.id == card_uid or (hasattr(defn, 'uid') and defn.uid == card_uid):
                    definition = deck.pop(i)
                    target_p_state.shield_zone.append(ShieldCard(definition=definition))
                    break
        else:
            # Top card of deck
            definition = deck.pop(0)
            target_p_state.shield_zone.append(ShieldCard(definition=definition))



def _store_temp_value(state: GameState, trigger: PendingTrigger, key: str) -> None:
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    creature.temp_flags[key] = True



def _do_twinpact_flip(state: GameState, trigger: PendingTrigger) -> None:
    """
    Handle TWINPACT_FLIP effect: flip a Twinpact creature to its other face.

    The trigger data should contain the target creature's uid.
    """
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid") or data.get("creature_uid")
    if not target_uid:
        return
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    flip_twinpact(creature)



def _do_forbidden_flip(state: GameState, trigger: PendingTrigger) -> None:
    """
    Handle FORBIDDEN_FLIP effect: flip a Forbidden card's face when leaving battle zone.

    The trigger data should contain the target creature's uid.
    """
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid") or data.get("creature_uid")
    if not target_uid:
        return
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    flip_forbidden(creature)



def _do_dragon_soul_evasion(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    found = _find_creature(state, data.get("target_uid") or data.get("creature_uid"))
    if not found:
        return
    player_idx, creature = found
    move_battle_to_hyperspatial(state, player_idx, creature.uid, reason="dragon_soul_evasion")



def _do_dragon_evasion(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    found = _find_creature(state, data.get("target_uid") or data.get("creature_uid"))
    if not found:
        return
    player_idx, creature = found
    move_battle_to_hyperspatial(state, player_idx, creature.uid, reason="dragon_evasion")



def _do_psychic_release(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    found = _find_creature(state, data.get("target_uid") or data.get("creature_uid"))
    if not found:
        return
    player_idx, creature = found
    move_battle_to_hyperspatial(state, player_idx, creature.uid, reason="psychic_release")



def _set_creature_flag(state: GameState, trigger: PendingTrigger, flag: str) -> None:
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    creature.temp_flags[flag] = True
