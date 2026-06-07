"""
parser.py
Parses Duel_Masters_rules.md into structured Python objects
ready to be inserted into PostgreSQL.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class Chapter:
    number: int
    title: str


@dataclass
class Section:
    chapter_number: int
    number: int
    title: str


@dataclass
class Rule:
    rule_number: str        # "101.2", "101.2a"
    section_number: int     # 101
    chapter_number: int     # 1
    parent_rule: str | None # "101.2"  →  parent of "101.2a"
    depth: int              # 0 = top-level, 1 = sub (a/b/c)
    text: str

    # engine tags (derived during post-processing)
    rule_category:    str        = "general"
    applies_in_phase: list[str]  = field(default_factory=list)
    applies_in_zone:  list[str]  = field(default_factory=list)
    is_state_based:   bool       = False
    is_turn_based:    bool       = False
    is_keyword_rule:  bool       = False
    priority:         int        = 100


# ── Regex patterns ───────────────────────────────────────────────────────────

# ## 1. Basics of the Game
RE_CHAPTER = re.compile(r'^##\s+(\d+)\.\s+(.+)$')

# **100. Things Needed for the Game**   or   **100. General**
RE_SECTION = re.compile(r'^\*\*(\d{3})\.\s*(.*?)\*\*\s*$')

# **101.2. Card Effects Beat the Rules**   or   **101.2.**
RE_RULE_TOP = re.compile(
    r'^\*\*(\d+\.\d+(?:\.\d+)?[a-z]?)\.\*\*\s*(.*)|'   # bold number then text outside
    r'^\*\*(\d+\.\d+(?:\.\d+)?[a-z]?)\.\s+(.+?)\*\*\s*(.*)'   # bold number + title
)

# * **101.2a** text…   or   * **101.2a.** text…
RE_RULE_SUB = re.compile(
    r'^\*\s+\*\*(\d+\.\d+(?:\.\d+)?[a-z]+)\.?\*\*\s*(.*)'
)

# Continuation: indented text after a rule (adds to previous rule text)
RE_CONTINUATION = re.compile(r'^  \s+(.+)')

# Example / note lines (strip leading *)
RE_EXAMPLE = re.compile(r'^\s*\*\s*(.*)')


# ── Category tagging helpers ─────────────────────────────────────────────────

# Map section-number ranges to default rule_category
SECTION_CATEGORIES: list[tuple[range, str]] = [
    (range(100, 105), "general"),
    (range(104, 105), "win_loss"),
    (range(105, 120), "general"),
    (range(112, 113), "cost_payment"),
    (range(113, 114), "general"),        # shields
    (range(200, 220), "general"),        # card reading
    (range(300, 320), "general"),        # card types
    (range(400, 420), "zone_rule"),
    (range(500, 513), "turn_structure"),
    (range(600, 620), "cost_payment"),
    (range(601, 602), "cost_payment"),
    (range(603, 604), "trigger"),
    (range(604, 610), "replacement"),
    (range(609, 610), "replacement"),
    (range(700, 702), "general"),
    (range(701, 702), "keyword"),
    (range(702, 703), "turn_structure"),
    (range(703, 704), "state_based"),
    (range(800, 825), "special_card"),
]

SBA_RULES = {f"703.4{c}" for c in "abcdefghijklm"}

KEYWORD_SECTIONS = {701}

PHASE_SECTIONS = set(range(500, 513))


def _get_category(section_number: int, rule_number: str) -> str:
    if rule_number in SBA_RULES:
        return "state_based"
    if section_number in KEYWORD_SECTIONS:
        return "keyword"
    if section_number in PHASE_SECTIONS:
        return "turn_structure"
    if section_number == 104:
        return "win_loss"
    for rng, cat in SECTION_CATEGORIES:
        if section_number in rng:
            return cat
    return "general"


# Zone keywords found in rule text → applies_in_zone tags
ZONE_HINTS = {
    "battle zone":      "battle_zone",
    "mana zone":        "mana_zone",
    "shield zone":      "shield_zone",
    "graveyard":        "graveyard",
    "hand":             "hand",
    "hyperspatial zone":"hyperspatial_zone",
    "ultra gr zone":    "ultra_gr_zone",
    "abyss zone":       "abyss_zone",
}

PHASE_HINTS = {
    "main step":         "main",
    "attack step":       "attack",
    "draw step":         "draw",
    "mana charge step":  "mana_charge",
    "start of turn":     "turn_start",
    "end of turn":       "turn_end",
    "end of attack":     "attack_end",
}


def _tag_zones(text: str) -> list[str]:
    t = text.lower()
    return list({v for k, v in ZONE_HINTS.items() if k in t}) or ["any"]


def _tag_phases(text: str) -> list[str]:
    t = text.lower()
    return list({v for k, v in PHASE_HINTS.items() if k in t}) or ["any"]


# ── Parser ───────────────────────────────────────────────────────────────────

def parse_rules_md(path: str | Path) -> tuple[list[Chapter], list[Section], list[Rule]]:
    """
    Returns (chapters, sections, rules) extracted from the markdown file.
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()

    chapters: list[Chapter] = []
    sections: list[Section] = []
    rules:    list[Rule]    = []

    current_chapter: Chapter | None  = None
    current_section: Section | None  = None
    current_rule:    Rule    | None  = None

    def _flush_rule():
        """Normalise and store the accumulated current rule."""
        nonlocal current_rule
        if current_rule is None:
            return
        r = current_rule
        r.text = re.sub(r'\s+', ' ', r.text).strip()
        if not r.text:
            current_rule = None
            return
        # derive category
        r.rule_category = _get_category(r.section_number, r.rule_number)
        r.is_state_based = r.rule_number in SBA_RULES
        r.is_turn_based  = r.rule_number.startswith("702.3")
        r.is_keyword_rule = r.section_number in KEYWORD_SECTIONS
        r.applies_in_zone  = _tag_zones(r.text)
        r.applies_in_phase = _tag_phases(r.text)
        # priority: SBA first, then turn-based, then rest
        if r.is_state_based:
            r.priority = 10
        elif r.is_turn_based:
            r.priority = 20
        elif r.rule_category == "win_loss":
            r.priority = 5
        else:
            r.priority = 100
        rules.append(r)
        current_rule = None

    def _section_from_rule_number(rnum: str) -> tuple[int, int]:
        """Return (chapter_number, section_number) from a rule number string."""
        top = rnum.split(".")[0]          # "101"
        sec = int(re.sub(r'[^0-9]', '', top))
        # chapter = hundreds digit
        chap = sec // 100
        return chap, sec

    for line in lines:
        stripped = line.strip()

        # ── Chapter header ────────────────────────────────────────
        m = RE_CHAPTER.match(stripped)
        if m:
            _flush_rule()
            current_chapter = Chapter(number=int(m.group(1)), title=m.group(2).strip())
            chapters.append(current_chapter)
            continue

        # ── Section header ────────────────────────────────────────
        m = RE_SECTION.match(stripped)
        if m:
            _flush_rule()
            sec_num  = int(m.group(1))
            sec_title = m.group(2).strip() if m.group(2) else f"Section {sec_num}"
            chap_num = sec_num // 100
            current_section = Section(
                chapter_number=chap_num,
                number=sec_num,
                title=sec_title,
            )
            sections.append(current_section)
            continue

        # ── Top-level rule  ───────────────────────────────────────
        # Matches:  **101.2.** text    OR    **101.2. Title** more text
        m = RE_RULE_TOP.match(stripped)
        if m:
            _flush_rule()
            if m.group(1):          # pattern 1: **num.** text
                rnum = m.group(1)
                text = m.group(2) or ""
            else:                   # pattern 2: **num. title** more_text
                rnum = m.group(3)
                text = (m.group(4) or "") + " " + (m.group(5) or "")

            chap_num, sec_num = _section_from_rule_number(rnum)
            current_rule = Rule(
                rule_number=rnum,
                section_number=sec_num,
                chapter_number=chap_num,
                parent_rule=None,
                depth=0,
                text=text.strip(),
            )
            continue

        # ── Sub-rule  (a / b / c …) ───────────────────────────────
        # Matches:  * **101.2a** text   or   * **101.2a.** text
        m = RE_RULE_SUB.match(line)
        if m:
            _flush_rule()
            rnum = m.group(1)
            text = m.group(2) or ""
            chap_num, sec_num = _section_from_rule_number(rnum)
            # parent = strip trailing letter(s)
            parent = re.sub(r'[a-z]+$', '', rnum)
            current_rule = Rule(
                rule_number=rnum,
                section_number=sec_num,
                chapter_number=chap_num,
                parent_rule=parent if parent != rnum else None,
                depth=1,
                text=text.strip(),
            )
            continue

        # ── Continuation / example text ────────────────────────────
        if current_rule is not None:
            # Skip lines that are just "---" separators or blank
            if stripped in ("---", "") or stripped.startswith("##"):
                continue
            # Append examples and continuation text to current rule
            if stripped.startswith("*Example") or stripped.startswith("* *Example"):
                example_text = RE_EXAMPLE.sub(r'\1', stripped).strip("* ")
                current_rule.text += f" [Example: {example_text}]"
            elif stripped and not stripped.startswith("**") and not stripped.startswith("* **"):
                current_rule.text += " " + stripped

    _flush_rule()  # flush the last pending rule

    # ── De-duplicate sections ─────────────────────────────────────
    seen_sections: dict[tuple, Section] = {}
    unique_sections: list[Section] = []
    for s in sections:
        key = (s.chapter_number, s.number)
        if key not in seen_sections:
            seen_sections[key] = s
            unique_sections.append(s)

    # ── Ensure all chapters referenced by sections exist ──────────
    chapter_numbers = {c.number for c in chapters}
    for s in unique_sections:
        if s.chapter_number not in chapter_numbers:
            chapters.append(Chapter(number=s.chapter_number,
                                    title=f"Chapter {s.chapter_number}"))
            chapter_numbers.add(s.chapter_number)

    chapters.sort(key=lambda c: c.number)
    unique_sections.sort(key=lambda s: (s.chapter_number, s.number))
    rules.sort(key=lambda r: _sort_key(r.rule_number))

    return chapters, unique_sections, rules


