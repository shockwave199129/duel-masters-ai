"""
Repair stored card text by reading the card pages again.

Only the ability text changes (cards.raw_text and cards.faces). Costs, types,
keywords, parsed effects and everything else are left as they are.

Run from the project root, in this order:

    python crawler/scripts/refresh_card_text.py fetch --decks 1 2 3 4 --sample 100
    python crawler/scripts/refresh_card_text.py compare
    python crawler/scripts/refresh_card_text.py apply
    python crawler/scripts/refresh_card_text.py restore data/reports/card_text_refresh/backup_X.json

``fetch`` keeps each page it reads, so it can be stopped and resumed, and
``compare`` can be repeated without reading the site again. ``compare`` writes
nothing to the database. A card is held back for review if its new text would
lose anything the stored text has.
"""

from __future__ import annotations

import argparse
import collections
import copy
import gzip
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "crawler"))
WORK = REPO / "data" / "reports" / "card_text_refresh"   # ignored by git
PAGES = WORK / "pages"
REPORT = WORK / "compare.json"
MAX_FAILURES_IN_A_ROW = 5

logger = logging.getLogger("refresh_card_text")


def load_env() -> None:
    env = REPO / "crawler" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    import psycopg2
    load_env()
    return psycopg2.connect(os.environ["DATABASE_URL"])


# ── Fetch ───────────────────────────────────────────────────────────────────

def page_path(card_id: int) -> Path:
    return PAGES / f"{card_id}.html.gz"


def choose_cards(cursor, args) -> list[tuple[int, str]]:
    """(id, url) of the cards asked for."""
    chosen: dict[int, str] = {}
    base = "SELECT id, source_url FROM cards WHERE coalesce(source_url, '') <> ''"
    if args.all:
        cursor.execute(base + " ORDER BY id")
        return cursor.fetchall()
    if args.slugs:
        cursor.execute(base + " AND slug = ANY(%s)", (args.slugs,))
        chosen.update(cursor.fetchall())
    if args.decks:
        cursor.execute(base + " AND id IN (SELECT card_id FROM training_deck_cards"
                       " WHERE deck_id = ANY(%s))", (args.decks,))
        chosen.update(cursor.fetchall())
    if args.sample:
        cursor.execute(base + " ORDER BY id")
        others = [row for row in cursor.fetchall() if row[0] not in chosen]
        picked = random.Random(args.seed).sample(others, min(args.sample, len(others)))
        chosen.update(picked)
    return sorted(chosen.items())


def fetch(args) -> int:
    from main import _make_session
    import scripts.cf_cookies as cf_cookies
    from scripts.scraper import _fetch, _polite_delay

    cf_cookies._CACHE["attempted"] = True  # plain requests; no browser step
    cursor = connect().cursor()
    wanted = choose_cards(cursor, args)
    todo = [(card_id, url) for card_id, url in wanted if not page_path(card_id).exists()]
    print(f"{len(wanted)} cards chosen, {len(wanted) - len(todo)} already saved", flush=True)
    PAGES.mkdir(parents=True, exist_ok=True)
    session = _make_session()
    failures_in_a_row = failed = 0
    started = time.time()
    for number, (card_id, url) in enumerate(todo, 1):
        html = _fetch(url, session, retries=2)
        if html and "wikitable" in html or html and "portable-infobox" in html:
            page_path(card_id).write_bytes(gzip.compress(html.encode("utf-8")))
            failures_in_a_row = 0
        else:
            failed += 1
            failures_in_a_row += 1
            print(f"no card page: {url}", flush=True)
            if failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
                # The site is refusing or unavailable: stop, do not push on
                print("Stopping: too many pages in a row could not be read.", flush=True)
                return 2
        if number % 100 == 0 or number == len(todo):
            rate = (time.time() - started) / number
            left = rate * (len(todo) - number) / 60
            print(f"{number}/{len(todo)} read, {failed} failed, about {left:.0f} min left",
                  flush=True)
        _polite_delay()
    return 0


# ── Compare ─────────────────────────────────────────────────────────────────

def plain(text: str) -> str:
    """Words only, so wording can be compared whatever the markup."""
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\{\{[^}]*\}\}|\{\w+\}", " ", text)   # templates and cost icons
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("[[", " ").replace("]]", " ")
    return " ".join(re.findall(r"[a-z0-9+\-]+", text.lower()))


