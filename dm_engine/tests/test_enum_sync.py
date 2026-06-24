#!/usr/bin/env python3
"""Test that crawler SYSTEM_PROMPT enum values match engine enums exactly."""

import sys
import os
# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, "crawler"))

import re
from dm_engine.core.enums import EffectType, TriggerEvent, EffectAction, Zone, Phase


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


def extract_enums_from_prompt(prompt: str, field_name: str) -> set:
    """Extract enum values from SYSTEM_PROMPT for a given field."""
    # Pattern to match the enum list in the prompt
    pattern = rf'"{field_name}":\s*"<one of:\s*([^"]+)>"'
    match = re.search(pattern, prompt)
    if not match:
        return set()
    enum_str = match.group(1)
    return {e.strip() for e in enum_str.split("|")}


def test_effect_type_sync():
    """Test that effect_type enum values match."""
    from crawler.scripts.effect_parser import SYSTEM_PROMPT

    prompt_types = extract_enums_from_prompt(SYSTEM_PROMPT, "effect_type")
    engine_types = {e.value for e in EffectType}

    missing_in_prompt = engine_types - prompt_types
    extra_in_prompt = prompt_types - engine_types

    all_ok = check(
        "effect_type enum sync",
        len(missing_in_prompt) == 0 and len(extra_in_prompt) == 0,
        f"missing: {missing_in_prompt}, extra: {extra_in_prompt}"
    )

    if missing_in_prompt:
        print(f"  Engine has but prompt missing: {missing_in_prompt}")
    if extra_in_prompt:
        print(f"  Prompt has but engine missing: {extra_in_prompt}")

    return all_ok


def test_trigger_event_sync():
    """Test that trigger_event enum values match."""
    from crawler.scripts.effect_parser import SYSTEM_PROMPT

    prompt_events = extract_enums_from_prompt(SYSTEM_PROMPT, "trigger_event")
    engine_events = {e.value for e in TriggerEvent}

    missing_in_prompt = engine_events - prompt_events
    extra_in_prompt = prompt_events - engine_events

    all_ok = check(
        "trigger_event enum sync",
        len(missing_in_prompt) == 0 and len(extra_in_prompt) == 0,
        f"missing: {missing_in_prompt}, extra: {extra_in_prompt}"
    )

    if missing_in_prompt:
        print(f"  Engine has but prompt missing: {missing_in_prompt}")
    if extra_in_prompt:
        print(f"  Prompt has but engine missing: {extra_in_prompt}")

    return all_ok


def test_effect_action_sync():
    """Test that effect_action enum values match."""
    from crawler.scripts.effect_parser import SYSTEM_PROMPT

    prompt_actions = extract_enums_from_prompt(SYSTEM_PROMPT, "effect_action")
    engine_actions = {e.value for e in EffectAction}

    missing_in_prompt = engine_actions - prompt_actions
    extra_in_prompt = prompt_actions - engine_actions

    all_ok = check(
        "effect_action enum sync",
        len(missing_in_prompt) == 0 and len(extra_in_prompt) == 0,
        f"missing: {len(missing_in_prompt)}, extra: {len(extra_in_prompt)}"
    )

    if missing_in_prompt:
        print(f"  Engine has but prompt missing: {sorted(missing_in_prompt)}")
    if extra_in_prompt:
        print(f"  Prompt has but engine missing: {sorted(extra_in_prompt)}")

    return all_ok


def test_zone_sync():
    """Test that active_in_zone values match Zone enum."""
    from crawler.scripts.effect_parser import VALID_ZONES

    engine_zones = {z.value for z in Zone}

    missing_in_prompt = engine_zones - VALID_ZONES
    extra_in_prompt = VALID_ZONES - engine_zones

    all_ok = check(
        "zone enum sync",
        len(missing_in_prompt) == 0 and len(extra_in_prompt) == 0,
        f"missing: {missing_in_prompt}, extra: {extra_in_prompt}"
    )

    if missing_in_prompt:
        print(f"  Engine has but VALID_ZONES missing: {missing_in_prompt}")
    if extra_in_prompt:
        print(f"  VALID_ZONES has but engine missing: {extra_in_prompt}")

    return all_ok


def test_phase_sync():
    """Test that active_in_phase values match Phase enum."""
    from crawler.scripts.effect_parser import VALID_PHASES

    # Engine phases (main phases + attack sub-phases)
    # "any" is a special crawler value meaning "all phases"
    engine_phases = {p.name.lower() for p in Phase}
    engine_phases_with_any = engine_phases | {"any"}

    missing_in_prompt = engine_phases_with_any - VALID_PHASES
    extra_in_prompt = VALID_PHASES - engine_phases_with_any

    all_ok = check(
        "phase enum sync",
        len(missing_in_prompt) == 0 and len(extra_in_prompt) == 0,
        f"missing: {missing_in_prompt}, extra: {extra_in_prompt}"
    )

    if missing_in_prompt:
        print(f"  Engine has but VALID_PHASES missing: {missing_in_prompt}")
    if extra_in_prompt:
        print(f"  VALID_PHASES has but engine missing: {extra_in_prompt}")

    return all_ok


if __name__ == "__main__":
    results = []
    results.append(test_effect_type_sync())
    results.append(test_trigger_event_sync())
    results.append(test_effect_action_sync())
    results.append(test_zone_sync())
    results.append(test_phase_sync())

    if all(results):
        print("\n✅ All enum sync tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some enum sync tests failed!")
        sys.exit(1)
