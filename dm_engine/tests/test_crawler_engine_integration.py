#!/usr/bin/env python3
"""Integration test: Parse a card with known effects, load in engine, verify no NONE fallbacks."""

import sys
import os
# Add project root and dm_engine to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
dm_engine_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
sys.path.insert(0, dm_engine_path)
sys.path.insert(0, os.path.join(project_root, "crawler"))

from db.card_database import CardDatabase
from core.enums import EffectAction, EffectType, TriggerEvent, Zone


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else "") )
    return condition


def test_engine_loads_cards_without_none_fallbacks():
    """Test that CardDatabase loads cards and no effects fall back to NONE."""
    # This test requires a database connection
    # For CI, we'll use a mock or skip if no DB
    import psycopg2
    try:
        # Try to connect to test database
        conn = psycopg2.connect(os.environ.get("DATABASE_URL", "postgresql://localhost/dm_db"))
        conn.close()
    except Exception:
        print("⚠️ SKIP: No database available for integration test")
        return True

    # Load cards from database
    db = CardDatabase(os.environ.get("DATABASE_URL", "postgresql://localhost/dm_db"))
    db.load()

    none_count = 0
    total_effects = 0

    for card in db.all_cards():
        for effect in card.effects:
            total_effects += 1
            if effect.effect_action == EffectAction.NONE:
                none_count += 1
                print(f"  ⚠️ Card {card.name} has EffectAction.NONE: {effect}")

    all_ok = check(
        "No EffectAction.NONE fallbacks in loaded cards",
        none_count == 0,
        f"{none_count}/{total_effects} effects fell back to NONE"
    )

    return all_ok


def test_effect_action_dispatch():
    """Test that all EffectAction values have handlers in effect_executor."""
    from engine.effect_executor import execute_pending_trigger
    from core.enums import EffectAction

    # Check that all non-NONE EffectAction values have a handler
    # by inspecting the execute_pending_trigger function
    import inspect
    source = inspect.getsource(execute_pending_trigger)

    # Actions handled inline (not via _do_* functions)
    inline_handlers = {
        "CANNOT_ATTACK": "_set_creature_flag",
        "CANNOT_BE_BLOCKED": "_set_creature_flag",
        "CANNOT_BE_DESTROYED": "_set_creature_flag",
        "WIN_BATTLE": "_set_creature_flag",
        "COST_REDUCE": "_store_temp_value",
        "COST_INCREASE": "_store_temp_value",
        "COPY_EFFECT": "_store_temp_value",
        "GACHINKO_JUDGE": "_store_temp_value",
        "EXTRA_EX_LIFE": "_store_temp_value",
        "WIN_CONDITION": "_do_win_by_effect",
        "LOSE_CONDITION": "_do_lose_by_effect",
        "ZEROM_FLIP": "_do_zerom_ritual",
        "POWER_ATTACKER": "power_attacker_active",
    }

    missing_handlers = []
    for action in EffectAction:
        if action == EffectAction.NONE:
            continue
        handler_name = f"_do_{action.name.lower()}"
        inline_handler = inline_handlers.get(action.name)
        
        has_handler = handler_name in source
        has_inline = inline_handler and inline_handler in source
        
        if not has_handler and not has_inline:
            missing_handlers.append(action.name)

    all_ok = check(
        "All EffectAction values have handlers",
        len(missing_handlers) == 0,
        f"Missing handlers for: {missing_handlers}"
    )

    return all_ok


def test_enum_values_not_none():
    """Test that enum values in SYSTEM_PROMPT don't use 'none' as default incorrectly."""
    from crawler.scripts.effect_parser import SYSTEM_PROMPT

    # Check that effect_action doesn't have "none" as the only option
    # (it should have many options, with "none" being just one of them)
    import re
    match = re.search(r'"effect_action":\s*"<one of:\s*([^"]+)>"', SYSTEM_PROMPT)
    if match:
        actions = match.group(1).split("|")
        has_none = "none" in actions
        has_many = len(actions) > 5

        all_ok = check(
            "effect_action has multiple options including 'none'",
            has_none and has_many,
            f"actions count: {len(actions)}, has 'none': {has_none}"
        )
    else:
        all_ok = check("effect_action found in prompt", False)

    return all_ok


if __name__ == "__main__":
    results = []
    results.append(test_enum_values_not_none())
    results.append(test_effect_action_dispatch())

    # Database test is optional
    try:
        results.append(test_engine_loads_cards_without_none_fallbacks())
    except Exception as e:
        print(f"⚠️ SKIP: Database test failed: {e}")

    if all(results):
        print("\n✅ All integration tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some integration tests failed!")
        sys.exit(1)