def sentences(abilities: list[str]) -> list[str]:
    found = []
    for ability in abilities:
        # Reminder text may legitimately differ, so it is not compared
        body = re.sub(r"''\(.*?\)''|\([^()]*\)", " ", ability)
        body = re.sub(r"\[\[[^\]]*$", " ", body)          # a cut-off link at the end
        found += [p for p in (plain(s) for s in re.split(r"■|►|▶|----|(?<=\.)\s", body)) if p]
    return found


def all_abilities(data: dict) -> list[str]:
    faces = data.get("faces") or []
    found = [a for face in faces for a in face.get("abilities") or []]
    return found or list(data.get("abilities") or [])


def is_cut(abilities: list[str]) -> bool:
    return any(a.count("[[") != a.count("]]") for a in abilities)


def merge(stored: dict, card) -> dict:
    """The stored record with its ability text replaced by the freshly read one."""
    merged = copy.deepcopy(stored)
    fresh = card.faces or [{"abilities": card.abilities}]
    old_faces = merged.get("faces") or []
    if old_faces or len(fresh) > 1:
        faces = []
        for index, new_face in enumerate(fresh):
            if index < len(old_faces):
                face = old_faces[index]
                face["abilities"] = new_face["abilities"]
                if not face.get("civilizations"):
                    face["civilizations"] = new_face.get("civilizations", [])
                if index and new_face.get("name"):
                    # A side that was a copy of the first gets its own details
                    for key in ("name", "card_type", "card_subtype", "cost", "power",
                                "civilizations"):
                        if key in new_face:
                            face[key] = new_face[key]
            else:
                face = {k: v for k, v in new_face.items() if k != "fields"}
            english = (new_face.get("fields") or {}).get("english text")
            fields = face.setdefault("fields", dict(new_face.get("fields") or {}))
            if english:
                for key in ("engtext", "english text"):
                    if key in fields or key == "english text":
                        fields[key] = english
            faces.append(face)
        merged["faces"] = faces
    merged["abilities"] = [a for face in fresh for a in face["abilities"]]
    fields = merged.setdefault("fields", {})
    first = (fresh[0].get("fields") or {})
    for key in ("engtext", "english text"):
        if key in fields and first.get("english text"):
            fields[key] = first["english text"]
    hyper = first.get("hyper power")
    if hyper:
        fields["hyperpower"] = hyper
    return merged


def judge(stored: dict, card) -> tuple[str, list[str]]:
    """
    "same", "update", or "review" (the new text would lose something), with the
    reasons for a review.
    """
    old = all_abilities(stored)
    fresh = card.faces or [{"abilities": card.abilities}]
    new = [a for face in fresh for a in face["abilities"]]
    reasons = []
    if not new:
        return ("same", []) if not old else ("review", ["no ability text was read"])
    if is_cut(new):
        reasons.append("the new text is cut off")
    old_sides = len(stored.get("faces") or []) or 1
    if any(not face.get("abilities") for face in fresh[1:]) and len(fresh) > 1:
        reasons.append("a side has no text")
    if len(fresh) < old_sides:
        reasons.append(f"fewer sides ({len(fresh)} instead of {old_sides})")
    everything = f" {plain(' '.join(re.sub(r'[(][^()]*[)]', ' ', a) for a in new))} "
    unreduced = f" {plain(' '.join(new))} "
    lost = []
    for sentence in sentences(old):
        if f" {sentence}" in everything or f" {sentence}" in unreduced:
            continue
        lost.append(sentence)
    reasons += [f"lost: {s}" for s in lost]
    if reasons:
        return "review", reasons
    same = [plain(a) for a in old] == [plain(a) for a in new] and not is_cut(old)
    icons_added = re.search(r"\{[LWDFN]+\d+\}", " ".join(new)) and not re.search(
        r"\{[LWDFN]+\d+\}", " ".join(old))
    if same and not icons_added and len(old) == len(new) and old_sides == len(fresh):
        return "same", []
    return "update", []


def added_sentences(stored: dict, card) -> list[str]:
    before = f" {plain(' '.join(all_abilities(stored)))} "
    fresh = card.faces or [{"abilities": card.abilities}]
    return [s for s in sentences([a for f in fresh for a in f["abilities"]])
            if f" {s}" not in before]


