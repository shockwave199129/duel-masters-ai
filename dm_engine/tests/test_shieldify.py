"""Test Step 7: SHIELDIFY (701.32, 113.1)."""

import sys
sys.path.insert(0, "/home/riki/Downloads/dm-files/dm_engine")

from engine.zone_mover import move_hand_to_battle  # noqa: F401 — avoid circular import

from core.state import GameState, TurnInfo, PendingTrigger
from core.enums import (
    Phase, CardType, CardSubtype, Civilization, EffectAction, EffectType,
    TriggerEvent, MAX_SHIELDS,
)
from core.cards import CardDefinition, CardEffect
from core.zones import HandCard, ShieldCard
from core.player_state import PlayerState
from engine.special_cards.shieldify import perform_shieldify
from engine.cards.effect_actions.misc import _do_shieldify


def card(cid, name, **kw):
    return CardDefinition(
        id=cid, slug=name.lower().replace(" ", "-"), name=name,
        cost=kw.get("cost", 3), power=kw.get("power", 2000),
        card_type=CardType.CREATURE,
        card_subtype=CardSubtype.NONE,
        is_multiface=False,
        civilizations=frozenset({Civilization.FIRE}),
        races=frozenset(),
        keywords=frozenset(),
        effects=tuple(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
    )


def hand_card(defn, uid):
    return HandCard(definition=defn, uid=uid)


def effect(action, **value):
    return CardEffect(
        card_id=1, ability_index=0, raw_text="test",
        effect_type=EffectType.TRIGGERED, trigger_event=TriggerEvent.ON_SUMMON,
        effect_action=action, trigger_condition={},
        effect_target={}, effect_value=dict(value),
        is_optional=False, is_replacement=False,
        active_in_phase=tuple(), active_in_zone=tuple(), parse_confidence=1.0,
    )


def trigger_for(effect_obj, **data):
    return PendingTrigger(
        effect=effect_obj,
        controller=0,
        source_uid="src",
        source_card_id=1,
        trigger_data=dict(data),
    )


def check(name, condition, detail=""):
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


def main() -> int:
    failed = 0
    print("\n" + "=" * 60)
    print("  STEP 7: SHIELDIFY (701.32)")
    print("=" * 60 + "\n")

    c1 = card(1, "Hand Shield A")
    c2 = card(2, "Hand Shield B")
    deck_top = card(10, "Deck Top")
    deck_second = card(11, "Deck Second")

    # ── Face-down from hand (701.32a) ─────────────────────────────────────────
    print("── Face-down from hand ──")
    state = GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0",
                hand=[hand_card(c1, "h1")],
                shield_zone=[],
            ),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    added = perform_shieldify(state, 0, from_zone="hand", card_uid="h1", face_up=False)
    shield = state.players[0].shield_zone[0]
    if not check("Adds one shield from hand", added == 1 and len(state.players[0].shield_zone) == 1):
        failed += 1
    if not check("Hand card removed", len(state.players[0].hand) == 0):
        failed += 1
    if not check("Shield is face-down (701.32a)", not shield.is_face_up):
        failed += 1
    if not check("Shield keeps hand uid", shield.uid == "h1"):
        failed += 1

    # ── Face-up from hand (701.32b) ─────────────────────────────────────────
    print("── Face-up from hand ──")
    state2 = GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0",
                hand=[hand_card(c2, "h2")],
                shield_zone=[],
            ),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    perform_shieldify(state2, 0, from_zone="hand", card_uid="h2", face_up=True)
    shield2 = state2.players[0].shield_zone[0]
    if not check("Shield is face-up (701.32b)", shield2.is_face_up):
        failed += 1

    # ── From deck (top card) ──────────────────────────────────────────────────
    print("── From deck ──")
    state3 = GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0",
                deck=[deck_top, deck_second],
                shield_zone=[],
            ),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    perform_shieldify(state3, 0, from_zone="deck", face_up=False)
    if not check("Deck top becomes shield", state3.players[0].shield_zone[0].definition.name == "Deck Top"):
        failed += 1
    if not check("Second deck card remains", len(state3.players[0].deck) == 1):
        failed += 1

    # ── Specific deck card by id ──────────────────────────────────────────────
    print("── Deck by card id ──")
    state3b = GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0",
                deck=[deck_top, deck_second],
                shield_zone=[],
            ),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    perform_shieldify(state3b, 0, from_zone="deck", card_uid="11")
    if not check("Picks deck card by id", state3b.players[0].shield_zone[0].definition.id == 11):
        failed += 1

    # ── MAX_SHIELDS cap (113.1) partial resolution ────────────────────────────
    print("── Shield cap (113.1) ──")
    full_shields = [ShieldCard(definition=card(99, f"S{i}")) for i in range(MAX_SHIELDS)]
    state4 = GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0",
                hand=[hand_card(c1, "hx")],
                shield_zone=list(full_shields),
            ),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    added4 = perform_shieldify(state4, 0, from_zone="hand", card_uid="hx")
    if not check("No shield added when full", added4 == 0 and len(state4.players[0].shield_zone) == MAX_SHIELDS):
        failed += 1
    if not check("Hand unchanged when full", len(state4.players[0].hand) == 1):
        failed += 1

    # Partial: 4 shields, try to add 2 via card_uids
    print("── Partial resolution (101.3) ──")
    four_shields = [ShieldCard(definition=card(98, f"F{i}")) for i in range(MAX_SHIELDS - 1)]
    ha = hand_card(card(20, "Extra A"), "ea")
    hb = hand_card(card(21, "Extra B"), "eb")
    state5 = GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0",
                hand=[ha, hb],
                shield_zone=list(four_shields),
            ),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    added5 = perform_shieldify(
        state5, 0, from_zone="hand", card_uids=["ea", "eb"], face_up=False,
    )
    if not check("Adds only one when one slot left", added5 == 1 and len(state5.players[0].shield_zone) == MAX_SHIELDS):
        failed += 1
    if not check("One hand card remains", len(state5.players[0].hand) == 1):
        failed += 1

    # ── Target opponent ───────────────────────────────────────────────────────
    print("── Target player ──")
    state6 = GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0",
                hand=[hand_card(c1, "h-op")],
            ),
            PlayerState(player_index=1, player_name="P1", shield_zone=[]),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    perform_shieldify(state6, 0, from_zone="hand", card_uid="h-op", target_player=1, face_up=True)
    if not check("Shield goes to target player", len(state6.players[1].shield_zone) == 1):
        failed += 1
    if not check("Controller shield zone empty", len(state6.players[0].shield_zone) == 0):
        failed += 1

    # ── Handler wiring (_do_shieldify) ────────────────────────────────────────
    print("── Effect handler ──")
    state7 = GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0",
                hand=[hand_card(c2, "hw")],
                shield_zone=[],
            ),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    eff = effect(EffectAction.SHIELDIFY, from_zone="hand", face_up=True, target_player=0)
    _do_shieldify(state7, 0, trigger_for(eff, card_uid="hw"))
    if not check("_do_shieldify sets face_up", state7.players[0].shield_zone[0].is_face_up):
        failed += 1

    print("\n" + "=" * 60)
    if failed:
        print(f"  FAILED: {failed} assertion(s)")
    else:
        print("  ALL SHIELDIFY TESTS PASSED")
    print("=" * 60 + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
