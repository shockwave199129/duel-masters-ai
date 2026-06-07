"""
tests/test_trigger_effect_resolvers.py — trigger ordering and simple effects.
"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.cards import CardDefinition, CardEffect
from core.enums import (
    CardSubtype, CardType, Civilization, EffectAction, EffectType,
    Phase, TriggerEvent,
)
from core.player_state import PlayerState
from core.state import GameState, PendingTrigger, TurnInfo
from engine.trigger_resolver import order_simultaneous_triggers, resolve_pending_triggers

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))


def card(cid, name):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=1, power=1000,
        card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]), races=frozenset(),
        keywords=frozenset(), effects=tuple(),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


def draw_effect(cid):
    return CardEffect(
        card_id=cid, ability_index=0, raw_text="draw",
        effect_type=EffectType.TRIGGERED, trigger_event=TriggerEvent.ON_SUMMON,
        effect_action=EffectAction.DRAW,
        trigger_condition={}, effect_target={}, effect_value={"amount": 1},
        is_optional=False, is_replacement=False,
        active_in_phase=tuple(), active_in_zone=tuple(), parse_confidence=1.0,
    )


print("\n" + "═"*60)
print("  DM ENGINE — TRIGGER / EFFECT TESTS")
print("═"*60)

eff0 = draw_effect(1)
eff1 = draw_effect(2)
t0 = PendingTrigger(eff0, "src0", 1, 0)
t1 = PendingTrigger(eff1, "src1", 2, 1)
ordered = order_simultaneous_triggers([t1, t0], turn_player=0)
check("Turn-player trigger ordered first", ordered[0].controller == 0)

c = card(3, "drawn")
state = GameState(
    players=(
        PlayerState(player_index=0, player_name="P0", deck=[c]),
        PlayerState(player_index=1, player_name="P1"),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)
state.effect_stack.add_trigger(t0)
after = resolve_pending_triggers(state)
check("Draw effect adds card to hand", len(after.players[0].hand) == 1)
check("Draw effect removes card from deck", after.players[0].deck_size == 0)

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
