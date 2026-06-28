"""engine/cards/effect_actions/misc.py — Miscellaneous and control effects."""
from __future__ import annotations

import random

from core.state import GameState, PendingTrigger
from core.enums import CardType
from core.cards import CardDefinition
from core.zones import Creature, HandCard, ManaCard, ShieldCard
from engine.zone_mover import (
    _new_uid,
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


_BZ_STANDALONE_TYPES = frozenset({
    CardType.CREATURE,
    CardType.FIELD,
    CardType.TAMASEED,
    CardType.CROSS_GEAR,
})


def _find_in_zone(p_state, zone_name: str, uid: str):
    zone = getattr(p_state, zone_name, None)
    if zone is None:
        return None
    for card in zone:
        if getattr(card, "uid", None) == uid:
            return card
    return None


def _can_enter_battle_zone(defn: CardDefinition) -> bool:
    return defn.card_type in _BZ_STANDALONE_TYPES


def _remove_global_effects_for_creature(state: GameState, creature: Creature) -> None:
    state.global_effects.remove_by_source(creature.uid)


def _creature_entering_battle_zone(
    state: GameState,
    player: int,
    card,
    *,
    inherit_from: Creature | None = None,
    entering_attacking: bool = False,
) -> Creature:
    """Create a battle-zone entry inheriting state from *inherit_from* (701.26a)."""
    creature = Creature(
        definition=card.definition,
        uid=card.uid,
        controller=player,
        owner=getattr(card, "owner", player),
        entered_turn=state.turn_number,
        has_summoning_sickness=False if entering_attacking else True,
    )
    if inherit_from is not None:
        creature.is_tapped = inherit_from.is_tapped
        creature.has_summoning_sickness = inherit_from.has_summoning_sickness
        creature.has_attacked_this_turn = inherit_from.has_attacked_this_turn
    if card.definition.card_type == CardType.FIELD:
        creature.field_orientation = getattr(inherit_from, "field_orientation", "upright") if inherit_from else "upright"
    creature.apply_static_effects(state)
    return creature


def _add_card_to_zone(
    state: GameState,
    player: int,
    zone_name: str,
    card,
    *,
    inherit_from: Creature | None = None,
    entering_attacking: bool = False,
) -> Creature | None:
    p_state = state.players[player]
    if zone_name == "hand":
        p_state.hand.append(HandCard(definition=card.definition, uid=card.uid))
        return None
    if zone_name == "battle_zone":
        if isinstance(card, Creature):
            creature = card
            if inherit_from is not None:
                creature.is_tapped = inherit_from.is_tapped
                creature.has_summoning_sickness = inherit_from.has_summoning_sickness
                creature.has_attacked_this_turn = inherit_from.has_attacked_this_turn
            if entering_attacking:
                creature.has_summoning_sickness = False
            p_state.battle_zone.append(creature)
            creature.apply_static_effects(state)
            return creature
        if not _can_enter_battle_zone(card.definition):
            return None
        creature = _creature_entering_battle_zone(
            state, player, card,
            inherit_from=inherit_from,
            entering_attacking=entering_attacking,
        )
        p_state.battle_zone.append(creature)
        return creature
    if zone_name == "mana_zone":
        p_state.mana_zone.append(ManaCard.from_charge(card.definition))
        return None
    if zone_name == "shield_zone":
        p_state.shield_zone.append(ShieldCard(definition=card.definition, uid=card.uid))
        return None
    if zone_name == "graveyard":
        from core.zones import GraveyardCard
        p_state.graveyard.insert(
            0,
            GraveyardCard(
                definition=card.definition,
                uid=getattr(card, "uid", _new_uid()),
                died_from="swap_zones",
                died_on_turn=state.turn_number,
            ),
        )
        return None
    return None


def _validate_paired_swap(
    p_state,
    zone_a: str,
    zone_b: str,
    card_a,
    card_b,
) -> bool:
    """Rule 701.26b: if either move is illegal, the whole swap fails."""
    defn_a = card_a.definition
    defn_b = card_b.definition
    if zone_b == "battle_zone" and not _can_enter_battle_zone(defn_a):
        return False
    if zone_a == "battle_zone" and not _can_enter_battle_zone(defn_b):
        return False
    if zone_a == "battle_zone" and isinstance(card_a, Creature) and card_a.is_ignored:
        return False
    if zone_b == "battle_zone" and isinstance(card_b, Creature) and card_b.is_ignored:
        return False
    return True


def _update_attack_context_after_swap(
    state: GameState,
    outgoing_uid: str,
    incoming_uid: str,
    *,
    entering_attacking: bool,
) -> None:
    if not state.attack_context or not entering_attacking:
        return
    if state.attack_context.attacker_uid == outgoing_uid:
        state.attack_context.attacker_uid = incoming_uid



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
    Handle SWAP_ZONES effect: swap cards between zones (Revolution Change / J-Change).

    Rule 701.26a: swapped-in BZ creature inherits orientation/state of swapped-out.
    Revolution Change / J-Change entrants are attacking.
    Rule 701.26b: if either movement fails, neither card moves.
    """
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    from_zone_a = effect.get("from_zone_a")
    from_zone_b = effect.get("from_zone_b")
    card_uid_a = data.get("card_uid_a") or effect.get("card_uid_a")
    card_uid_b = data.get("card_uid_b") or effect.get("card_uid_b")
    target_player = effect.get("target_player", controller)
    swap_type = effect.get("swap_type", "normal")
    entering_attacking = swap_type in ("revolution_change", "j_change")

    if not from_zone_a or not from_zone_b or not card_uid_a:
        return

    p_state = state.players[target_player]

    card_a = _find_in_zone(p_state, from_zone_a, card_uid_a)
    if not card_a:
        return

    card_b = None
    if card_uid_b:
        card_b = _find_in_zone(p_state, from_zone_b, card_uid_b)
    elif from_zone_b == "battle_zone" and state.attack_context:
        # Revolution Change during attack: swap with the attacking creature
        card_b = p_state.find_creature(state.attack_context.attacker_uid)
    elif getattr(p_state, from_zone_b, None):
        zone_b = getattr(p_state, from_zone_b)
        if zone_b:
            card_b = zone_b[0]

    if not card_b:
        return

    if not _validate_paired_swap(p_state, from_zone_a, from_zone_b, card_a, card_b):
        return  # Rule 701.26b

    inherit_for_a = card_b if isinstance(card_b, Creature) else None
    inherit_for_b = card_a if isinstance(card_a, Creature) else None
    outgoing_bz_uid = card_b.uid if from_zone_b == "battle_zone" and isinstance(card_b, Creature) else None

    zone_a = getattr(p_state, from_zone_a)
    zone_b = getattr(p_state, from_zone_b)
    zone_a.remove(card_a)
    zone_b.remove(card_b)

    if from_zone_a == "battle_zone" and isinstance(card_a, Creature):
        _remove_global_effects_for_creature(state, card_a)
    if from_zone_b == "battle_zone" and isinstance(card_b, Creature):
        _remove_global_effects_for_creature(state, card_b)

    incoming = _add_card_to_zone(
        state, target_player, from_zone_b, card_a,
        inherit_from=inherit_for_a,
        entering_attacking=entering_attacking and from_zone_b == "battle_zone",
    )
    _add_card_to_zone(
        state, target_player, from_zone_a, card_b,
        inherit_from=inherit_for_b,
        entering_attacking=False,
    )

    if incoming and outgoing_bz_uid:
        _update_attack_context_after_swap(
            state, outgoing_bz_uid, incoming.uid,
            entering_attacking=entering_attacking,
        )



def _do_turn_upside_down(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle TURN_UPSIDE_DOWN effect: flip a Field card upside down (Rule 701.28).

    Effect value:
    - field_uid: UID of the Field creature in the battle zone
    """
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    field_uid = data.get("field_uid") or effect.get("field_uid")
    target_player = effect.get("target_player", controller)
    if not field_uid:
        return

    for creature in state.players[target_player].battle_zone:
        if creature.uid != field_uid or not creature.is_field():
            continue
        creature.field_orientation = (
            "upright" if creature.field_orientation == "upside_down" else "upside_down"
        )
        break



def _do_shieldify(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle SHIELDIFY effect (Rule 701.32).

    Effect value:
    - from_zone: "hand" (default) or "deck"
    - face_up: bool — False = face-down shield (701.32a), True = face-up (701.32b)
    - card_uid / card_uids: specific card(s) to shieldify
    - count: number of cards when no uid specified (deck top / random hand)
    - target_player: who receives the shield (default: controller)
    """
    from engine.special_cards.shieldify import perform_shieldify

    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    from_zone = effect.get("from_zone", "hand")
    target_player = effect.get("target_player", controller)
    face_up = bool(effect.get("face_up", False))
    count = int(effect.get("count", 1))
    card_uid = data.get("card_uid") or effect.get("card_uid")
    card_uids = list(data.get("card_uids") or effect.get("card_uids") or [])

    perform_shieldify(
        state,
        controller,
        from_zone=from_zone,
        target_player=target_player,
        card_uid=card_uid,
        card_uids=card_uids or None,
        face_up=face_up,
        count=count,
    )



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
