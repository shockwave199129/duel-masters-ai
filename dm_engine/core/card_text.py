"""
core/card_text.py - translate printed English card text into ability data.

The output has the same shape as the hand-written entries in core/card_scripts.py:
abilities made of steps, plus values for that module's tables. A card's text is
split into abilities, and each ability into parts: when it happens, what it does,
and what it affects.

Translation is strict. A sentence that is not understood completely is listed in
``unknown`` and the card is not "complete", so nothing is ever half guessed.

This module holds data and text handling only and must not import the engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.card_scripts import (
    EX_LIFE_SHIELD_LEFT, EX_LIFE_SHIELD_LEFT_KEY, FREE_PLAY_KEY, HAND_TURN_END_KEY, QUEUED,
    Ability,
)
from core.enums import INFINITY, Civilization, Keyword, TriggerEvent

PUT = TriggerEvent.ON_ENTER_BATTLE_ZONE
ATTACK = TriggerEvent.ON_ATTACK
DESTROYED = TriggerEvent.ON_DESTROY
CAST = TriggerEvent.ON_CAST
SUMMON = TriggerEvent.ON_SUMMON
TURN_END = TriggerEvent.END_OF_TURN

_CIVILIZATIONS = {c.value.lower(): c for c in Civilization}
_COST_LETTERS = {
    "L": Civilization.LIGHT, "W": Civilization.WATER, "D": Civilization.DARKNESS,
    "F": Civilization.FIRE, "N": Civilization.NATURE,
}
_CIV = r"(light|water|darkness|fire|nature)"
_ME = r"(?:this creature|this tamaseed|this galaxy castle)"
_PUT_IT = rf"when you put {_ME}(?: into the battle zone)?"
_BOTTOM = r"put it on the bottom of your deck instead of your graveyard"

# Keywords the engine already carries out for any card that has them
_PLAIN_KEYWORDS = {
    "blocker": Keyword.BLOCKER,
    "shield trigger": Keyword.SHIELD_TRIGGER,
    "speed attacker": Keyword.SPEED_ATTACKER,
    "slayer": Keyword.SLAYER,
    "mach fighter": Keyword.MACH_FIGHTER,
    "g-strike": Keyword.G_STRIKE,
    "double breaker": Keyword.DOUBLE_BREAKER,
    "triple breaker": Keyword.TRIPLE_BREAKER,
    "world breaker": Keyword.WORLD_BREAKER,
    "last burst": Keyword.LAST_BURST,
    "mega last burst": Keyword.MEGA_LAST_BURST,
}


@dataclass
class Face:
    text: str
    card_type: str = "Creature"
    cost: int = 0
    civilizations: frozenset = frozenset()
    hyper_power: int | None = None


@dataclass
class Translation:
    abilities: list[Ability] = field(default_factory=list)
    tables: dict[str, object] = field(default_factory=dict)  # table name -> this card's value
    modes: dict[str, dict] = field(default_factory=dict)     # mode table name -> modes
    keywords: set = field(default_factory=set)
    unknown: list[str] = field(default_factory=list)
    rules_used: set[str] = field(default_factory=set)
    race_terms: set[str] = field(default_factory=set)  # checked against the real races

    @property
    def complete(self) -> bool:
        return not self.unknown


# ── Text clean-up ───────────────────────────────────────────────────────────

def _plain(text: str) -> str:
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    text = text.replace("'''", "").replace("''", "")
    for old, new in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("\u206f", " ")):
        text = text.replace(old, new)
    return text


def _split_reminders(text: str) -> tuple[str, list[str]]:
    """Take out reminder text in parentheses, which may be nested."""
    kept, reminders, depth, current = [], [], 0, []
    for char in text:
        if char == "(":
            depth += 1
        if depth:
            current.append(char)
        else:
            kept.append(char)
        if char == ")" and depth:
            depth -= 1
            if not depth:
                reminders.append("".join(current))
                current = []
    return "".join(kept), reminders


def _paragraphs(text: str) -> list[str]:
    # Zero-width spaces and rules of dashes also separate abilities on card pages.
    # A zero-width space in front of a small letter is inside a sentence.
    text = re.sub(r"\u200b(?=\s*[a-z])", "", text)
    parts = re.split(r"■|----|\u200b", text)
    return [p for p in (" ".join(part.split()).strip(" .") for part in parts) if p]


def _sentences(text: str) -> list[str]:
    found = re.split(r"(?<=[a-z0-9\"'}])\.\s+(?=\S)", text.strip())
    return [s.strip(" .") for s in found if s.strip(" .")]


def _number(word: str | None, default: int = 1) -> int:
    if not word:
        return default
    return {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3}.get(word.lower()) or int(word)


def _match(pattern: str, text: str):
    return re.fullmatch(pattern, text, re.IGNORECASE)


def _race_word(word: str, result: "Translation | None" = None) -> str:
    race = " ".join(part.capitalize() for part in word.split())
    if result is not None:
        result.race_terms.add(race)
    return race


def _is_race(term: str, races) -> bool:
    """Is this a race, or whole words of one (rules 203.2, 203.4)?"""
    return any(f" {term.lower()} " in f" {race.lower()} " for race in races)


# ── What an ability does ────────────────────────────────────────────────────
# Each rule is (name, pattern, build). ``build(match, optional)`` returns
# (steps, handles_optional). A pattern may cover several sentences.

_EFFECT_RULES: list[tuple[str, str, object]] = []


def _effect(name: str, pattern: str, *, optional: bool = False):
    def register(build):
        _EFFECT_RULES.append((name, pattern, (build, optional)))
        return build
    return register


@_effect("draw", r"draw (a|\d+) cards?")
def _draw(m, may):
    return [("draw", {"count": _number(m[1]), "who": "self"})]


@_effect("opponent_draws", r"your opponent draws (a|\d+) cards?")
def _opponent_draws(m, may):
    return [("draw", {"count": _number(m[1]), "who": "opponent"})]


@_effect("draw_then_discard", r"draw (\d+) cards and discard a card from your hand")
def _draw_discard(m, may):
    return [("draw", {"count": int(m[1]), "who": "self"}), ("discard_own", {"count": 1})]


@_effect("draw_discard_for_random",
         r"draw (\d+) cards and discard up to (\d+) cards from your hand\. for each discarded "
         r"card, discard a card from your opponent's hand at random")
def _draw_discard_random(m, may):
    return [("draw", {"count": int(m[1]), "who": "self"}),
            ("discard_then_random", {"left": int(m[2]), "discarded": 0})]


@_effect("mill", r"put the top (\d+ )?cards? of your deck into your graveyard")
def _mill(m, may):
    return [("mill", {"count": _number((m[1] or "").strip())})]


@_effect("mill_up_to", r"put up to (\d+) cards from the top of your deck into your graveyard")
def _mill_up_to(m, may):
    return [("mill_up_to", {"count": int(m[1])})]


@_effect("deck_to_mana",
         r"put the top (\d+ )?cards? of your deck into your mana zone( tapped)?")
def _deck_to_mana(m, may):
    return [("deck_to_mana", {"count": _number((m[1] or "").strip()), "tapped": bool(m[2])})]


@_effect("shieldify_top", r"shieldify the top card of your deck")
def _shieldify_top(m, may):
    return [("deck_to_shield", {})]


@_effect("shieldify_creature", r"choose a creature and shieldify it")
def _shieldify_creature(m, may):
    return [("shieldify_creature", {})]


@_effect("random_discard", r"discard a card at random from your opponent's hand")
def _random_one(m, may):
    return [("random_discard", {"count": 1})]


@_effect("opponent_random_discard",
         r"your opponent discards (a|\d+) cards? at random from their hand")
def _random_many(m, may):
    return [("random_discard", {"count": _number(m[1])})]


@_effect("opponent_discards", r"your opponent discards (a|\d+) cards? from their hand")
def _opponent_discards(m, may):
    return [("opponent_discards", {"count": _number(m[1])})]


@_effect("debuff",
         r"choose one of your opponent's creatures\. that creature (?:gets -(\d+) power|"
         r"loses (infinite) power) until the end of the turn")
def _debuff(m, may):
    return [("debuff", {"amount": -INFINITY if m[2] else -int(m[1])})]


@_effect("lock",
         r"choose one of your opponent's creatures\. that creature can't attack or block "
         r"until the start of your next turn")
def _lock(m, may):
    return [("lock", {})]


def _destroy_step(target: str, limit: str | None, optional: bool, then=()):
    params = {"max_cost": None, "max_power": None, "optional": optional,
              "tamaseeds": "tamaseed" in target.lower(), "then_if_destroyed": list(then)}
    if limit:
        if by_power := _match(r" that has power (\d+) or less", limit):
            params["max_power"] = int(by_power[1])
        elif by_cost := _match(r" that costs? (\d+) or less", limit):
            params["max_cost"] = int(by_cost[1])
        else:
            params["max_cost"] = "graveyard"
    return ("destroy", params)


_TARGET = (r"one of your opponent's (creatures or tamaseeds|creatures)"
           r"( that has power \d+ or less| that costs? \d+ or less"
           r"| that costs? the same as or less than the number of cards in your graveyard)?")


@_effect("destroy", rf"choose {_TARGET} and destroy (?:it|them)", optional=True)
def _destroy_chosen(m, may):
    return [_destroy_step(m[1], m[2], may)]


@_effect("destroy_then_draw", rf"choose {_TARGET}, destroy it, then draw a card", optional=True)
def _destroy_then_draw(m, may):
    return [_destroy_step(m[1], m[2], may, [("draw", {"count": 1, "who": "self"})])]


_REANIMATE_KINDS = {
    (False, False, "creature"): "creature",
    (True, False, "creature"): "non_evolution",
    (True, True, "creature"): "non_evolution_darkness",
    (False, False, "abyss"): "abyss",
    (False, False, "abyss creature"): "abyss",
}


@_effect("put_from_graveyard",
         r"put (?:a|an) (non-evolution )?(darkness )?(abyss creature|abyss|creature)"
         r"(?: that costs (\d+) or less)? from your graveyard(?: into the battle zone)?",
         optional=True)
def _reanimate(m, may):
    kind = _REANIMATE_KINDS.get((bool(m[1]), bool(m[2]), m[3].lower()))
    if kind is None:
        return None
    return [("reanimate", {"max_cost": int(m[4]) if m[4] else None, "kind": kind,
                           "optional": may, "except_name": None})]


@_effect("return_spells", r"return up to (\d+) spells from your graveyard to your hand")
def _return_spells(m, may):
    return [("return_spells", {"left": int(m[1])})]


@_effect("cast_from_hand", r"cast a spell that costs (\d+) or less from your hand for no cost",
         optional=True)
def _cast_from_hand(m, may):
    return [("cast_from_hand", {"max_cost": int(m[1])})] if may else None


@_effect("cast_then_bottom",
         r"cast a spell that costs (\d+) or less from your hand or graveyard for no cost\. "
         rf"after you cast(?: it)?, {_BOTTOM}", optional=True)
def _cast_then_bottom(m, may):
    return [("cast_then_bottom", {"max_cost": int(m[1])})] if may else None


_PLAY_KINDS = {"darkness": "darkness", "non-creature": "non_creature"}
_AFTER_PLAY = (rf"(\. if you cast a spell this way, after you cast(?: it)?, {_BOTTOM})")
_SUPER = (r"(\. if that card has \"super shield trigger\", you may use its ability as if you "
          r"had no shields)?")


@_effect("play_from_graveyard",
         r"play a (darkness|non-creature) card that costs (?:(\d+) or less|the same as or less "
         r"than (?:the )?number of cards in your graveyard)(?: from your graveyard)? for no cost"
         rf"{_SUPER}{_AFTER_PLAY}", optional=True)
def _play_from_graveyard(m, may):
    params = {"max_cost": int(m[2]) if m[2] else "graveyard", "kind": _PLAY_KINDS[m[1].lower()],
              "optional": may}
    if m[3]:
        params["super_as_if_no_shields"] = True
    return [("play_from_graveyard", params)]


@_effect("play_shield_trigger",
         r"play a card that has \"shield trigger\" from your graveyard for no cost"
         rf"{_AFTER_PLAY}", optional=True)
def _play_shield_trigger(m, may):
    return [("play_from_graveyard",
             {"max_cost": None, "kind": "shield_trigger", "optional": may})]


@_effect("put_under", r"put a card from your hand under this creature face up", optional=True)
def _put_under(m, may):
    return [("put_under_from_hand", {})]


@_effect("mana_to_hand", r"return a card from your mana zone to your hand", optional=True)
def _mana_to_hand(m, may):
    return [("mana_to_hand", {})] if may else None


@_effect("tap_all", r"tap all your opponent's creatures(?: in the battle zone)?")
def _tap_all(m, may):
    return [("tap_all_opposing", {})]


@_effect("tap_some", r"tap (\d+) of your opponent's creatures")
def _tap_some(m, may):
    return [("tap_opposing", {"left": int(m[1])})]


@_effect("untap_mana", r"untap all cards in your mana zone")
def _untap_mana(m, may):
    return [("untap_mana", {})]


@_effect("untap_self", r"untap this creature")
def _untap_self(m, may):
    return [("untap_self", {})]


@_effect("destroy_all_except_race", r"destroy all non-([a-z ]+?)s")
def _destroy_all_except(m, may):
    return [("destroy_all_without_race", {"race": _race_word(m[1])})]


@_effect("bounce_all", r"return all your opponent's creatures to their owners' hands")
def _bounce_all(m, may):
    return [("bounce_all_opposing", {})]


@_effect("opponent_hand_to_mana", r"your opponent puts their hand into their mana zone")
def _hand_to_mana(m, may):
    return [("opponent_hand_to_mana", {})]


@_effect("look_top_four",
         r"look at the top 4 cards of your deck\. put one of them into your hand, one into "
         r"your mana zone, one into your graveyard and one on the bottom of your deck")
def _look_four(m, may):
    return [("look_top", {"look": 4, "places": ["hand", "mana", "graveyard"]})]


@_effect("look_top_two",
         r"look at the top 2 cards of your deck\. put one into your mana zone and the other "
         r"into your graveyard")
def _look_two(m, may):
    return [("look_top", {"look": 2, "places": ["mana", "graveyard"]})]


@_effect("reveal_race_to_mana",
         r"reveal the top (\d+) cards of your deck\. put all dragons revealed this way into "
         r"your mana zone tapped and the rest into your graveyard")
def _reveal_dragons(m, may):
    return [("reveal_dragons_to_mana", {"count": int(m[1])})]


@_effect("reveal_creatures",
         r"reveal the top (\d+) cards of your deck\. from among them, put any number of "
         r"creatures into your hand and put the rest into your mana zone tapped")
def _reveal_creatures(m, may):
    return [("reveal_creatures_to_hand", {"left": int(m[1])})]


@_effect("shuffle_reveal_put",
         r"shuffle your deck, then reveal the top card of your deck\. if that cards? (?:is )?a "
         r"creature, you may put it\. if it's not a creature, put it into your mana zone")
def _mystery(m, may):
    return [("mystery_cube", {})]


@_effect("gachinko_extra_turn",
         r"you and your opponent play gachinko judge\. if you win, take an extra turn after "
         r"this one")
def _gachinko(m, may):
    return [("gachinko_extra_turn", {})]


@_effect("name_card", r"choose a creature card name except (.+)")
def _name_card(m, may):
    return [("name_card", {"except_name": m[1].strip()})]


@_effect("keep_shields",
         r"each player chooses (\d+) of their shields and puts the rest into their graveyard")
def _keep_shields(m, may):
    return [("keep_shields", {"keep": int(m[1]), "seat": seat}) for seat in ("self", "opponent")]


@_effect("put_dragon_from_hand",
         r"put a non-evolution light dragon creature that costs (\d+) or less from your hand\. "
         r"that creature gets \"blocker\" until the start of your next turn")
def _put_dragon(m, may):
    return [("put_dragon_from_hand", {"max_cost": int(m[1])})]


@_effect("mana_if_another_else_self",
         r"if you have another ([a-z ]+), put the top card of your deck into your mana zone\. "
         r"otherwise, put this creature into your mana zone")
def _mana_or_leave(m, may):
    return [("mana_or_leave", {"race": _race_word(m[1])})]


@_effect("self_to_mana_if_you_do",
         rf"put it into your mana zone\. if you do, choose {_TARGET} and destroy it",
         optional=True)
def _self_to_mana(m, may):
    if not may:
        return None
    return [("may", {"then": [("self_to_mana", {"then": [_destroy_step(m[1], m[2], False)]})]})]


@_effect("destroy_then_owner_reanimates",
         r"choose a player\. you may destroy one of that player's creatures\. if you do, choose "
         r"a non-evolution creature except (.+?) from that player's graveyard and put it into "
         r"the battle zone")
def _destroy_and_return(m, may):
    return [("babelginus", {"except_name": m[1].strip()})]


@_effect("next_creature_discount",
         r"this turn, your next creature costs up to (\d+) less to summon\. it can't cost 0 or "
         r"less\. when you put that creature, that creature gets \"speed attacker\" until the "
         r"end of the turn")
def _next_discount(m, may):
    return [("next_creature_discount", {"amount": int(m[1])})]


def _parse_sentences(sentences: list[str], used: set[str]) -> list | None:
    """Match rules against runs of sentences, longest first."""
    if not sentences:
        return []
    for length in range(min(len(sentences), 4), 0, -1):
        chunk = ". ".join(sentences[:length])
        chunk = re.sub(r"^then,? ", "", chunk, flags=re.IGNORECASE)
        for optional in (False, True):
            body = chunk
            if optional:
                if not re.match(r"you may ", body, re.IGNORECASE):
                    continue
                body = body[len("you may "):]
            for name, pattern, (build, handles_optional) in _EFFECT_RULES:
                found = _match(pattern, body)
                if not found:
                    continue
                steps = build(found, optional)
                if steps is None:
                    continue
                if optional and not handles_optional:
                    steps = [("may", {"then": steps})]
                rest = _parse_sentences(sentences[length:], used)
                if rest is None:
                    continue
                used.add(name)
                return [*steps, *rest]
    return None


def _parse_effect(text: str, result: Translation, slug: str) -> tuple | None:
    """Steps for what an ability does, including a choice between modes."""
    modes = _match(r"choose (one|\d+) of the following\.?\s*(►.+)", text)
    if modes:
        table = {}
        for index, option in enumerate(p.strip(" .") for p in modes[2].split("►") if p.strip()):
            steps = _parse_sentences(_sentences(option), result.rules_used)
            if steps is None:
                return None
            table[f"mode:{index}"] = steps
        name = f"{slug}/{len(result.modes)}"
        result.modes[name] = table
        result.rules_used.add("modes")
        return (("modes", {"table": name, "count": _number(modes[1]), "chosen": []}),)
    steps = _parse_sentences(_sentences(text), result.rules_used)
    return None if steps is None else tuple(steps)


# ── When an ability happens ─────────────────────────────────────────────────
# (name, pattern for the opening of the sentence, [(key, event, watch, extra first steps)])

_TRIGGERS = [
    ("put_or_attack", rf"{_PUT_IT} or it attacks",
     [("put", PUT, "self"), ("attack", ATTACK, "self")]),
    ("put_or_turn_end", rf"{_PUT_IT} or at the end of your turn",
     [("put", PUT, "self"), ("turn_end", TURN_END, "own_turn_end")]),
    ("put_or_race_attacks", rf"{_PUT_IT} or whenever one of your abyss(?:es)? attacks",
     [("put", PUT, "self"), ("abyss_attack", ATTACK, "own_abyss")]),
    ("attack_or_ex_life_left",
     rf"whenever {_ME} attacks or when this creature's \"ex life\" shield leaves your shield zone",
     [("attack", ATTACK, "self"), (EX_LIFE_SHIELD_LEFT_KEY, EX_LIFE_SHIELD_LEFT, "self")]),
    ("face_up_self", rf"when you put {_ME} into your shield zone face up",
     [("face_up_self", QUEUED, "self")]),
    ("put", _PUT_IT, [("put", PUT, "self")]),
    ("attack", rf"when(?:ever)? {_ME} attacks", [("attack", ATTACK, "self")]),
    ("summon", r"when you summon this creature", [("summoned", SUMMON, "self")]),
    ("destroyed", r"when this creature is destroyed", [("destroyed", DESTROYED, "self")]),
    ("cast_any_spell", r"whenever you cast a spell", [("spell_cast", CAST, "own_cast")]),
    ("turn_end_after_cast",
     r"at the end of each of your turns, if you cast a spell that turn",
     [("turn_end", TURN_END, "own_turn_end_after_cast")]),
    ("summon_dragon", r"whenever you summon a dragon",
     [("dragon_summon", SUMMON, "own_dragon_summon")]),
    ("leaves_from_under", r"whenever a card leaves from under this creature",
     [("left_under", QUEUED, "self")]),
    ("face_up_shield", r"whenever you put a card face up into your shield zone",
     [("face_up_shield", QUEUED, "self")]),
]


def _triggered(paragraph: str, result: Translation, slug: str) -> bool:
    for name, opening, abilities in _TRIGGERS:
        found = re.match(rf"{opening}, ", paragraph, re.IGNORECASE)
        if not found:
            continue
        steps = _parse_effect(paragraph[found.end():], result, slug)
        if steps is None:
            return False
        for key, event, watch in abilities:
            result.abilities.append(Ability(key, event, steps, paragraph, watch=watch))
        result.rules_used.add(f"when:{name}")
        return True
    return False


# ── Whole abilities that are not "when X, do Y" ─────────────────────────────

_STATIC_RULES: list[tuple[str, str, object]] = []


def _static(name: str, pattern: str):
    def register(build):
        _STATIC_RULES.append((name, pattern, build))
        return build
    return register


def _grant(result: Translation, whose: str, race: str | None, *keywords) -> None:
    grants = result.tables.get("GRANTS", ())
    result.tables["GRANTS"] = (*grants, (whose, race, tuple(keywords)))


def _flag(pattern: str, *tables: str):
    """A rule that only switches tables on."""
    def build(m, result, face):
        for table in tables:
            result.tables[table] = True
        return True
    _STATIC_RULES.append((tables[0].lower(), pattern, build))


_flag(r"your opponent's creatures are put tapped", "OPPONENT_CREATURES_ENTER_TAPPED")
_flag(r"this creature is put tapped and doesn't untap at the start of your turn",
      "ENTERS_TAPPED", "DOES_NOT_UNTAP")
_flag(r"your opponent can't cast spells", "OPPONENT_CANT_CAST")
_flag(r"you can't be attacked by creatures that have less power than this creature",
      "WEAKER_CANT_ATTACK_YOU")
_flag(r"your opponent's creatures can't attack you on the turn they are put",
      "NEW_CREATURES_CANT_ATTACK_YOU")
_flag(r"your opponent's cards are put into their mana zone tapped",
      "OPPONENT_MANA_ENTERS_TAPPED")
_flag(r"each creature put from your graveyard can attack your opponent during the turn they "
      r"are put", "GRAVEYARD_CREATURES_RUSH")
_flag(r"during your opponent's turn, whenever your opponent puts a creature in any way except "
      r"by summoning, destroy that creature", "DESTROYS_UNSUMMONED")
_flag(r"whenever your opponent puts a creature and (?:its|your opponent's creature's) ability "
      r"would trigger, that ability won't trigger instead", "PUT_TRIGGER_SUPPRESSORS")
_flag(r"whenever this creature would break a shield, your opponent puts that shield into "
      r"their graveyard instead", "BREAKS_TO_GRAVEYARD")
_flag(r"when your opponent would put a creature except by summoning, put it into their mana "
      r"zone instead", "UNSUMMONED_TO_MANA")
_flag(r"ex life", "EX_LIFE")
_flag(r"eternal omega: when this creature would leave, return it to your hand instead|"
      r"eternal omega", "ETERNAL_OMEGA")


@_static("cant_attack", r"this creature can't attack")
def _cant_attack(m, result, face):
    _grant(result, "self", None, Keyword.CANNOT_ATTACK)
    return True


@_static("your_creatures_get",
         r"each of your ([a-z ]+? )?creatures(?: in the battle zone)? gets \"([a-z ]+)\""
         r"(?: and \"([a-z ]+)\")?")
def _creatures_get(m, result, face):
    keywords = [_PLAIN_KEYWORDS.get(word.lower()) for word in (m[2], m[3]) if word]
    if None in keywords:
        return False
    _grant(result, "own", _race_word(m[1], result) if m[1] else None, *keywords)
    return True


@_static("opposing_race_cant_attack", r"your opponent's ([a-z ]+?)s can't attack")
def _opposing_cant(m, result, face):
    if m[1].lower() == "creature":
        return False
    _grant(result, "opponent", _race_word(m[1], result), Keyword.CANNOT_ATTACK)
    return True


@_static("power_attacker", r"power attacker \+(\d+)")
def _power_attacker(m, result, face):
    result.tables["POWER_ATTACKERS"] = int(m[1])
    return True


@_static("quattro_breaker", r"quattro breaker")
def _quattro(m, result, face):
    result.tables["BREAKER_COUNTS"] = 4
    return True


@_static("power_per_opposing_spell",
         r"this creature gets \+(\d+) power for each spell in your opponent's hand")
def _per_spell(m, result, face):
    result.tables["POWER_PER_OPPONENT_SPELL"] = int(m[1])
    return True


@_static("big_summon_discount",
         r"your creatures that cost (\d+) or more cost (\d+) less to summon\. they can't cost 0 "
         r"or less")
def _big_discount(m, result, face):
    result.tables["BIG_SUMMON_DISCOUNT"] = (int(m[1]), int(m[2]))
    return True


@_static("graveyard_summon_discount",
         r"your creatures in your graveyard cost (\d+) less to summon\. they can't cost 0 or "
         r"less")
def _grave_discount(m, result, face):
    result.tables["GRAVEYARD_SUMMON_DISCOUNT"] = int(m[1])
    return True


@_static("graveyard_self_discount",
         r"if you have (\d+) or more cards in your graveyard, this creature may cost (\d+) less "
         r"to summon(\. you may also summon this creature from your graveyard)?")
def _self_discount(m, result, face):
    result.tables["GRAVEYARD_SELF_DISCOUNT"] = (int(m[1]), int(m[2]))
    if m[3]:
        result.tables["SUMMON_FROM_GRAVEYARD"] = True
    return True


@_static("summon_self_from", r"you may summon this creature from your (graveyard|mana zone)")
def _summon_from(m, result, face):
    table = "SUMMON_FROM_GRAVEYARD" if m[1].lower() == "graveyard" else "SUMMON_FROM_MANA"
    result.tables[table] = True
    return True


@_static("summon_race_from_mana",
         r"you may summon ([a-z ]+?) creatures from your mana zone")
def _race_from_mana(m, result, face):
    result.tables["SUMMON_FROM_MANA_GRANTS"] = _race_word(m[1], result)
    return True


@_static("abyss_rush_grant",
         r"your ([a-z ]+?) creatures in your graveyard get \"abyss rush\"")
def _rush_grant(m, result, face):
    result.tables["ABYSS_RUSH_GRANTS"] = _race_word(m[1], result)
    return True


@_static("neo_evolution",
         r"(?:neo evolution: put on one of your|g-neo evolution: you may put this creature on "
         r"top of one of your) ([a-z, ]+?) creatures")
def _neo(m, result, face):
    words = re.findall(_CIV, m[1], re.IGNORECASE)
    if not words or re.sub(rf"{_CIV}|or|and|,| ", "", m[1], flags=re.IGNORECASE):
        return False
    entry = result.tables.setdefault("NEO_EVOLUTION", {"discount": 0})
    entry["civilizations"] = frozenset(_CIVILIZATIONS[w.lower()] for w in words)
    return True


@_static("neo_discount",
         r"if you summon this creature as a neo evolution creature, it costs (\d+) less to summon")
def _neo_discount(m, result, face):
    result.tables.setdefault("NEO_EVOLUTION", {})["discount"] = int(m[1])
    return True


@_static("evolution_on_race", r"evolution: put on one of your ([a-z ]+?)s?")
def _evolution(m, result, face):
    race = re.sub(r" creature$", "", m[1], flags=re.IGNORECASE)
    if race.lower() in _CIVILIZATIONS or race.lower() in ("creature",):
        return False
    result.tables["EVOLUTION_BASE_RACE"] = _race_word(race, result)
    return True


@_static("creature_only_with_darkness",
         r"if you don't have a total of (\d+) or more darkness creatures and/or darkness "
         r"tamaseeds, this tamaseed isn't treated as a creature in the battle zone")
def _needs_darkness(m, result, face):
    result.tables["CREATURE_ONLY_WITH_DARKNESS"] = int(m[1])
    return True


@_static("elements_only_from_hand",
         r"your opponent can't put elements (from anywhere except their hand|except by playing "
         r"them)")
def _from_hand_only(m, result, face):
    # The engine enforces both halves together, so both must be printed
    seen = result.tables.setdefault("_element_limits", set())
    seen.add(m[1].lower()[:1])
    if len(seen) == 2:
        result.tables.pop("_element_limits")
        result.tables["ELEMENTS_ONLY_PLAYED_FROM_HAND"] = True
    return True


@_static("ignore_chosen_name", r"ignore any abilities of creatures that have the chosen name")
def _ignore_name(m, result, face):
    result.tables["NAMES_TO_IGNORE"] = True
    return True


@_static("choice_protection",
         r"if you have another ([a-z ]+), your opponent can't choose this creature")
def _protection(m, result, face):
    result.tables["CHOICE_PROTECTION"] = _race_word(m[1], result)
    return True


@_static("opposing_races_to_graveyard",
         r"whenever your opponent would put a ([a-z]+) or ([a-z]+) creature, put it into their "
         r"graveyard instead")
def _races_to_grave(m, result, face):
    result.tables["OPPONENT_RACES_TO_GRAVEYARD"] = (
        _race_word(m[1], result), _race_word(m[2], result))
    return True


@_static("leave_instead",
         r"when(?:ever)? (this creature|your abyss|one of your creatures) would leave, you may "
         r"(.+) instead")
def _leave_instead(m, result, face):
    subject, action = m[1].lower(), m[2]
    rule: dict = {"scope": "self" if subject == "this creature" else "own"}
    if subject == "your abyss":
        rule["race"] = "Abyss"
    if found := _match(r"discard (\d+) cards from your hand", action):
        rule.update(kind="discard", count=int(found[1]))
    elif _match(r"destroy one of your other creatures", action):
        rule["kind"] = "destroy_other"
    elif found := _match(r"put (\d+) cards from your mana zone into your graveyard", action):
        rule.update(kind="mana", count=int(found[1]))
    elif found := _match(r"choose (\d+) cards from your graveyard, shuffle them, and put them "
                         r"on the bottom of your deck", action):
        rule.update(kind="shuffle_grave", count=int(found[1]))
    else:
        return False
    result.tables["LEAVE_REPLACEMENTS"] = rule
    return True


@_static("saver", rf"saver: {_CIV} creature")
def _saver(m, result, face):
    result.tables["LEAVE_REPLACEMENTS"] = {
        "kind": "saver", "scope": "own", "civilization": _CIVILIZATIONS[m[1].lower()]}
    return True


@_static("star_max_replacement",
         r"oni star max evolution: when you would lose the game or this creature would leave, "
         r"you may destroy (\d+) of your face up cards instead")
def _oni(m, result, face):
    result.tables["LEAVE_REPLACEMENTS"] = {
        "kind": "destroy_face_up", "count": int(m[1]), "scope": "self"}
    result.tables["LOSS_REPLACEMENTS"] = int(m[1])
    return True


@_static("attack_chance",
         rf"attack chance: {_CIV} creature that has power (\d+) or more")
def _attack_chance(m, result, face):
    result.tables["ATTACK_CHANCE"] = (_CIVILIZATIONS[m[1].lower()], int(m[2]))
    return True


def _cost_icon(icon: str) -> tuple[int, frozenset] | None:
    found = re.fullmatch(r"([LWDFN]+)(\d+)", icon)
    if not found:
        return None
    return int(found[2]), frozenset(_COST_LETTERS[letter] for letter in found[1])


@_static("recycle", r"recycle \{(\w+)\}")
def _recycle(m, result, face):
    cost = _cost_icon(m[1])
    if cost is None:
        return False
    result.tables["RECYCLE"] = cost[0]
    return True


@_static("counterattack",
         r"counterattack \{(\w+)\} ?: when a creature attacks you, if you haven't used "
         r"'counterattack' during that attack, you may cast this spell from your hand for no "
         r"cost\. if you do, pay \{\w+\} at the start of your next turn\. if you can't pay, you "
         r"lose the game")
def _counterattack(m, result, face):
    cost = _cost_icon(m[1])
    if cost is None or len(cost[1]) != 1:
        return False
    result.tables["COUNTERATTACK"] = (cost[0], next(iter(cost[1])))
    return True


@_static("civil_count",
         rf"civil count (\d+): if this spell is in your shield zone and you have a total of \d+ "
         rf"or more {_CIV} creatures and/or {_CIV} tamaseeds, this spell gets \"shield "
         rf"trigger\"")
def _civil_count(m, result, face):
    result.tables["CIVIL_COUNT_SHIELD_TRIGGER"] = (_CIVILIZATIONS[m[2].lower()], int(m[1]))
    return True


@_static("free_play_punisher",
         r"whenever a player puts a creature or cross gear or casts a spell without tapping "
         r"cards in their mana zone, that player chooses one of their creatures and destroys it")
def _free_play(m, result, face):
    result.abilities.append(Ability(FREE_PLAY_KEY, QUEUED, (("destroy_own", {}),), m[0]))
    return True


@_static("cast_discarded",
         r"whenever your opponent discards a spell, you may cast that spell from your "
         r"opponent's graveyard for no cost")
def _cast_discarded(m, result, face):
    result.abilities.append(Ability(
        "opponent_discarded_spell", QUEUED, (("cast_discarded", {}),), m[0]))
    return True


@_static("put_instead_of_discard",
         r"when this creature would be discarded by the effect of one of your opponent's "
         r"spells or ability of an opponent's creature, you may put it instead")
def _instead_of_discard(m, result, face):
    result.abilities.append(Ability(
        "instead_of_discard", QUEUED, (("put_instead_of_discard", {}),), m[0]))
    return True


@_static("first_race_attack",
         r"each turn, when your abyss attacks? for the first time, (.+)")
def _first_attack(m, result, face):
    steps = _parse_sentences(_sentences(m[1]), result.rules_used)
    if steps is None:
        return False
    watch = "own_abyss_hyper" if "HYPER_MODE" in result.tables else "own_abyss"
    result.abilities.append(Ability(
        "first_abyss_attack", ATTACK,
        (("once_per_turn", {"flag": "first_abyss_attack"}), *steps), m[0], watch=watch))
    return True


@_static("free_cast_after_many_creatures",
         r"at the end of your opponent's turn, if (\d+) or more creatures were put that turn, "
         r"you may cast this spell for no cost")
def _after_creatures(m, result, face):
    result.tables["HAND_AT_OPPONENT_TURN_END"] = {
        "kind": "creatures_put", "count": int(m[1]), "ability": HAND_TURN_END_KEY}
    result.abilities.append(Ability(HAND_TURN_END_KEY, QUEUED, (("may_play_this", {}),), m[0]))
    return True


@_static("free_summon_after_free_play",
         r"at the end of your opponent's turn, if your opponent put a creature or cast a spell "
         r"without tapping a card in their mana zone that turn, and you don't have a .+?, you "
         r"may summon this creature for no cost")
def _after_free_play(m, result, face):
    result.tables["HAND_AT_OPPONENT_TURN_END"] = {
        "kind": "opponent_played_free", "ability": HAND_TURN_END_KEY}
    result.abilities.append(Ability(HAND_TURN_END_KEY, QUEUED, (("may_play_this", {}),), m[0]))
    return True


@_static("destroy_self_unless_shields_broken",
         r"during your opponent's turn, when you put this creature, if (\d+) or more of your "
         r"shields weren't broken this turn, destroy this creature")
def _unless_broken(m, result, face):
    # Part of the same arrival: it follows what the creature does when put
    for index, ability in enumerate(result.abilities):
        if ability.event == PUT:
            result.abilities[index] = Ability(
                ability.key, ability.event,
                (*ability.steps, ("destroy_self_unless_shields_broken", {"count": int(m[1])})),
                ability.text, watch=ability.watch)
            return True
    return False


# ── Whole cards ─────────────────────────────────────────────────────────────

def _special_paragraphs(text: str, reminders: list[str], result: Translation, face: Face) -> str:
    """Keywords whose numbers or effect run across reminder text."""
    # Fushigiverse: the numbers are cost icons, one of them inside the reminder
    found = re.search(r"Fushigiverse \{(\w+)\}", text)
    if found:
        cost = _cost_icon(found[1])
        least = next((m for r in reminders
                      if (m := re.search(r"can't cost less than \{(\w+)\}", r))), None)
        floor = _cost_icon(least[1]) if least else None
        if cost and floor:
            result.tables["FUSHIGIVERSE"] = (cost[0], floor[0], cost[1])
            result.rules_used.add("fushigiverse")
            text = text.replace(found[0], "■")
    # Metamorph: with enough mana, a bigger version of the first effect
    found = re.search(r"Metamorph\s*■?\s*Instead of the top card of your deck, put the top (\d+) "
                      r"cards of your deck into your mana zone\.?", text.replace("\u200b", ""))
    threshold = next((m for r in reminders
                      if (m := re.search(r"(\d+) or more cards in your mana zone", r))), None)
    if found and threshold:
        text = text.replace("\u200b", "").replace(found[0], "")
        result.tables["_metamorph"] = (int(threshold[1]), int(found[1]))
        result.rules_used.add("metamorph")
    return text


def _translate_face(face: Face, result: Translation, slug: str) -> list:
    """Translate one side. Returns the steps of its spell text, if it is a spell."""
    raw = _plain(face.text)
    if raw.count("[[") != raw.count("]]") or "[[" in raw:
        result.unknown.append("stored card text is cut off")
        return []
    body, reminders = _split_reminders(raw)
    if any(re.search(r"choose (?:[2-9]) of the following", _plain(face.text), re.IGNORECASE)
           for _ in (0,)) and "same one twice" not in raw.lower():
        result.unknown.append("choosing different options only is not supported")
    body = re.sub(r"(of the following)\.?\s*(?:You may choose the same one twice\.?)?\s*",
                  r"\1. ", body, flags=re.IGNORECASE)
    body = _special_paragraphs(body, reminders, result, face)
    # A list of modes belongs to the ability in front of it
    body = re.sub(r"\s*[■\u200b]+\s*►", " ►", body)

    spell = face.card_type.lower().endswith("spell")
    spell_steps: list = []
    paragraphs = _paragraphs(body)
    index = 0
    while index < len(paragraphs):
        paragraph = paragraphs[index]
        index += 1
        lowered = paragraph.lower()
        if lowered in _PLAIN_KEYWORDS:
            result.keywords.add(_PLAIN_KEYWORDS[lowered])
            if "HYPER_MODE" in result.tables:  # printed in the Hyper Mode part
                breakers = {"double breaker": 2, "triple breaker": 3}
                if lowered not in breakers:
                    result.unknown.append(f"Hyper Mode keyword: {paragraph}")
                else:
                    result.tables["HYPER_MODE"]["breaker"] = breakers[lowered]
                    result.keywords.discard(_PLAIN_KEYWORDS[lowered])
            continue
        if _match(r"hyperify: tap one of your other creatures", paragraph):
            if face.hyper_power is None:
                result.unknown.append("Hyper Mode power is not in the card data")
            result.tables["HYPER_MODE"] = {"power": face.hyper_power}
            result.rules_used.add("hyperify")
            continue

        for name, pattern, build in _STATIC_RULES:
            found = _match(pattern, paragraph)
            if found and build(found, result, face):
                result.rules_used.add(name)
                break
        else:
            hyper = "HYPER_MODE" in result.tables
            if not hyper and _triggered(paragraph, result, slug):
                continue
            if spell:
                steps = _parse_effect(paragraph, result, slug)
                if steps is not None:
                    spell_steps.extend(steps)
                    continue
            result.unknown.append(paragraph)
    return spell_steps


def _effect_races(steps) -> set[str]:
    found: set[str] = set()
    for _operation, params in steps:
        if "race" in params:
            found.add(params["race"])
        for key in ("then", "then_if_destroyed"):
            found |= _effect_races(params.get(key, ()))
    return found


def translate(slug: str, faces: list[Face], races=frozenset()) -> Translation:
    """
    Translate every side of one card. ``races`` lists the races that exist; a
    word that is not one of them is never accepted as a race.
    """
    result = Translation()
    if not faces:
        return result
    first = faces[0]
    spell_steps = _translate_face(first, result, slug)
    if spell_steps:
        morph = result.tables.pop("_metamorph", None)
        if morph:
            if spell_steps[0] != ("deck_to_mana", {"count": 1, "tapped": False}):
                result.unknown.append("Metamorph does not match the spell's first effect")
            spell_steps[0] = ("metamorph_mana",
                              {"needed": morph[0], "normal": 1, "morphed": morph[1]})
        result.abilities.append(Ability("cast", CAST, tuple(spell_steps), first.text))

    for other in faces[1:]:
        if other.text.strip() == first.text.strip():
            result.unknown.append("the data repeats one side, so the other side is missing")
            continue
        if not other.card_type.lower().endswith("spell"):
            result.unknown.append("a second side that is not a spell")
            continue
        side = Translation(modes=result.modes, rules_used=result.rules_used)
        steps = _translate_face(other, side, slug)
        result.unknown.extend(side.unknown)
        if side.tables or side.abilities or side.keywords:
            result.unknown.append("the spell side has abilities beyond its effect")
        if steps:
            result.abilities.append(Ability("twin_spell", QUEUED, tuple(steps), other.text))
            result.tables["TWINPACT_SPELLS"] = (other.cost, other.civilizations)

    for leftover in ("_element_limits", "_metamorph"):
        if leftover in result.tables:
            result.tables.pop(leftover)
            result.unknown.append(f"incomplete rule: {leftover[1:]}")
    keys: dict[str, int] = {}
    for position, ability in enumerate(result.abilities):
        keys[ability.key] = keys.get(ability.key, 0) + 1
        if keys[ability.key] > 1:
            result.abilities[position] = Ability(
                f"{ability.key}{keys[ability.key]}", ability.event, ability.steps,
                ability.text, watch=ability.watch)
    all_steps = [step for ability in result.abilities for step in ability.steps]
    all_steps += [s for table in result.modes.values() for mode in table.values() for s in mode]
    for term in sorted(result.race_terms | _effect_races(all_steps)):
        if not _is_race(term, races):
            result.unknown.append(f"not a race: {term}")
    return result
