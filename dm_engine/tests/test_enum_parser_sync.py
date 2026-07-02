"""Standalone test checking that the enum sets in the engine match the validation whitelists in the crawler effect parser.

The test is deliberately simple – it runs as a normal Python script (no pytest
required) and prints PASS/FAIL lines.  If any enum value is missing from the
corresponding whitelist (or vice‑versa), the script will exit with status 1.
"""

import sys
import os
from pathlib import Path

# Ensure the crawler scripts directory is on the import path so we can import
# the whitelist constants.
# Ensure the engine package and the crawler scripts directory are on the import path.
engine_path = Path(__file__).resolve().parents[2] / "dm_engine"
sys.path.append(str(engine_path))

crawler_scripts_path = Path(__file__).resolve().parents[2] / "crawler" / "scripts"
sys.path.append(str(crawler_scripts_path))

# Import the enums from the engine.
from core.enums import (
    EffectAction,
    EffectType,
    TriggerEvent,
    Zone,
    Phase,
)

# Add the repository root (so that "scripts" resolves to crawler/scripts)
repo_root = Path(__file__).resolve().parents[2]
crawler_path = repo_root / "crawler"
sys.path.append(str(crawler_path))# Import the whitelist sets from the parser module.
from effect_parser import (
    VALID_EFFECT_ACTIONS,
    VALID_EFFECT_TYPES,
    VALID_TRIGGER_EVENTS,
    VALID_ZONES,
    VALID_PHASES,
)


def _check_enum_vs_set_names(name_set, whitelist_set, name):
    """Compare a pre‑computed *string* set (e.g. Phase member names) against a whitelist.
    Returns True on exact match, prints PASS/FAIL and returns a bool.
    """
    enum_vals = set(name_set)
    whitelist_vals = set(whitelist_set)
    missing = enum_vals - whitelist_vals
    extra = whitelist_vals - enum_vals
    if missing:
        print(f"❌ FAIL: {name} – values missing from whitelist: {sorted(missing)}")
        return False
    if extra:
        print(f"❌ FAIL: {name} – extra entries in whitelist not in enum: {sorted(extra)}")
        return False
    print(f"✅ PASS: {name} – enum and whitelist are in sync")
    return True
def _check_enum_vs_set(enum_cls, whitelist_set, name):
    # Enum values are the lower‑case strings stored in .value
    enum_vals = {member.value for member in enum_cls}
    whitelist_vals = set(whitelist_set)
    missing = enum_vals - whitelist_vals
    extra = whitelist_vals - enum_vals
    if missing:
        print(f"❌ FAIL: {name} – values missing from whitelist: {sorted(missing)}")
        return False
    if extra:
        print(f"❌ FAIL: {name} – extra entries in whitelist not in enum: {sorted(extra)}")
        return False
    print(f"✅ PASS: {name} – enum and whitelist are in sync")
    return True


def main():
    ok = True
    ok &= _check_enum_vs_set(EffectAction, VALID_EFFECT_ACTIONS, "EffectAction")
    ok &= _check_enum_vs_set(EffectType, VALID_EFFECT_TYPES, "EffectType")
    ok &= _check_enum_vs_set(TriggerEvent, VALID_TRIGGER_EVENTS, "TriggerEvent")
    ok &= _check_enum_vs_set(Zone, VALID_ZONES, "Zone")
    # Phase enum stores numeric values; compare its *names* (lower‑snake) with the whitelist.
    phase_names = {member.name.lower() for member in Phase}
    # "any" is a sentinel used in the engine for unrestricted phases – ignore it for the sync check.
    phase_whitelist = set(VALID_PHASES) - {"any"}
    ok &= _check_enum_vs_set_names(phase_names, phase_whitelist, "Phase")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
