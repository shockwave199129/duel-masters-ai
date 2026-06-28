"""Test Step 4: TODO 10 zone operations (evolve/cross/fortify/deploy/swap/flip)."""

import sys
sys.path.insert(0, "/home/riki/Downloads/dm-files/dm_engine")

from core.state import GameState, TurnInfo, AttackContext, PendingTrigger
from core.enums import (
    Phase, CardSubtype, CardType, Civilization,
    EffectAction, EffectType, TriggerEvent,
)
from core.cards import CardDefinition, CardEffect
from core.zones import Creature, HandCard, ShieldCard
from core.player_state import PlayerState
from engine.evolution_rules import is_valid_evolution_base
from engine.cards.effect_actions.special_summon import (
    _do_evolve, _do_cross_gear, _do_fortify, _do_deploy_field,
)
from engine.cards.effect_actions.misc import _do_swap_zones, _do_turn_upside_down


def card(cid, name, card_type=CardType.CREATURE, civ=Civilization.FIRE, **kw):
    races = kw.pop("races", frozenset({"Human"}))
    evo_races = kw.pop("evolution_source_races", frozenset())
    subtype = kw.pop("card_subtype", CardSubtype.NONE)
    return CardDefinition(
        id=cid, slug=name.lower(), name=name,
        cost=kw.get("cost", 3), power=kw.get("power", 1000),
        card_type=card_type, card_subtype=subtype,
        civilizations=frozenset({civ}),
        races=races,
        keywords=frozenset(),
        effects=tuple(),
        evolution_source_races=evo_races,
        evolution_source_types=frozenset(),
        is_multiface=False,
    )


def effect(action, **value):
    return CardEffect(
        card_id=1, ability_index=0, raw_text="test",
        effect_type=EffectType.TRIGGERED, trigger_event=TriggerEvent.ON_SUMMON,
        effect_action=action, trigger_condition={},
        effect_target={}, effect_value=dict(value),
        is_optional=False, is_replacement=False,
        active_in_phase=tuple(), active_in_zone=tuple(), parse_confidence=1.0,
    )


def check(name, condition, detail=""):
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


