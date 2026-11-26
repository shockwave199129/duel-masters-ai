"""
Measure the card text translator (core/card_text.py). Reads the database and
never writes to it. The engine does not use the translator yet.

    python dm_engine/scripts/translate_cards.py --decks          compare with the hand scripts
    python dm_engine/scripts/translate_cards.py --all            coverage of the whole database
    python dm_engine/scripts/translate_cards.py --all --sample 40   print some to review
    python dm_engine/scripts/translate_cards.py --run FILE       run a script or test with the
                                                                 translations replacing the
                                                                 hand scripts (needs --decks first)
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import pickle
import random
import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "dm_engine"))
REPORTS = REPO / "data" / "reports" / "card_text"
SWAP = REPORTS / "translations.pickle"
DECKS = (1, 2, 3, 4)

import core.card_scripts as hand  # noqa: E402  (must be imported before any engine module)

SET_TABLES = (
    "OPPONENT_CANT_CAST", "WEAKER_CANT_ATTACK_YOU", "NEW_CREATURES_CANT_ATTACK_YOU",
    "OPPONENT_MANA_ENTERS_TAPPED", "GRAVEYARD_CREATURES_RUSH", "DESTROYS_UNSUMMONED",
    "ELEMENTS_ONLY_PLAYED_FROM_HAND", "SUMMON_FROM_GRAVEYARD", "SUMMON_FROM_MANA",
    "PUT_TRIGGER_SUPPRESSORS", "ENTERS_TAPPED", "EX_LIFE", "NAMES_TO_IGNORE",
    "DOES_NOT_UNTAP", "BREAKS_TO_GRAVEYARD", "ETERNAL_OMEGA",
    "OPPONENT_CREATURES_ENTER_TAPPED", "UNSUMMONED_TO_MANA",
)
DICT_TABLES = (
    "POWER_PER_OPPONENT_SPELL", "BREAKER_COUNTS", "BIG_SUMMON_DISCOUNT",
    "GRAVEYARD_SELF_DISCOUNT", "CREATURE_ONLY_WITH_DARKNESS", "HAND_AT_OPPONENT_TURN_END",
    "SUMMON_FROM_MANA_GRANTS", "ABYSS_RUSH_GRANTS", "GRAVEYARD_SUMMON_DISCOUNT",
    "NEO_EVOLUTION", "POWER_ATTACKERS", "CHOICE_PROTECTION", "HYPER_MODE", "GRANTS",
    "OPPONENT_RACES_TO_GRAVEYARD", "EVOLUTION_BASE_RACE", "ATTACK_CHANCE", "RECYCLE",
    "COUNTERATTACK", "CIVIL_COUNT_SHIELD_TRIGGER", "FUSHIGIVERSE", "TWINPACT_SPELLS",
    "LEAVE_REPLACEMENTS", "LOSS_REPLACEMENTS",
)
SPECIAL_KEYS = {
    hand.EX_LIFE_SHIELD_LEFT_KEY, hand.FREE_PLAY_KEY, hand.HAND_TURN_END_KEY, "twin_spell",
    "left_under", "face_up_self", "face_up_shield", "opponent_discarded_spell",
    "instead_of_discard",
}
STEP_DEFAULTS = {
    "deck_to_mana": {"count": 1, "tapped": False},
    "destroy": {"max_cost": None, "max_power": None, "optional": False, "tamaseeds": False,
                "then_if_destroyed": []},
    "reanimate": {"max_cost": None, "optional": False, "except_name": None},
}


# ── Reading card text ───────────────────────────────────────────────────────

def _database():
    env = REPO / "crawler" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    import psycopg2
    return psycopg2.connect(os.environ["DATABASE_URL"]).cursor()


def _civilizations(value) -> frozenset:
    from core.enums import Civilization
    words = " ".join(value) if isinstance(value, list) else str(value or "")
    return frozenset(c for c in Civilization if c.value.lower() in words.lower())


def _number(value, default=0) -> int:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(digits) if digits else default


def faces_from(raw_text: str, card_type: str):
    """The sides of a card, from its stored text."""
    from core.card_text import Face

    stored = json.loads(raw_text) if raw_text else {}
    fields = stored.get("fields") or {}
    hyper = _number(fields.get("hyperpower")) or None
    sides = stored.get("faces") or [stored]
    faces = [
        Face(" ".join(side.get("abilities") or []), side.get("card_type") or card_type,
             _number(side.get("cost")), _civilizations(side.get("civilizations")), hyper)
        for side in sides
    ]
    for face in faces[1:]:
        face.civilizations = face.civilizations or faces[0].civilizations
    return faces


def read_cards(deck_only: bool):
    from core.card_text import translate

    cursor = _database()
    cursor.execute("SELECT DISTINCT race FROM card_races")
    races = frozenset(row[0] for row in cursor.fetchall() if row[0])
    query = "SELECT c.slug, c.raw_text, c.card_type FROM cards c"
    if deck_only:
        query += (" WHERE c.id IN (SELECT card_id FROM training_deck_cards"
                  f" WHERE deck_id IN {DECKS})")
    cursor.execute(query + " ORDER BY c.slug")
    for slug, raw_text, card_type in cursor.fetchall():
        try:
            faces = faces_from(raw_text, card_type or "Creature")
        except (ValueError, KeyError, AttributeError):
            faces = []
        yield slug, faces, translate(slug, faces, races)


# ── Comparing with the hand-written data ────────────────────────────────────

def _normal_steps(steps, mode_tables) -> list:
    normal = []
    for operation, params in steps:
        params = {**STEP_DEFAULTS.get(operation, {}), **params}
        params.pop("prompt", None)
        if operation == "modes":
            table = mode_tables[params.pop("table")]
            params["modes"] = [_normal_steps(mode, mode_tables) for mode in table.values()]
        for key in ("then", "then_if_destroyed"):
            if key in params:
                params[key] = _normal_steps(params[key], mode_tables)
        normal.append((operation, sorted(params.items(), key=repr)))
    return normal


def _normal_abilities(abilities, mode_tables) -> list:
    return sorted(
        (repr((a.key if a.key in SPECIAL_KEYS else None, a.event.name, a.watch,
               _normal_steps(a.steps, mode_tables))) for a in abilities)
    )


def hand_tables(slug: str) -> dict:
    tables = {}
    for name in SET_TABLES:
        if slug in getattr(hand, name):
            tables[name] = True
    for name in DICT_TABLES:
        if slug in getattr(hand, name):
            tables[name] = getattr(hand, name)[slug]
    return tables


def differences(slug: str, result) -> list[str]:
    """How a translation differs from the hand-written data for the card."""
    found = []
    mine = _normal_abilities(hand.SCRIPTS.get(slug, ()), hand.MODE_TABLES)
    theirs = _normal_abilities(result.abilities, result.modes)
    if mine != theirs:
        found += [f"hand only: {a}" for a in mine if a not in theirs]
        found += [f"translation only: {a}" for a in theirs if a not in mine]
    expected, got = hand_tables(slug), result.tables
    for name in sorted(set(expected) | set(got)):
        if expected.get(name) != got.get(name):
            found.append(f"{name}: hand={expected.get(name)} translation={got.get(name)}")
    missing = set(hand.EXTRA_KEYWORDS.get(slug, ())) - result.keywords
    if missing:
        found.append(f"keywords not found: {sorted(k.value for k in missing)}")
    return found


# ── Using translations instead of the hand scripts ──────────────────────────

def install(swaps: dict) -> None:
    """Delete the hand data for each card and put the translation in its place."""
    sets = {name: set(getattr(hand, name)) for name in SET_TABLES}
    dicts = {name: dict(getattr(hand, name)) for name in DICT_TABLES}
    for slug, result in swaps.items():
        old_modes = []  # the tests name modes as the hand scripts do
        for ability in hand.SCRIPTS.get(slug, ()):
            old_modes += [list(hand.MODE_TABLES[p["table"]]) for op, p in ability.steps
                          if op == "modes"]
        for index, (name, table) in enumerate(result.modes.items()):
            ids = old_modes[index] if index < len(old_modes) else list(table)
            hand.MODE_TABLES[name] = dict(zip(ids, table.values()))
        hand.SCRIPTS.pop(slug, None)
        for members in sets.values():
            members.discard(slug)
        for entries in dicts.values():
            entries.pop(slug, None)
        if result.abilities or result.tables or slug in hand.EXTRA_KEYWORDS:
            hand.SCRIPTS[slug] = tuple(result.abilities)
        for name, value in result.tables.items():
            if name in sets:
                sets[name].add(slug)
            else:
                dicts[name][slug] = value
        hand.EXTRA_KEYWORDS[slug] = tuple(result.keywords)
    for name, members in sets.items():
        setattr(hand, name, frozenset(members))
    for name, entries in dicts.items():
        setattr(hand, name, entries)


# ── Reports ─────────────────────────────────────────────────────────────────

def report_decks(verbose: bool) -> None:
    swaps, same, differ, untranslated = {}, [], {}, {}
    for slug, _faces, result in read_cards(deck_only=True):
        if not result.complete:
            untranslated[slug] = result.unknown
            continue
        swaps[slug] = result
        found = differences(slug, result)
        if found:
            differ[slug] = found
        else:
            same.append(slug)
    total = len(swaps) + len(untranslated)
    print(f"Deck cards: {total}")
    print(f"  translated completely: {len(swaps)}")
    print(f"    identical to the hand data: {len(same)}")
    print(f"    different from the hand data: {len(differ)}")
    print(f"  not translated: {len(untranslated)}")
    for slug, reasons in untranslated.items():
        print(f"\nNOT TRANSLATED {slug}")
        for reason in reasons:
            print(f"   {reason}")
    for slug, found in differ.items():
        print(f"\nDIFFERENT {slug}")
        for line in found if verbose else found[:6]:
            print(f"   {line}")
    REPORTS.mkdir(parents=True, exist_ok=True)
    SWAP.write_bytes(pickle.dumps(swaps))


def report_all(sample: int, seed: int) -> None:
    cursor = _database()
    cursor.execute("SELECT DISTINCT c.slug FROM cards c JOIN training_deck_cards d "
                   f"ON d.card_id = c.id WHERE d.deck_id IN {DECKS}")
    deck_slugs = {row[0] for row in cursor.fetchall()}
    counts = collections.Counter()
    users: dict[str, set] = collections.defaultdict(set)
    reviewable = []
    for slug, faces, result in read_cards(deck_only=False):
        outside = slug not in deck_slugs
        group = "other" if outside else "deck"
        if not any(face.text.strip() for face in faces):
            counts[group, "no ability text"] += 1
        elif result.unknown and any("cut off" in u for u in result.unknown):
            counts[group, "stored text cut off"] += 1
        elif not result.complete:
            counts[group, "not translated"] += 1
        elif result.abilities or result.tables:
            counts[group, "translated: has abilities beyond plain keywords"] += 1
            if outside:
                reviewable.append((slug, faces, result))
        else:
            counts[group, "translated: plain keywords only"] += 1
        if result.complete and outside:
            for rule in result.rules_used:
                users[rule].add(slug)
    for group in ("deck", "other"):
        total = sum(n for (g, _), n in counts.items() if g == group)
        print(f"\n{'Deck cards' if group == 'deck' else 'All other cards'}: {total}")
        for (g, label), number in sorted(counts.items()):
            if g == group:
                print(f"  {label:<50} {number:>6}  {number / total:6.1%}")
    from core.card_text import _EFFECT_RULES, _STATIC_RULES, _TRIGGERS
    names = {r[0] for r in _EFFECT_RULES} | {r[0] for r in _STATIC_RULES}
    names |= {f"when:{t[0]}" for t in _TRIGGERS}
    reuse = {name: len(users.get(name, ())) for name in names}
    print(f"\nRules: {len(reuse)}. In translated cards outside the decks they are used by:")
    for label, test in (("no card", lambda n: n == 0), ("1-4 cards", lambda n: 1 <= n <= 4),
                        ("5-19 cards", lambda n: 5 <= n <= 19), ("20+ cards", lambda n: n >= 20)):
        print(f"  {label:<10} {sum(1 for n in reuse.values() if test(n))} rules")
    if sample:
        chosen = random.Random(seed).sample(reviewable, min(sample, len(reviewable)))
        for slug, faces, result in chosen:
            print(f"\n== {slug}")
            for face in faces:
                print("  TEXT:", " ".join(face.text.split())[:500])
            for ability in result.abilities:
                steps = ability.steps
                print(f"  -> {ability.event.name}/{ability.watch}: {steps}")
            for name, table in result.modes.items():
                print(f"  -> modes {table}")
            for name, value in result.tables.items():
                print(f"  -> {name} = {value}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure the card text translator")
    parser.add_argument("--decks", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--sample", type=int, default=0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--run", help="run a Python file with translations in place of hand data")
    args = parser.parse_args()
    if args.run:
        swaps = pickle.loads(SWAP.read_bytes())  # written by --decks on this machine
        install(swaps)
        sys.argv = [args.run]
        runpy.run_path(args.run, run_name="__main__")
        return 0
    if args.decks:
        report_decks(args.verbose)
    if args.all:
        report_all(args.sample, args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
