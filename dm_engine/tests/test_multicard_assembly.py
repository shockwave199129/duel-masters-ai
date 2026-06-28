"""Test Step 6: multi-card assembly (Forbidden Explosion + Zerom Birth)."""

import sys
sys.path.insert(0, "/home/riki/Downloads/dm-files/dm_engine")

# Load zone_mover first to avoid special_cards ↔ zone_mover circular import.
from engine.zone_mover import move_hand_to_battle  # noqa: F401

from core.state import GameState, TurnInfo, PendingTrigger
from core.enums import (
    Phase, CardSubtype, CardType, Civilization,
    EffectAction, EffectType, TriggerEvent,
)
from core.cards import CardDefinition, CardEffect
from core.zones import Creature
from core.player_state import PlayerState
from engine.special_cards.forbidden_explosion import (
    perform_forbidden_explosion,
    is_final_forbidden_field,
    FINAL_FORBIDDEN_FIELD_COUNT,
)
from engine.special_cards.zerom_assembly import (
    perform_zerom_birth,
    is_zerom_nebula,
    is_zerom_ritual,
)
from engine.cards.effect_actions.win_loss import _do_forbidden_explosion
from engine.cards.effect_actions.hyper_mode import _do_zerom_birth


def card(cid, name, card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE, **kw):
    return CardDefinition(
        id=cid, slug=name.lower().replace(" ", "-"), name=name,
        cost=kw.get("cost", 0), power=kw.get("power", 5000),
        card_type=card_type, card_subtype=card_subtype,
        civilizations=frozenset({Civilization.DARKNESS}),
        races=frozenset(),
        keywords=frozenset(),
        effects=tuple(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=True,
        other_face_id=kw.get("other_face_id"),
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
    print("  STEP 6: MULTI-CARD ASSEMBLY")
    print("=" * 60 + "\n")

    # ── Forbidden Explosion ────────────────────────────────────────────────────
    print("── Forbidden Explosion (701.29) ──")
    field_face = card(
        1, "Final Forbidden Field A",
        card_type=CardType.FIELD, card_subtype=CardSubtype.FINAL_FORBIDDEN,
        power=None, other_face_id=10,
    )
    creature_face = card(
        10, "Final Forbidden Creature",
        card_type=CardType.CREATURE, card_subtype=CardSubtype.FINAL_FORBIDDEN,
        power=9999,
    )
    core_face = card(11, "Forbidden Core", card_type=CardType.CORE, power=None)

    fields = [
        Creature(
            definition=field_face, uid=f"ff_{i}", controller=0, owner=0,
            entered_turn=1, has_summoning_sickness=False,
        )
        for i in range(FINAL_FORBIDDEN_FIELD_COUNT)
    ]
    fields[4].definition = card(
        5, "Final Forbidden Field E",
        card_type=CardType.FIELD, card_subtype=CardSubtype.FINAL_FORBIDDEN,
        power=None, other_face_id=11,
    )

    if not check("is_final_forbidden_field helper", is_final_forbidden_field(field_face)):
        failed += 1

    state = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", battle_zone=fields),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )

    assembled = perform_forbidden_explosion(
        state, 0,
        assembled_creature_def=creature_face,
        forbidden_core_def=core_face,
    )
    if not check("Forbidden explosion returns creature", assembled is not None):
        failed += 1
    if not check("Only one BZ entry remains", len(state.players[0].battle_zone) == 1):
        failed += 1
    if assembled and not check("Assembled is Final Forbidden Creature", assembled.definition.id == 10):
        failed += 1
    if assembled and not check("Five linked components", len(assembled.linked_cells) == 5):
        failed += 1
    if assembled and not check("Explosion flag set", assembled.temp_flags.get("_forbidden_explosion") is True):
        failed += 1
    if assembled and not check("Core attached under creature", len(assembled.attached_cards) == 1):
        failed += 1
    if assembled and not check("Core is Forbidden Core", assembled.attached_cards[0].card_type == CardType.CORE):
        failed += 1

    # Effect handler path
    fields2 = [
        Creature(
            definition=field_face, uid=f"ff2_{i}", controller=0, owner=0,
            entered_turn=1, has_summoning_sickness=False,
        )
        for i in range(FINAL_FORBIDDEN_FIELD_COUNT)
    ]
    state2 = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", battle_zone=fields2),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    _do_forbidden_explosion(state2, 0, PendingTrigger(
        effect=effect(EffectAction.FORBIDDEN_EXPLOSION),
        source_uid="ff2_0", source_card_id=1, controller=0,
        trigger_data={
            "assembled_creature_definition": creature_face,
            "forbidden_core_definition": core_face,
        },
    ))
    if not check("Handler assembles via effect path", len(state2.players[0].battle_zone) == 1):
        failed += 1

    # ── Zerom Birth ────────────────────────────────────────────────────────────
    print("\n── Zerom Birth (701.31 / 812) ──")
    ritual_face = card(
        20, "Ritual of Zerom",
        card_type=CardType.RITUAL, card_subtype=CardSubtype.ZEROM,
        other_face_id=30,
    )
    nebula_face = card(
        21, "Nebula of Zerom",
        card_type=CardType.NEBULA, card_subtype=CardSubtype.NONE,
        other_face_id=30,
    )
    zerom_creature = card(
        30, "Zerom Creature",
        card_type=CardType.CREATURE, card_subtype=CardSubtype.ZEROM,
        power=12000,
    )

    if not check("is_zerom_ritual helper", is_zerom_ritual(ritual_face)):
        failed += 1
    if not check("is_zerom_nebula helper", is_zerom_nebula(nebula_face)):
        failed += 1

    ritual = Creature(
        definition=ritual_face, uid="ritual_1", controller=0, owner=0,
        entered_turn=1, has_summoning_sickness=False,
    )
    nebulas = [
        Creature(
            definition=nebula_face, uid=f"nebula_{i}", controller=0, owner=0,
            entered_turn=1, has_summoning_sickness=False,
        )
        for i in range(4)
    ]
    state3 = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", battle_zone=[ritual, *nebulas]),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )

    zerom = perform_zerom_birth(state3, 0, assembled_creature_def=zerom_creature)
    if not check("Zerom birth returns creature", zerom is not None):
        failed += 1
    if not check("One BZ entry after Zerom birth", len(state3.players[0].battle_zone) == 1):
        failed += 1
    if zerom and not check("Assembled Zerom creature face", zerom.definition.id == 30):
        failed += 1
    if zerom and not check("Five linked Zerom components", len(zerom.linked_cells) == 5):
        failed += 1
    if zerom and not check("Zerom flipped flag", zerom.temp_flags.get("_zerom_flipped") is True):
        failed += 1
    if zerom and not check("Zerom birth flag", zerom.temp_flags.get("_zerom_birth") is True):
        failed += 1

    state4 = GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0",
                battle_zone=[
                    Creature(definition=ritual_face, uid="r2", controller=0, owner=0, entered_turn=1),
                    *[Creature(definition=nebula_face, uid=f"n2_{i}", controller=0, owner=0, entered_turn=1) for i in range(4)],
                ],
            ),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    _do_zerom_birth(state4, 0, PendingTrigger(
        effect=effect(EffectAction.ZEROM_BIRTH),
        source_uid="r2", source_card_id=20, controller=0,
        trigger_data={"assembled_creature_definition": zerom_creature},
    ))
    if not check("Zerom handler assembles via effect path", len(state4.players[0].battle_zone) == 1):
        failed += 1

    print("\n" + "=" * 60)
    print(f"  RESULTS: {failed} failed")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
