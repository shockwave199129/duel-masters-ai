"""engine/cards/effect_actions/special_summon.py — Special summon and evolution effects."""
from __future__ import annotations

from core.state import GameState, PendingTrigger
from core.enums import CardType
from core.zones import Creature, HandCard
from core.cards import CardDefinition, is_g_castle
from engine.evolution_rules import is_valid_evolution_base
from engine.zone_mover import (
    _new_uid,
    awaken_psychic_creature,
    combine_king_cells,
    cross_gear_to_creature,
    dragsolve_dragheart,
    fortify_g_castle_to_shield,
    fortify_shield_with_castle,
    link_psychic_cells,
    move_hand_to_battle,
    move_hand_to_field,
    move_ultra_gr_to_battle,
)
from engine.god_manager import GodManager

# ── Shared helpers ──────────────────────────────────────────────────────────

def _effect_value(trigger: PendingTrigger) -> dict:
    return dict(trigger.effect.effect_value)



def _find_creature(state: GameState, uid: str) -> tuple[int, Creature] | None:
    if not uid:
        return None
    return state.find_creature_anywhere(uid)



def _move_card_to_hand(state: GameState, player: int, definition: CardDefinition, uid: str | None = None) -> HandCard:
    card = HandCard(definition=definition, uid=uid or _new_uid())
    state.players[player].hand.append(card)
    return card


def _trigger_data(trigger: PendingTrigger) -> dict:
    return dict(trigger.trigger_data)



# ── Effect action implementations ──────────────────────────────────────────

