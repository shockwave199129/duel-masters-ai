"""Test Step 8: select_mana awaited choice with real mana combos."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.actions import ActionType
from core.cards import CardDefinition, CardEffect
from core.enums import (
    CardSubtype, CardType, Civilization, EffectAction, EffectType, Phase,
    TriggerEvent, ManaUsage,
)
from core.player_state import PlayerState
from core.state import GameState, PendingTrigger, TurnInfo, AwaitedChoice
from core.zones import Creature, HandCard, ManaCard
from engine.action_generator import get_legal_actions, _get_mana_combinations
from engine.effect_executor import (
    execute_pending_trigger,
    _collect_mana_options,
    _resolve_mana_payment_requirements,
)


def check(name, condition, detail=""):
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


def card(cid, name, cost=3, civs=(Civilization.FIRE,)):
    return CardDefinition(
        id=cid, slug=name.lower().replace(" ", "_"), name=name,
        cost=cost, power=2000,
        card_type=CardType.CREATURE,
        card_subtype=CardSubtype.NONE,
        civilizations=frozenset(civs),
        races=frozenset(),
        keywords=frozenset(),
        effects=tuple(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
    )


def mana(defn, uid=None, tapped=False):
    return ManaCard(definition=defn, uid=uid or f"m-{defn.id}", is_tapped=tapped)


def effect(action, **value):
    return CardEffect(
        card_id=1, ability_index=0, raw_text="test",
        effect_type=EffectType.TRIGGERED, trigger_event=TriggerEvent.ON_SUMMON,
        effect_action=action, trigger_condition={},
        effect_target={}, effect_value=dict(value),
        is_optional=False, is_replacement=False,
        active_in_phase=tuple(), active_in_zone=tuple(), parse_confidence=1.0,
    )


def main() -> int:
    failed = 0
    print("\n" + "=" * 60)
    print("  STEP 8: SELECT_MANA CHOICE")
    print("=" * 60 + "\n")

    fire3 = card(1, "FireThree", cost=3, civs=(Civilization.FIRE,))
    fire2 = card(2, "FireTwo", cost=2, civs=(Civilization.FIRE,))
    multi = card(3, "Multi", cost=2, civs=(Civilization.FIRE, Civilization.WATER))

    mana_zone = [mana(fire3, "m1"), mana(fire3, "m2"), mana(fire3, "m3")]
    source_creature = Creature(
        definition=fire3, uid="src-c", controller=0, owner=0, entered_turn=1,
    )

    state = GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0",
                mana_zone=mana_zone,
                battle_zone=[source_creature],
            ),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )

    eff = effect(EffectAction.PUT_TO_MANA, select_mana=True, cost=3, civilizations=["Fire"])
    trigger = PendingTrigger(
        effect=eff,
        source_uid="src-c",
        source_card_id=fire3.id,
        controller=0,
    )

    print("── _collect_mana_options ──")
    combos = _collect_mana_options(state, 0, eff, trigger)
    expected = _get_mana_combinations(mana_zone, 3, frozenset({Civilization.FIRE}))
    if not check("Returns mana combos", len(combos) == len(expected) and len(combos) >= 1):
        failed += 1
    if combos and not check("Combo entries are ManaUsage lists", isinstance(combos[0][0], ManaUsage)):
        failed += 1

    print("── Source card fallback ──")
    eff2 = effect(EffectAction.PUT_TO_MANA, select_mana=True)
    cost, civs = _resolve_mana_payment_requirements(state, trigger, eff2)
    if not check("Falls back to source card cost", cost == 3):
        failed += 1
    if not check("Falls back to source card civs", civs == frozenset({Civilization.FIRE})):
        failed += 1

    print("── execute_pending_trigger sets real options ──")
    paused = execute_pending_trigger(state.copy(), trigger)
    choice = paused.effect_stack.awaited_choice
    if not check("AwaitedChoice is select_mana", choice and choice.choice_type == "select_mana"):
        failed += 1
    if choice and not check(
        "valid_options are real combos (not placeholder)",
        choice.valid_options
        and not (len(choice.valid_options) == 1 and choice.valid_options[0] == "mana_combo"),
    ):
        failed += 1
    if choice and not check(
        "valid_options match _collect_mana_options",
        len(choice.valid_options) == len(combos),
    ):
        failed += 1

    print("── get_legal_actions expands combos ──")
    if choice:
        state_choice = state.copy()
        state_choice.effect_stack.set_choice(choice)
        actions = get_legal_actions(state_choice)
        mana_actions = [a for a in actions if a.action_type == ActionType.SELECT_MANA]
        if not check("SELECT_MANA actions generated", len(mana_actions) == len(combos)):
            failed += 1
        if mana_actions and not check(
            "Each action carries mana_used",
            all(len(a.mana_used) >= 1 for a in mana_actions),
        ):
            failed += 1

    print("── No valid mana → pass only ──")
    empty_state = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", mana_zone=[]),
            PlayerState(player_index=1, player_name="P1"),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN),
    )
    empty_combos = _collect_mana_options(empty_state, 0, eff, trigger)
    if not check("Empty mana zone yields no combos", empty_combos == []):
        failed += 1
    empty_choice = AwaitedChoice(
        choice_type="select_mana",
        player=0,
        effect=eff,
        source_uid="src-c",
        valid_options=empty_combos,
        prompt="Pay mana",
    )
    empty_state.effect_stack.set_choice(empty_choice)
    empty_actions = get_legal_actions(empty_state)
    if not check(
        "No combos → PASS offered",
        len(empty_actions) == 1 and empty_actions[0].action_type == ActionType.PASS,
    ):
        failed += 1

    print("\n" + "=" * 60)
    if failed:
        print(f"  FAILED: {failed} assertion(s)")
    else:
        print("  ALL SELECT_MANA TESTS PASSED")
    print("=" * 60 + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
