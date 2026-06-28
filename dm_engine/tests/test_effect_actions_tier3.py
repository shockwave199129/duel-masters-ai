"""Tier 3 EffectAction integration tests (rules 701.15–701.32, 506.1b, 507.1b)."""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.actions import ActionType, pass_attack, pass_block
from core.cards import CardDefinition, CardEffect
from core.enums import (
    CardSubtype, CardType, Civilization, EffectAction, EffectType,
    Keyword, Phase, TriggerEvent,
)
from core.player_state import PlayerState
from core.state import AttackContext, GameState, PendingTrigger, TurnInfo
from core.zones import Creature
from engine.action_generator import get_legal_actions
from engine.action_executor import _is_legal_action
from engine.cards.effect_actions.combat import (
    _do_must_attack, _do_must_block, _do_cannot_block,
)
from engine.cards.effect_actions.misc import _do_protection, _do_gain_control
from engine.cards.effect_actions.zone_ops import _do_destroy
from engine.effect_executor import _collect_target_options
from engine.phase_controller import _end_turn
from engine.turn.action_gen import _generate_attack_declarations, _generate_block_actions


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


def card(cid, name, civ=Civilization.FIRE, power=2000, keywords=(), races=("Human",)):
    return CardDefinition(
        id=cid, slug=name.lower().replace(" ", "_"), name=name,
        cost=3, power=power,
        card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE,
        civilizations=frozenset({civ}),
        races=frozenset(races),
        keywords=frozenset(keywords),
        effects=tuple(),
        evolution_source_races=frozenset(),
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


def trigger(action, **data):
    return PendingTrigger(
        effect=effect(action),
        source_uid=data.get("source_uid", "src"),
        source_card_id=data.get("source_card_id", 1),
        controller=data.get("controller", 0),
        trigger_data={k: v for k, v in data.items() if k not in ("source_uid", "source_card_id", "controller")},
    )


def bare_state(**kw) -> GameState:
    p0 = kw.get("p0", PlayerState(player_index=0, player_name="P0"))
    p1 = kw.get("p1", PlayerState(player_index=1, player_name="P1"))
    return GameState(
        players=(p0, p1),
        turn_info=TurnInfo(
            turn_number=kw.get("turn", 2),
            active_player=kw.get("active", 0),
            phase=kw.get("phase", Phase.MAIN),
        ),
        attack_context=kw.get("attack_context"),
    )


def test_protection() -> int:
    failed = 0
    print("\n── PROTECTION (606.2) ──")
    fire_src = card(1, "Fire Source", civ=Civilization.FIRE)
    light_target = card(2, "Light Target", civ=Civilization.LIGHT)
    source = Creature(
        definition=fire_src, uid="fire-src", controller=0, owner=0, entered_turn=1,
    )
    protected = Creature(
        definition=light_target, uid="light-tgt", controller=1, owner=1, entered_turn=1,
    )
    protected.temp_flags["protection"] = {
        "from_civ": ["Fire"],
        "from_race": [],
        "duration": "until_end_of_turn",
    }
    state = bare_state(
        p0=PlayerState(player_index=0, player_name="P0", battle_zone=[source]),
        p1=PlayerState(player_index=1, player_name="P1", battle_zone=[protected]),
    )
    tr = trigger(
        EffectAction.DESTROY,
        source_uid="fire-src", source_card_id=1,
        target_uid="light-tgt",
    )
    _do_destroy(state, 0, tr)
    if not check("Fire destroy blocked by protection", len(state.players[1].battle_zone) == 1):
        failed += 1

    options = _collect_target_options(
        state, 0, "battle_zone", {"type": "creature"}, tr,
    )
    if not check("Protected creature excluded from targets", "light-tgt" not in options):
        failed += 1

    unprotected = Creature(
        definition=card(3, "Water Target", civ=Civilization.WATER),
        uid="water-tgt", controller=1, owner=1, entered_turn=1,
    )
    state2 = bare_state(
        p0=PlayerState(player_index=0, player_name="P0", battle_zone=[source]),
        p1=PlayerState(player_index=1, player_name="P1", battle_zone=[protected, unprotected]),
    )
    tr2 = trigger(
        EffectAction.DESTROY,
        source_uid="fire-src", source_card_id=1,
        target_uids=["light-tgt", "water-tgt"],
    )
    _do_destroy(state2, 0, tr2)
    if not check("Unprotected target still destroyed (101.3)", len(state2.players[1].battle_zone) == 1):
        failed += 1
    if state2.players[1].battle_zone and not check(
        "Remaining creature is protected one",
        state2.players[1].battle_zone[0].uid == "light-tgt",
    ):
        failed += 1
    return failed


def test_gain_control() -> int:
    failed = 0
    print("\n── GAIN_CONTROL + EOT revert ──")
    opp_creature = Creature(
        definition=card(10, "Opponent Creature"),
        uid="opp-c", controller=1, owner=1, entered_turn=1,
    )
    state = bare_state(
        p0=PlayerState(player_index=0, player_name="P0"),
        p1=PlayerState(player_index=1, player_name="P1", battle_zone=[opp_creature]),
    )
    _do_gain_control(state, 0, trigger(
        EffectAction.GAIN_CONTROL,
        target_uid="opp-c",
    ))
    if not check("Controller swapped to P0", state.players[0].battle_zone[0].controller == 0):
        failed += 1
    if not check("P1 battle zone empty", len(state.players[1].battle_zone) == 0):
        failed += 1

    _end_turn(state)
    if not check("EOT reverts to P1", state.players[1].battle_zone[0].controller == 1):
        failed += 1
    if not check("Creature back on P1 side", len(state.players[0].battle_zone) == 0):
        failed += 1
    return failed


def test_mandatory_actions() -> int:
    failed = 0
    print("\n── MUST_ATTACK / MUST_BLOCK / CANNOT_BLOCK ──")

    attacker = Creature(
        definition=card(20, "Must Attacker", power=3000),
        uid="must-atk", controller=0, owner=0, entered_turn=1,
        has_summoning_sickness=False,
    )
    optional = Creature(
        definition=card(21, "Optional Attacker", power=2000),
        uid="opt-atk", controller=0, owner=0, entered_turn=1,
        has_summoning_sickness=False,
    )
    state = bare_state(
        phase=Phase.ATTACK,
        p0=PlayerState(player_index=0, player_name="P0", battle_zone=[attacker, optional]),
        p1=PlayerState(player_index=1, player_name="P1"),
    )
    _do_must_attack(state, 0, trigger(EffectAction.MUST_ATTACK, target_uid="must-atk"))
    attack_actions = _generate_attack_declarations(state)
    attack_types = {a.action_type for a in attack_actions}
    if not check("Must-attack: no PASS in attack step", ActionType.PASS not in attack_types):
        failed += 1
    if attack_actions and not check(
        "Must-attack: only compelled creature attacks",
        all(getattr(a, "card_uid", None) == "must-atk" for a in attack_actions
            if a.action_type in (ActionType.ATTACK_PLAYER, ActionType.ATTACK_CREATURE)),
    ):
        failed += 1
    pass_atk = pass_attack(0)
    if not check("Must-attack: pass_attack illegal in executor", not _is_legal_action(state, pass_atk)):
        failed += 1

    blocker = Creature(
        definition=card(30, "Must Blocker", keywords=(Keyword.BLOCKER,)),
        uid="must-blk", controller=1, owner=1, entered_turn=1,
        has_summoning_sickness=False,
    )
    cannot_blk = Creature(
        definition=card(31, "No Block", keywords=(Keyword.BLOCKER,)),
        uid="no-blk", controller=1, owner=1, entered_turn=1,
        has_summoning_sickness=False,
    )
    atk = Creature(
        definition=card(32, "Attacker"),
        uid="atk-1", controller=0, owner=0, entered_turn=1,
    )
    tgt = Creature(
        definition=card(33, "Target"),
        uid="tgt-1", controller=1, owner=1, entered_turn=1, is_tapped=True,
    )
    block_state = bare_state(
        phase=Phase.BLOCK_DECLARE,
        active=0,
        p0=PlayerState(player_index=0, player_name="P0", battle_zone=[atk]),
        p1=PlayerState(player_index=1, player_name="P1", battle_zone=[blocker, cannot_blk, tgt]),
        attack_context=AttackContext(
            attacker_uid="atk-1", attacker_player=0,
            target_type="creature", target_uid="tgt-1",
        ),
    )
    _do_cannot_block(block_state, 1, trigger(
        EffectAction.CANNOT_BLOCK, target_uid="no-blk", controller=1,
    ))
    block_actions = _generate_block_actions(block_state)
    block_uids = {a.card_uid for a in block_actions if a.action_type == ActionType.DECLARE_BLOCKER}
    if not check("Cannot-block creature omitted from blockers", "no-blk" not in block_uids):
        failed += 1

    _do_must_block(block_state, 1, trigger(
        EffectAction.MUST_BLOCK, target_uid="must-blk", controller=1,
    ))
    pass_blk = pass_block(1)
    if not check("Must-block: pass_block illegal in executor", not _is_legal_action(block_state, pass_blk)):
        failed += 1
    return failed


def run_delegated_test(filename: str) -> bool:
    path = os.path.join(os.path.dirname(__file__), filename)
    print(f"\n── Delegated: {filename} ──")
    result = subprocess.run(
        [sys.executable, path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
    ok = result.returncode == 0
    check(f"{filename} passes", ok)
    return ok


def main() -> int:
    failed = 0
    print("\n" + "=" * 60)
    print("  TIER 3 EFFECT ACTIONS — INTEGRATION TESTS")
    print("=" * 60)

    failed += test_protection()
    failed += test_gain_control()
    failed += test_mandatory_actions()

    for delegated in (
        "test_tier3_zone_ops.py",
        "test_multicard_assembly.py",
        "test_shieldify.py",
        "test_select_mana_choice.py",
        "test_gods_core.py",
    ):
        if not run_delegated_test(delegated):
            failed += 1

    print("\n" + "=" * 60)
    if failed:
        print(f"  FAILED: {failed} check(s)")
    else:
        print("  ALL TIER 3 TESTS PASSED")
    print("=" * 60 + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