def compare(args) -> int:
    from scripts.scraper import parse_card_page

    cursor = connect().cursor()
    saved = {int(path.name.split(".")[0]) for path in PAGES.glob("*.html.gz")}
    cursor.execute("SELECT id, slug, source_url, raw_text FROM cards WHERE id = ANY(%s) "
                   "ORDER BY id", (sorted(saved),))
    rows = []
    for card_id, slug, url, raw_text in cursor.fetchall():
        html = gzip.decompress(page_path(card_id).read_bytes()).decode("utf-8")
        entry = {"id": card_id, "slug": slug}
        try:
            stored = json.loads(raw_text or "{}")
            card = parse_card_page(html, "UNKNOWN", url)
        except (ValueError, KeyError, AttributeError, TypeError) as error:
            rows.append({**entry, "verdict": "review", "reasons": [f"error: {error}"]})
            continue
        if card is None:
            rows.append({**entry, "verdict": "review", "reasons": ["page not understood"]})
            continue
        verdict, reasons = judge(stored, card)
        entry.update(verdict=verdict, reasons=reasons, was_cut=is_cut(all_abilities(stored)))
        if verdict != "same":
            entry["added"] = added_sentences(stored, card)
            entry["old_raw_text"] = raw_text
            entry["new_raw_text"] = json.dumps(merge(stored, card), ensure_ascii=False)
        rows.append(entry)
    WORK.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    counts = collections.Counter(r["verdict"] for r in rows)
    print(f"compared {len(rows)} saved pages: {dict(counts)}")
    updates = [r for r in rows if r["verdict"] == "update"]
    print(f"  updates that repair cut-off text: {sum(r['was_cut'] for r in updates)}")
    print(f"  updates that add sentences the database lacks: "
          f"{sum(1 for r in updates if r['added'] and not r['was_cut'])} (not cut off)")
    kinds = collections.Counter(
        reason.split(":")[0] for r in rows if r["verdict"] == "review" for reason in r["reasons"])
    if kinds:
        print(f"  reasons for review: {dict(kinds)}")
    print(f"details: {REPORT}")
    return 0


# ── Apply and restore ───────────────────────────────────────────────────────

def write_rows(connection, rows: list[tuple[int, str]]) -> None:
    """Set raw_text and faces for each (id, raw_text), all or nothing."""
    with connection, connection.cursor() as cursor:
        for card_id, raw_text in rows:
            faces = (json.loads(raw_text) if raw_text else {}).get("faces")
            cursor.execute(
                "UPDATE cards SET raw_text = %s, faces = %s, updated_at = NOW() WHERE id = %s",
                (raw_text, json.dumps(faces, ensure_ascii=False) if faces else None, card_id))


def apply(args) -> int:
    rows = json.loads(REPORT.read_text(encoding="utf-8"))
    updates = [r for r in rows if r["verdict"] == "update"]
    connection = connect()
    with connection, connection.cursor() as cursor:
        cursor.execute("SELECT id, raw_text, faces FROM cards WHERE id = ANY(%s)",
                       ([r["id"] for r in updates],))
        current = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
    # A row changed since the comparison is left alone
    ready = [r for r in updates if current.get(r["id"], (None,))[0] == r["old_raw_text"]]
    print(f"{len(ready)} cards to update, {len(updates) - len(ready)} changed since compare")
    if args.dry_run or not ready:
        return 0
    backup = WORK / f"backup_{datetime.now():%Y%m%d_%H%M%S}.json"
    backup.write_text(json.dumps(
        [{"id": r["id"], "raw_text": current[r["id"]][0], "faces": current[r["id"]][1]}
         for r in ready], ensure_ascii=False), encoding="utf-8")
    print(f"backup written: {backup}")
    write_rows(connection, [(r["id"], r["new_raw_text"]) for r in ready])
    print(f"updated {len(ready)} cards")
    return 0


def restore(args) -> int:
    saved = json.loads(Path(args.backup).read_text(encoding="utf-8"))
    connection = connect()
    with connection, connection.cursor() as cursor:
        for row in saved:
            faces = row["faces"]
            if faces is not None and not isinstance(faces, str):
                faces = json.dumps(faces, ensure_ascii=False)
            cursor.execute("UPDATE cards SET raw_text = %s, faces = %s WHERE id = %s",
                           (row["raw_text"], faces, row["id"]))
    print(f"restored {len(saved)} cards")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair stored card text")
    commands = parser.add_subparsers(dest="command", required=True)
    fetching = commands.add_parser("fetch")
    fetching.add_argument("--slugs", nargs="*")
    fetching.add_argument("--decks", nargs="*", type=int)
    fetching.add_argument("--sample", type=int, default=0)
    fetching.add_argument("--seed", type=int, default=1)
    fetching.add_argument("--all", action="store_true")
    commands.add_parser("compare")
    applying = commands.add_parser("apply")
    applying.add_argument("--dry-run", action="store_true")
    commands.add_parser("restore").add_argument("backup")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    return {"fetch": fetch, "compare": compare, "apply": apply, "restore": restore}[
        args.command](args)


if __name__ == "__main__":
    sys.exit(main())
