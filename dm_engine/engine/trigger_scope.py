"""
engine/trigger_scope.py — whose event a triggered ability reacts to.

Parsed effects often have no trigger condition, so without this check "when you
put THIS creature" would run for every creature that entered. The printed text
of the ability says which cards it watches. When the text names something this
module cannot check, the ability does not trigger: doing nothing is safer than
triggering for the wrong card.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.cards import CardDefinition, CardEffect
from core.enums import Civilization, TriggerEvent

# Events that are about one particular card (two for a battle)
CARD_EVENTS = frozenset({
    TriggerEvent.ON_ENTER_BATTLE_ZONE,
    TriggerEvent.ON_SUMMON,
    TriggerEvent.ON_ATTACK,
    TriggerEvent.ON_BLOCK,
    TriggerEvent.ON_DESTROY,
    TriggerEvent.ON_LEAVE_BATTLE_ZONE,
    TriggerEvent.ON_BATTLE,
    TriggerEvent.ON_WIN_BATTLE,
})


@dataclass(frozen=True)
class Watch:
    who: str                 # "self" | "own" | "opponent" | "any" | "never"
    kind: str = ""           # civilization or race the other card must have
    others_only: bool = False


_SELF = Watch("self")
_NEVER = Watch("never")

_LINK = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]")
_CLAUSE = re.compile(r"\b(?:when|whenever|each time)\b(.*)")
_REMINDER = re.compile(r"\([^()]*\)")
# Keywords with their own rules. Their text mentions attacks but is not a trigger.
_KEYWORD_ABILITIES = (
    "blocker", "guardman", "ninja strike", "attack chance", "invasion", "s-rank invasion",
    "revolution change", "guard strike", "cross gear", "saver",
)
# "<owner> <verb> a <kind>", e.g. "you summon a dragon"
_PLAYER_ACTS = re.compile(
    r"^(you|your opponent|a player)\s+(?:summons?|puts?)\s+(a|an|another)\s+(.+?)(?:\s+in(?:to)?\b|[,.]|$)"
)
# "<one of your> <kind> <verb>", e.g. "one of your fire creatures attacks"
_CARD_ACTS = re.compile(
    r"^(one of your opponent's|your opponent's|an opponent's|one of your|any of your|your|another of your|"
    r"a|an|another)\s+(.+?)\s+(?:attacks?|blocks?|enters?|leaves?|is|are|would|battles?|wins?)\b"
)


def _clean(text: str) -> str:
    text = _LINK.sub(r"\1", text or "").replace("'''", "").replace("’", "'")
    return " ".join(text.split()).lower()


def _without_reminders(text: str) -> str:
    """Drop parenthesized reminder text, including nested or unclosed parts."""
    previous = None
    while previous != text:
        previous, text = text, _REMINDER.sub(" ", text)
    return " ".join(text.split("(")[0].split())


def _kind_of(phrase: str) -> str | None:
    """The civilization or race in "fire creatures" ("" for any creature), or None if unreadable."""
    words = phrase.split()
    if words and words[0] == "other":
        words = words[1:]
    if words and words[-1] in ("creature", "creatures"):
        words = words[:-1]
    if not words:
        return ""
    if len(words) > 2 or any(w in ("spell", "spells", "card", "cards", "that", "with", "or") for w in words):
        return None
    return " ".join(words).rstrip("s")


def classify(raw_text: str) -> Watch:
    """Read which cards an ability watches from its printed text."""
    text = _clean(raw_text)
    if text.lstrip("\u25a0\u200b ").startswith(_KEYWORD_ABILITIES):
        return _NEVER
    text = _without_reminders(text)
    if "would" in text and "instead" in text:
        return _NEVER  # a replacement effect stored as a trigger
    match = _CLAUSE.search(text)
    if match is None:
        return _SELF
    clause = match.group(1).strip()
    if re.match(r"^(?:(?:you|your opponent) (?:put|puts|summon|summons) )?this\b", clause):
        return _SELF
    if re.match(r"^(?:you|your opponent|a player) casts?\b", clause):
        return _NEVER  # about spells, so a creature event is the wrong trigger

    for pattern in (_PLAYER_ACTS, _CARD_ACTS):
        found = pattern.match(clause)
        if not found:
            continue
        owner, phrase = found.group(1), found.group(found.re.groups)
        another = "another" in found.groups()[:-1] or owner.startswith("another")
        kind = _kind_of(phrase)
        if kind is None:
            return _NEVER
        if "opponent" in owner:
            who = "opponent"
        elif owner in ("you", "one of your", "any of your", "your", "another of your"):
            who = "own"
        else:
            who = "any"
        return Watch(who, kind, others_only=another or phrase.startswith("other"))
    # Unrecognized wording, such as a card's own name: treat it as this card
    return _SELF


def _matches_kind(definition: CardDefinition, kind: str) -> bool:
    if not kind:
        return True
    if kind == "colorless":
        return not definition.civilizations
    civilizations = {c.value.lower(): c for c in Civilization}
    if kind in civilizations:
        return civilizations[kind] in definition.civilizations
    # Whole words only: "Dragonoid" is not a "Dragon" (rule 203.2)
    return any(f" {kind} " in f" {race.lower()} " for race in definition.races)


def _watch_for(effect: CardEffect) -> Watch:
    scripted = effect.effect_value.get("watch") if effect.effect_value.get("script") else None
    if scripted in ("own_abyss", "own_abyss_hyper"):
        return Watch("own", "abyss")
    if scripted == "self_hyper":
        return Watch("self")
    if scripted == "own_dragon_summon":
        return Watch("own", "dragon")
    if scripted:
        return Watch(scripted)
    return classify(effect.raw_text)


def watches(
    state,
    event: TriggerEvent,
    effect: CardEffect,
    source_uid: str,
    source_card,
    subject_uid: str | None,
    trigger_data: dict,
) -> bool:
    """Should this ability, on the card ``source_uid``, react to this event?"""
    # A Hyper Mode ability does nothing until Hyper Mode is released (rule 816.3)
    if (str(effect.effect_value.get("watch", "")).endswith("_hyper")
            and not getattr(source_card, "hyper_mode_released", False)):
        return False
    if effect.effect_value.get("script") and effect.effect_value.get("watch") == "own_cast":
        return trigger_data.get("controller") == source_card.controller
    if effect.effect_value.get("script") and effect.effect_value.get("watch") == "own_turn_end_after_cast":
        # Checked as the turn ends, while this turn's spell count still stands
        return (trigger_data.get("controller") == source_card.controller
                and state.players[source_card.controller].spells_cast_this_turn > 0)
    if effect.effect_value.get("script") and effect.effect_value.get("watch") == "own_turn_end":
        return trigger_data.get("controller") == source_card.controller
    if event not in CARD_EVENTS:
        return True
    subjects = {subject_uid}
    if event == TriggerEvent.ON_BATTLE:
        subjects |= {trigger_data.get("attacker_uid"), trigger_data.get("defender_uid")}
    subjects.discard(None)
    if not subjects:
        return True  # the event does not say which card it is about

    watch = _watch_for(effect)
    if watch.who == "never":
        return False
    if watch.who == "self":
        return source_uid in subjects
    for uid in subjects:
        if watch.others_only and uid == source_uid:
            continue
        found = state.find_creature_anywhere(uid)
        if found is None:
            continue
        controller, creature = found
        if watch.who == "own" and controller != source_card.controller:
            continue
        if watch.who == "opponent" and controller == source_card.controller:
            continue
        if _matches_kind(creature.definition, watch.kind):
            return True
    return False
