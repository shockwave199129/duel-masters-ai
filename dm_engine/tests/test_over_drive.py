#!/usr/bin/env python3
"""Tests for Over Drive (Rule 112.2d)."""

import sys
sys.path.insert(0, "dm_engine")

from core.enums import Phase, Civilization, CardType, CardSubtype, Keyword
from core.state import GameState, TurnInfo, PlayerState
from core.zones import Creature, ManaCard, HandCard
from core.cards import CardDefinition
from core.actions import use_over_drive, ManaUsage
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions, _get_over_drive_requirements, _over_drive_mana_combos


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


class FakeEffect:
    def __init__(self, raw_text: str):
        self.raw_text = raw_text

    def is_activated(self) -> bool:
        return False

    def is_triggered(self) -> bool:
        return False

    def is_static(self) -> bool:
        return False


def card(cid, name, cost=1, card_type=CardType.CREATURE, power=1000,
         civilizations=None, keywords=frozenset(), effects=tuple()):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=cost,
        power=power, card_type=card_type, card_subtype=CardSubtype.NONE,
        civilizations=frozenset(civilizations or [Civilization.FIRE]),
        races=frozenset(), keywords=keywords, effects=effects,
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


class MockCardDef:
    def __init__(self, effects):
        self.effects = effects


def make_state() -> GameState:
    filler = card(99, "Filler")
    return GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", deck=[filler]),
            PlayerState(player_index=1, player_name="P1", deck=[filler]),
        ),
        turn_info=TurnInfo(turn_number=1, active_player=0, phase=Phase.MAIN, first_player=0),
    )


print("=" * 70)
print("Testing Over Drive Mechanic (Rule 112.2d)")
print("=" * 70)

print("\n--- Section 1: Over Drive regex parsing ---")
tests = [
    ("Fire x1", {Civilization.FIRE: 1}),
    ("Water x1 Darkness x1 Fire x1", {
        Civilization.WATER: 1,
        Civilization.DARKNESS: 1,
        Civilization.FIRE: 1,
    }),
]
for text, expected in tests:
    full_text = f"■ Over Drive—{text} (When you summon this creature..."
    mock_def = MockCardDef([FakeEffect(full_text)])
    result = _get_over_drive_requirements(mock_def)
    check(f"Parsing '{text}'", result == expected, f"got {result}")

print("\n--- Section 2: Multi-civ mana combos ---")
mana_defs = [
    card(1, "F", civilizations=[Civilization.FIRE]),
    card(2, "W", civilizations=[Civilization.WATER]),
    card(3, "D", civilizations=[Civilization.DARKNESS]),
]
mana_zone = [ManaCard(definition=d) for d in mana_defs]
combos = _over_drive_mana_combos(
    mana_zone,
    {Civilization.WATER: 1, Civilization.DARKNESS: 1, Civilization.FIRE: 1},
)
check("Multi-civ Over Drive has valid combo", len(combos) == 1)
check("Combo taps three distinct mana cards", len(combos[0]) == 3)

print("\n--- Section 3: USE_OVER_DRIVE action ---")
action = use_over_drive(
    player=0,
    creature_uid="test123",
    creature_id=1,
    mana_used=[ManaUsage(mana_uid="mana1", used_for_civ=Civilization.FIRE)],
)
check("USE_OVER_DRIVE action type", action.action_type.value == "use_over_drive")
check("USE_OVER_DRIVE is not free execution", not action.is_free_execution())

print("\n--- Section 4: Over Drive execution ---")
state = make_state()
od_effects = (FakeEffect("■ Over Drive—Fire x1 (When you summon this creature..."),)
od_def = card(100, "Test Over Drive", cost=3, effects=od_effects)
fire_mana = ManaCard(definition=card(50, "Mana Fire", civilizations=[Civilization.FIRE]))
state.players[0].mana_zone = [fire_mana]
creature = Creature(
    definition=od_def,
    uid="creature123",
    controller=0,
    owner=0,
    entered_turn=state.turn_number,
    has_summoning_sickness=True,
)
state.players[0].battle_zone.append(creature)

od_actions = [a for a in get_legal_actions(state) if a.action_type.value == "use_over_drive"]
check("Over Drive action generated after summon", len(od_actions) > 0)

if od_actions:
    after = execute_action(state, od_actions[0], validate=False)
    updated = after.players[0].find_creature("creature123")
    check("Over Drive flag set", updated.temp_flags.get("over_drive_used") is True)
    check("Over Drive bonus active", updated.temp_flags.get("over_drive_active") is True)
    check("Mana tapped", after.players[0].mana_zone[0].is_tapped)

    od_after = [a for a in get_legal_actions(after) if a.action_type.value == "use_over_drive"]
    check("Over Drive not offered twice", len(od_after) == 0)

print("\n" + "=" * 70)
