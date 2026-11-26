"""
core/continuous_rules.py — continuous card abilities that other rules must ask about.

Imports only core modules, so both core and engine code can use it.
"""

from __future__ import annotations

from core.card_scripts import (
    CHOICE_PROTECTION, CIVIL_COUNT_SHIELD_TRIGGER, CREATURE_ONLY_WITH_DARKNESS, GRANTS,
    GRAVEYARD_CREATURES_RUSH,
    NAMES_TO_IGNORE, NEW_CREATURES_CANT_ATTACK_YOU, OPPONENT_CANT_CAST,
    WEAKER_CANT_ATTACK_YOU,
)
from core.enums import CardType, Civilization, Keyword
from core.state import PendingTrigger

IGNORED_FLAG = "abilities_ignored"
GRANTED_FLAG = "granted_keywords"
NAMED_FLAG = "ignored_name"


def has_race(definition, word: str) -> bool:
    """Does one of the card's races include this word or phrase (rule 203.2)?"""
    return any(f" {word} " in f" {race} " for race in definition.races)


def ignored_names(state) -> set[str]:
    """Card names whose abilities are ignored by creatures in the battle zone."""
    return {
        creature.temp_flags[NAMED_FLAG]
        for player in state.players
        for creature in player.battle_zone
        if creature.definition.slug in NAMES_TO_IGNORE and NAMED_FLAG in creature.temp_flags
    }


def sync_ignored_abilities(state) -> None:
    """
    Mark every creature whose abilities are currently ignored.

    Run whenever the battle zone may have changed, so abilities come back when
    the card that named them leaves.
    """
    names = ignored_names(state)
    for player in state.players:
        for creature in player.battle_zone:
            if creature.definition.name in names:
                creature.temp_flags[IGNORED_FLAG] = True
            else:
                creature.temp_flags.pop(IGNORED_FLAG, None)
    sync_granted_keywords(state)


def sync_granted_keywords(state) -> None:
    """Give creatures the keywords other cards currently grant them."""
    granted_to: dict[str, set[str]] = {}
    for index, player in enumerate(state.players):
        for source in player.battle_zone:
            rules = GRANTS.get(source.definition.slug)
            if not rules or not has_abilities(source) or source.is_ignored:
                continue
            for whose, race, keywords in rules:
                if whose == "self":
                    targets = [source]
                else:
                    targets = state.players[index if whose == "own" else 1 - index].battle_zone
                for target in targets:
                    if race is None or has_race(target.definition, race):
                        granted_to.setdefault(target.uid, set()).update(k.value for k in keywords)
    for player in state.players:
        for creature in player.battle_zone:
            words = granted_to.get(creature.uid, set())
            if "blocker_until_turn_of" in creature.temp_flags:
                words = words | {Keyword.BLOCKER.value}
            if words:
                creature.temp_flags[GRANTED_FLAG] = frozenset(words)
            else:
                creature.temp_flags.pop(GRANTED_FLAG, None)


def shield_has_trigger(state, player: int, definition) -> bool:
    """Shield Trigger, printed or gained through Civil Count."""
    if definition.has_shield_trigger():
        return True
    needed = CIVIL_COUNT_SHIELD_TRIGGER.get(definition.slug)
    if needed is None:
        return False
    civilization, count = needed
    return creatures_and_tamaseeds(state, player, civilization) >= count


def has_abilities(creature) -> bool:
    return not creature.temp_flags.get(IGNORED_FLAG, False)


def can_be_chosen(state, creature, chooser: int) -> bool:
    """May this player choose this creature? Attacking and blocking are not choosing."""
    race = CHOICE_PROTECTION.get(creature.definition.slug)
    if race is None or chooser == creature.controller or not has_abilities(creature):
        return True
    return not any(
        other.uid != creature.uid and any(race in r for r in other.definition.races)
        for other in state.players[creature.controller].battle_zone
    )


def sources_of(state, ability_key: str) -> list:
    """Creatures in the battle zone that have this engine-queued ability."""
    suffix = f"#{ability_key}"
    return [
        creature
        for player in state.players
        for creature in player.battle_zone
        if has_abilities(creature) and any(
            effect.effect_value.get("script", "").endswith(suffix)
            for effect in creature.definition.effects
        )
    ]


def queue_ability(state, definition, ability_key: str, *, source_uid: str, controller: int, data: dict) -> None:
    """Queue a scripted ability that the engine, not a trigger event, sets off."""
    for effect in definition.effects:
        if effect.effect_value.get("script", "").endswith(f"#{ability_key}"):
            state.effect_stack.add_trigger(PendingTrigger(
                effect=effect, source_uid=source_uid, source_card_id=definition.id,
                controller=controller, trigger_data=dict(data),
            ))


def controls(state, player: int, slugs) -> bool:
    """Does this player have one of these cards, with its abilities working?"""
    return count_of(state, player, slugs) > 0


def count_of(state, player: int, slugs) -> int:
    return sum(
        1 for creature in state.players[player].battle_zone
        if creature.definition.slug in slugs and has_abilities(creature) and not creature.is_ignored
    )


def spells_locked(state, player: int) -> bool:
    return controls(state, 1 - player, OPPONENT_CANT_CAST)


def is_creature_now(state, creature) -> bool:
    """Only creatures count; some Tamaseeds are creatures only with enough darkness."""
    if creature.definition.card_type != CardType.CREATURE:
        return False  # fields, cross gear and other elements (rule 505.3)
    needed = CREATURE_ONLY_WITH_DARKNESS.get(creature.definition.slug)
    if needed is None or not has_abilities(creature):
        return True
    return creatures_and_tamaseeds(state, creature.controller, Civilization.DARKNESS) >= needed


def creatures_and_tamaseeds(state, player: int, civilization) -> int:
    """This player's creatures and Tamaseeds of a civilization; other cards don't count."""
    return sum(
        1 for card in state.players[player].battle_zone
        if civilization in card.definition.civilizations
        and card.definition.card_type in (CardType.CREATURE, CardType.TAMASEED)
    )


def has_graveyard_rush(state, creature) -> bool:
    """Put from the graveyard this turn, while a card removes their summoning sickness."""
    return (
        creature.temp_flags.get("from_graveyard_turn") == state.turn_number
        and controls(state, creature.controller, GRAVEYARD_CREATURES_RUSH)
    )


def may_attack_player(state, attacker, defender: int) -> bool:
    for guard in state.players[defender].battle_zone:
        if not has_abilities(guard):
            continue
        slug = guard.definition.slug
        if slug in WEAKER_CANT_ATTACK_YOU and attacker.compute_power(state) < guard.compute_power(state):
            return False
        if slug in NEW_CREATURES_CANT_ATTACK_YOU and attacker.entered_turn == state.turn_number:
            return False
    return True


def granted(state, player: int, grants: dict) -> set[str]:
    """Races this player's cards currently grant something to."""
    return {value for slug, value in grants.items() if controls(state, player, {slug})}