def _do_gr_summon(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    source_card_id = data.get("source_card_id")

    # Check if the card is in the Ultra GR zone
    if source_card_id is not None:
        ultra_gr_def = state.find_in_ultra_gr(controller, source_card_id)
        if ultra_gr_def is not None:
            move_ultra_gr_to_battle(state, controller, ultra_gr_def)
            return

    # Otherwise, summon from hand (existing behavior)
    card_uid = data.get("card_uid")
    if card_uid:
        move_hand_to_battle(state, controller, card_uid)
        return
    definition = data.get("card_definition")
    if isinstance(definition, CardDefinition):
        _move_card_to_hand(state, controller, definition)



def _do_awaken(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    awakened = data.get("awakened_face_definition")
    if not isinstance(awakened, CardDefinition):
        return
    found = _find_creature(state, data.get("target_uid") or data.get("creature_uid"))
    if not found:
        return
    _, creature = found
    awaken_psychic_creature(state, controller, creature.uid, awakened)



def _do_awaken_link(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    cell_uids = list(data.get("cell_uids") or [])
    super_def = data.get("super_creature_definition")
    if not isinstance(super_def, CardDefinition) or not cell_uids:
        return
    link_psychic_cells(state, controller, cell_uids, super_def, primary_uid=data.get("primary_uid"))



def _do_dragsolve(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    face_def = data.get("creature_face_definition")
    if not isinstance(face_def, CardDefinition):
        return
    found = _find_creature(state, data.get("target_uid") or data.get("creature_uid"))
    if not found:
        return
    _, creature = found
    dragsolve_dragheart(state, controller, creature.uid, face_def)



def _do_link_release(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    target_uid = data.get("target_uid") or data.get("creature_uid")
    found = _find_creature(state, target_uid)
    if not found:
        return
    _, creature = found
    creature.temp_flags["link_release"] = True



def _do_evolve(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle EVOLVE effect: evolve a creature by placing an evolution creature on top of a base.

    Effect value:
    - target_uid: UID of the base creature to evolve (in battle zone)
    - evolve_card_uid: UID of the evolution creature in hand
    - is_neo: boolean (if NEO Evolution)
    """
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    target_uid = data.get("target_uid") or effect.get("target_uid")
    evolve_card_uid = data.get("evolve_card_uid") or effect.get("evolve_card_uid")
    if not target_uid or not evolve_card_uid:
        return

    found = _find_creature(state, target_uid)
    if not found:
        return
    _, base_creature = found

    p_state = state.players[controller]
    hand_card = p_state.find_in_hand(evolve_card_uid)
    if not hand_card:
        return

    # Rule 801.1a: reject invalid evolution bases
    if not is_valid_evolution_base(hand_card.definition, base_creature):
        return

    move_hand_to_battle(state, controller, evolve_card_uid, evolution_base_uid=target_uid)



def _find_cross_gear_creature(state: GameState, player: int, gear_uid: str) -> Creature | None:
    """Cross Gear must be in the battle zone before crossing (rules 701.16/701.17)."""
    gear = state.players[player].find_creature(gear_uid)
    if gear is None or gear.definition.card_type != CardType.CROSS_GEAR:
        return None
    return gear


def _do_cross_gear(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle CROSS_GEAR effect: attach a Cross Gear from battle zone to a creature.

    Effect value:
    - target_uid: UID of the creature to attach to (in battle zone)
    - gear_uid: UID of the Cross Gear in battle zone
    """
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    target_uid = data.get("target_uid") or effect.get("target_uid")
    gear_uid = data.get("gear_uid") or effect.get("gear_uid")
    if not target_uid or not gear_uid:
        return

    if _find_cross_gear_creature(state, controller, gear_uid) is None:
        return

    cross_gear_to_creature(state, controller, gear_uid, target_uid)



def _do_combine(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    data = _trigger_data(trigger)
    king_def = data.get("king_creature_definition")
    cell_uids = list(data.get("cell_uids") or [])
    if not isinstance(king_def, CardDefinition) or not cell_uids:
        return
    combine_king_cells(state, controller, king_def, cell_uids)



def _do_fortify(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle FORTIFY effect: fortify a shield with a Castle from hand.

    Effect value:
    - target_uid: UID of the shield to fortify
    - castle_uid: UID of the Castle in hand
    """
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    target_uid = data.get("target_uid") or effect.get("target_uid")
    castle_uid = data.get("castle_uid") or effect.get("castle_uid")
    if not target_uid or not castle_uid:
        return

    p_state = state.players[controller]
    hand_card = p_state.find_in_hand(castle_uid)
    if not hand_card:
        return

    if is_g_castle(hand_card.definition):
        fortify_g_castle_to_shield(state, controller, castle_uid, target_uid)
    else:
        fortify_shield_with_castle(state, controller, castle_uid, target_uid)



def _do_deploy_field(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle DEPLOY_FIELD effect: deploy a Field card from hand to the battle zone.

    Effect value:
    - field_uid: UID of the Field card in hand
    - target_player: player who gets the field (default: controller)
    """
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    field_uid = data.get("field_uid") or effect.get("field_uid")
    target_player = effect.get("target_player", controller)
    if not field_uid:
        return

    move_hand_to_field(state, controller, field_uid, target_player=target_player)



def _do_god_link(state: GameState, controller: int, trigger: PendingTrigger) -> None:
    """
    Handle GOD_LINK effect: link God cards together (Rule 804).

    Effect value:
    - source_uid: UID of the God creature in battle zone
    - link_card_uid: UID of the God card in hand to link
    """
    data = _trigger_data(trigger)
    effect = _effect_value(trigger)
    source_uid = data.get("source_uid") or effect.get("source_uid")
    link_card_uid = data.get("link_card_uid") or effect.get("link_card_uid")
    if not source_uid or not link_card_uid:
        return

    # Find the source creature in battle zone
    found = _find_creature(state, source_uid)
    if not found:
        return
    _, source_creature = found

    # Find the link card in hand
    p_state = state.players[controller]
    hand_card = p_state.find_in_hand(link_card_uid)
    if not hand_card:
        return

    # Move the link card from hand — link onto the anchor God (804.3)
    GodManager.link_gods(state, controller, source_creature, hand_card)
