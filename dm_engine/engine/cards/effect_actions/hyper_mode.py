"""engine/cards/effect_actions/hyper_mode.py — Hyper mode and release effects."""
from __future__ import annotations

from core.state import GameState, PendingTrigger
from core.zones import Creature
from core.cards import is_hyper_mode
from core.enums import CardSubtype
from engine.zone_mover import (
    flip_forbidden,
    move_hand_to_battle,
    move_zerom_to_battle,
    swap_hyper_mode,
)

# ── Shared helpers ──────────────────────────────────────────────────────────

def _find_creature(state: GameState, uid: str) -> tuple[int, Creature] | None:
    if not uid:
        return None
    return state.find_creature_anywhere(uid)



def _trigger_data(trigger: PendingTrigger) -> dict:
    return dict(trigger.trigger_data)



# ── Effect action implementations ──────────────────────────────────────────

def _do_hyperize(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    found = _find_creature(state, data.get("creature_uid") or data.get("target_uid"))
    if not found:
        return
    _, creature = found
    if creature.hyper_mode_released:
        # Already released — nothing to do
        return
    if not is_hyper_mode(creature.definition):
        # Not a Hyper Mode creature — just set flag as fallback
        creature.hyper_mode_released = True
        return
    # Remove old static effects before swapping definition
    creature.remove_static_effects(state)
    # Perform the card definition swap
    swap_hyper_mode(creature)
    # Re-apply static effects from the released face
    creature.apply_static_effects(state)



def _do_forbidden_release(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle FORBIDDEN_RELEASE effect (Rule 809).
    
    A Forbidden card in hand is flipped and summoned to the battle zone.
    The trigger data should contain the hand card's uid.
    """
    data = _trigger_data(trigger)
    hand_uid = data.get("hand_uid") or data.get("source_uid")
    if not hand_uid:
        return
    
    # Find the card in hand
    p_state = state.players[controller]
    hand_card = None
    for hc in p_state.hand:
        if hc.uid == hand_uid:
            hand_card = hc
            break
    if hand_card is None:
        return
    
    # Remove from hand, flip, and place in battle zone
    p_state.hand.remove(hand_card)
    creature = move_hand_to_battle(
        state, controller, hand_card.uid, hand_card.definition.id,
        is_forbidden_release=True,
    )
    if creature:
        flip_forbidden(creature)



def _do_neo_evolve(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle NEO_EVOLVE effect (Rule 802).
    
    A NEO creature in the battle zone activates its evolution ability
    to place a new evolution stack entry (evolve in place).
    
    Rule 802.3: A creature placed via NEO Evolution ability is treated as
    having summoning sickness exemption for the rest of the turn it was placed.
    """
    data = _trigger_data(trigger)
    creature_uid = data.get("source_uid") or data.get("creature_uid")
    if not creature_uid:
        return
    
    found = _find_creature(state, creature_uid)
    if not found:
        return
    _, creature = found
    
    # Add a new evolution stack entry from hand
    p_state = state.players[controller]
    evolve_card_uid = data.get("evolve_card_uid")
    if evolve_card_uid:
        for hc in p_state.hand:
            if hc.uid == evolve_card_uid:
                p_state.hand.remove(hc)
                from core.zones.creature import EvolutionStackEntry
                entry = EvolutionStackEntry(
                    definition=hc.definition,
                    uid=hc.uid,
                    owner=controller,
                    entered_turn=state.turn_number,
                    neo_evolution_placed=True,  # Rule 802.3: NEO Evolution placement
                )
                creature.evolution_stack.append(entry)
                break




def _do_zerom_birth(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle ZEROM_BIRTH effect: flip a Zerom ritual/nebula to its creature face.
    
    Similar to DRAGSOLVE but for Zerom cards (Rule 812 / 701.31).
    The trigger should target a Zerom card in the battle zone.
    """
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid") or data.get("creature_uid")
    if not target_uid:
        return
    
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    
    # Check if this is a Zerom
    if not creature.definition.card_subtype == CardSubtype.ZEROM:
        return
    
    # Use the move_zerom_to_battle function which handles the flip
    move_zerom_to_battle(state, controller, creature.definition)



def _do_zerom_ritual(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle Zerom ritual cast: remove the Zerom from its current zone,
    create a creature with _zerom_flipped flag, and place it in the battle zone.
    (Rule 812)
    """
    data = _trigger_data(trigger)
    source_uid = data.get("target_uid") or trigger.source_uid
    if not source_uid:
        return

    # Find and remove the Zerom card from its current zone
    p_state = state.players[controller]
    hand_card = p_state.find_in_hand(source_uid)
    if hand_card is not None:
        p_state.hand.remove(hand_card)
        card_def = hand_card.definition
    else:
        # Check mana zone (in case Zerom was charged)
        mana_card = p_state.find_mana(source_uid)
        if mana_card is not None:
            p_state.mana_zone.remove(mana_card)
            card_def = mana_card.definition
        else:
            return  # card not found in any playable zone

    # Use the creature face definition if available, otherwise the card itself
    creature_def = card_def  # Zerom card def IS the creature face
    move_zerom_to_battle(state, controller, creature_def)