def main() -> int:
    failed = 0
    print("\n" + "=" * 60)
    print("  STEP 4: TODO 10 ZONE OPERATIONS")
    print("=" * 60 + "\n")

    print("── Evolution validation ──")
    base_def = card(1, "Human Base", races=frozenset({"Human"}))
    evo_def = card(2, "Human Evo", evolution_source_races=frozenset({"Human"}))
    bad_evo = card(3, "Dragon Evo", evolution_source_races=frozenset({"Dragon"}))
    base = Creature(definition=base_def, uid="base_1", controller=0, owner=0, entered_turn=1)
    if not check("Valid Human evolution base", is_valid_evolution_base(evo_def, base)):
        failed += 1
    if not check("Invalid Dragon evolution base rejected", not is_valid_evolution_base(bad_evo, base)):
        failed += 1

    print("\n── EVOLVE effect ──")
    evo_hand = HandCard(definition=evo_def, uid="evo_hand")
    state = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", hand=[evo_hand], battle_zone=[base]),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    _do_evolve(state, 0, PendingTrigger(
        effect=effect(EffectAction.EVOLVE),
        source_uid="base_1", source_card_id=1, controller=0,
        trigger_data={"target_uid": "base_1", "evolve_card_uid": "evo_hand"},
    ))
    if not check("Evolution stacks on same UID", state.players[0].battle_zone[0].uid == "base_1"):
        failed += 1
    if not check("Top card is evolution creature", state.players[0].battle_zone[0].definition.id == 2):
        failed += 1
    if not check("Evolution stack has base underneath", len(state.players[0].battle_zone[0].evolution_stack) == 1):
        failed += 1
    if not check("Hand emptied after evolve", len(state.players[0].hand) == 0):
        failed += 1

    bad_hand = HandCard(definition=bad_evo, uid="bad_evo")
    base2 = Creature(definition=base_def, uid="base_2", controller=0, owner=0, entered_turn=1)
    state2 = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", hand=[bad_hand], battle_zone=[base2]),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    _do_evolve(state2, 0, PendingTrigger(
        effect=effect(EffectAction.EVOLVE),
        source_uid="base_2", source_card_id=1, controller=0,
        trigger_data={"target_uid": "base_2", "evolve_card_uid": "bad_evo"},
    ))
    if not check("Invalid evolution base leaves hand unchanged", len(state2.players[0].hand) == 1):
        failed += 1

    print("\n── CROSS_GEAR effect ──")
    gear_def = card(10, "Gear", card_type=CardType.CROSS_GEAR, cost=2)
    target_def = card(11, "Target")
    gear = Creature(definition=gear_def, uid="gear_1", controller=0, owner=0, entered_turn=1, has_summoning_sickness=False)
    target = Creature(definition=target_def, uid="tgt_1", controller=0, owner=0, entered_turn=1, has_summoning_sickness=False)
    state3 = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", battle_zone=[gear, target]),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    _do_cross_gear(state3, 0, PendingTrigger(
        effect=effect(EffectAction.CROSS_GEAR),
        source_uid="gear_1", source_card_id=10, controller=0,
        trigger_data={"gear_uid": "gear_1", "target_uid": "tgt_1"},
    ))
    if not check("Cross Gear leaves battle zone", len(state3.players[0].battle_zone) == 1):
        failed += 1
    if not check("Cross Gear attached to target", len(state3.players[0].battle_zone[0].attached_cards) == 1):
        failed += 1

    print("\n── FORTIFY G-Castle ──")
    g_castle_def = card(20, "G-Castle", card_type=CardType.CASTLE, card_subtype=CardSubtype.G_CASTLE, cost=1)
    castle_hand = HandCard(definition=g_castle_def, uid="gc_1")
    shield = ShieldCard(definition=card(21, "Shield"), uid="sh_1")
    state4 = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", hand=[castle_hand], shield_zone=[shield]),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    _do_fortify(state4, 0, PendingTrigger(
        effect=effect(EffectAction.FORTIFY),
        source_uid="gc_1", source_card_id=20, controller=0,
        trigger_data={"castle_uid": "gc_1", "target_uid": "sh_1"},
    ))
    if not check("G-Castle leaves hand", len(state4.players[0].hand) == 0):
        failed += 1
    if not check("G-Castle fortifies shield", len(state4.players[0].shield_zone[0].fortified_castles) == 1):
        failed += 1

    print("\n── DEPLOY_FIELD ──")
    field_def = card(30, "D2 Field", card_type=CardType.FIELD, card_subtype=CardSubtype.D2, cost=0, power=None)
    field_hand = HandCard(definition=field_def, uid="fld_1")
    state5 = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", hand=[field_hand]),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    _do_deploy_field(state5, 0, PendingTrigger(
        effect=effect(EffectAction.DEPLOY_FIELD),
        source_uid="fld_1", source_card_id=30, controller=0,
        trigger_data={"field_uid": "fld_1"},
    ))
    if not check("Field in battle zone", len(state5.players[0].battle_zone) == 1):
        failed += 1
    if not check("Field is upright", state5.players[0].battle_zone[0].field_orientation == "upright"):
        failed += 1
    if not check("Field flagged just_entered", state5.players[0].battle_zone[0].temp_flags.get("just_entered") is True):
        failed += 1
    if not check("Deprecated field_zone unused", len(state5.players[0].field_zone) == 0):
        failed += 1

    print("\n── TURN_UPSIDE_DOWN ──")
    field_creature = state5.players[0].battle_zone[0]
    _do_turn_upside_down(state5, 0, PendingTrigger(
        effect=effect(EffectAction.TURN_UPSIDE_DOWN),
        source_uid="fld_1", source_card_id=30, controller=0,
        trigger_data={"field_uid": "fld_1"},
    ))
    if not check("Field flipped upside down", field_creature.field_orientation == "upside_down"):
        failed += 1

    print("\n── SWAP_ZONES (Revolution Change) ──")
    attacker_def = card(40, "Attacker")
    attacker = Creature(
        definition=attacker_def, uid="atk_1", controller=0, owner=0,
        entered_turn=1, has_summoning_sickness=False, is_tapped=True,
    )
    rev_hand = HandCard(definition=card(41, "Revolution"), uid="rev_1")
    state6 = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", hand=[rev_hand], battle_zone=[attacker]),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.ATTACK),
        attack_context=AttackContext(
            attacker_uid="atk_1", attacker_player=0,
            target_type="player", target_uid=None,
        ),
    )
    _do_swap_zones(state6, 0, PendingTrigger(
        effect=effect(
            EffectAction.SWAP_ZONES,
            from_zone_a="hand", from_zone_b="battle_zone",
            swap_type="revolution_change",
        ),
        source_uid="rev_1", source_card_id=41, controller=0,
        trigger_data={"card_uid_a": "rev_1", "card_uid_b": "atk_1"},
    ))
    if not check("Attacker moved to hand", any(c.uid == "atk_1" for c in state6.players[0].hand)):
        failed += 1
    if not check("Revolution creature in battle zone", len(state6.players[0].battle_zone) == 1):
        failed += 1
    incoming = state6.players[0].battle_zone[0]
    if not check("Incoming inherits tapped state", incoming.is_tapped is True):
        failed += 1
    if not check("Attack context updated to incoming", state6.attack_context.attacker_uid == incoming.uid):
        failed += 1

    print("\n" + "=" * 60)
    print(f"  RESULTS: {failed} failed")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