def _sort_key(rule_number: str) -> tuple:
    """Natural sort key for rule numbers like '101.2a'."""
    parts = re.split(r'[\.\s]', rule_number)
    result = []
    for p in parts:
        m = re.match(r'^(\d+)([a-z]*)$', p)
        if m:
            result.append(int(m.group(1)))
            result.append(m.group(2))
        else:
            result.append(0)
            result.append(p)
    return tuple(result)


# ── Quick sanity check ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    md_path = sys.argv[1] if len(sys.argv) > 1 else "Duel_Masters_rules.md"
    chapters, sections, rules = parse_rules_md(md_path)

    print(f"Chapters : {len(chapters)}")
    print(f"Sections : {len(sections)}")
    print(f"Rules    : {len(rules)}")
    print()

    sba   = [r for r in rules if r.is_state_based]
    kw    = [r for r in rules if r.is_keyword_rule]
    phase = [r for r in rules if r.rule_category == "turn_structure"]

    print(f"  State-based actions : {len(sba)}")
    print(f"  Keyword rules       : {len(kw)}")
    print(f"  Turn-structure rules: {len(phase)}")
    print()
    print("Sample rules:")
    for r in rules[:5]:
        print(f"  [{r.rule_number}] (cat={r.rule_category}) {r.text[:80]}…")
