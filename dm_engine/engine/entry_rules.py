"""
engine/entry_rules.py — rules about how cards are put, summoned and cast.

Several cards care about this: whether a creature was summoned or "put", whether
mana was paid, and which zone it came from.
"""

from __future__ import annotations

from core.card_scripts import (
    DESTROYS_UNSUMMONED, ELEMENTS_ONLY_PLAYED_FROM_HAND, FREE_PLAY_KEY,
    HAND_AT_OPPONENT_TURN_END, OPPONENT_RACES_TO_GRAVEYARD, UNSUMMONED_TO_MANA,
)
from core.continuous_rules import controls, has_race, is_creature_now, queue_ability, sources_of
from core.enums import CardSubtype
from core.state import GameState


def may_enter(
    state: GameState, player: int, *, from_hand: bool, played: bool, definition=None,
) -> bool:
    """May this player's element enter the battle zone this way?"""
    if (definition is not None and definition.card_subtype == CardSubtype.DREAM
            and any(c.definition.name == definition.name for c in state.players[player].battle_zone)):
        return False  # rule 817.2
    if controls(state, 1 - player, ELEMENTS_ONLY_PLAYED_FROM_HAND):
        # "Can't put elements from anywhere except hand, or except by playing them"
        return from_hand and played
    return True


def entry_redirect(state: GameState, player: int, definition, *, summoned: bool) -> str | None:
    """Where a creature goes instead of the battle zone, if a card says so."""
    if not definition.is_creature():
        return None
    for slug, races in OPPONENT_RACES_TO_GRAVEYARD.items():
        if (controls(state, 1 - player, {slug})
                and any(has_race(definition, race) for race in races)):
            return "graveyard"
    if not summoned and any(
        shield.is_face_up and shield.definition.slug in UNSUMMONED_TO_MANA
        for shield in state.players[1 - player].shield_zone
    ):
        return "mana_zone"
    return None


def after_enter(
    state: GameState,
    creature,
    player: int,
    *,
    summoned: bool,
    paid: bool,
    from_graveyard: bool = False,
) -> None:
    """Apply what watches a card arriving in the battle zone."""
    p_state = state.players[player]
    if from_graveyard:
        creature.temp_flags["from_graveyard_turn"] = state.turn_number
    if creature.definition.is_creature():
        p_state.creatures_put_this_turn += 1
    if not paid:
        free_play(state, player)
    # "During your opponent's turn, when they put a creature other than by
    # summoning it, destroy it"
    if (
        not summoned
        and state.active_player == player
        and creature.definition.is_creature()
        and controls(state, 1 - player, DESTROYS_UNSUMMONED)
        and creature.can_be_destroyed()
    ):
        from engine.zone_mover import move_battle_to_graveyard
        move_battle_to_graveyard(state, player, creature.uid, reason="effect")


def free_play(state: GameState, player: int) -> None:
    """A creature or cross gear was put, or a spell cast, without tapping mana."""
    state.players[player].played_free_this_turn = True
    for watcher in sources_of(state, FREE_PLAY_KEY):
        queue_ability(
            state, watcher.definition, FREE_PLAY_KEY, source_uid=watcher.uid,
            controller=watcher.controller, data={"player": player},
        )


def offer_from_hand_at_turn_end(state: GameState, turn_player: int) -> None:
    """Cards the non-turn player may play from hand as the turn ends."""
    waiting = 1 - turn_player
    for card in list(state.players[waiting].hand):
        condition = HAND_AT_OPPONENT_TURN_END.get(card.definition.slug)
        if condition is None:
            continue
        if condition["kind"] == "creatures_put":
            met = sum(p.creatures_put_this_turn for p in state.players) >= condition["count"]
        else:  # the turn player played something for free, and you lack this creature
            met = state.players[turn_player].played_free_this_turn and not any(
                c.definition.slug == card.definition.slug for c in state.players[waiting].battle_zone
            )
        if met:
            queue_ability(
                state, card.definition, condition["ability"], source_uid=card.uid,
                controller=waiting, data={},
            )


def tap_new_mana(before: list[set[str]], state: GameState) -> None:
    """"Your opponent's cards are put into their mana zone tapped.\""""
    from core.card_scripts import OPPONENT_MANA_ENTERS_TAPPED

    for player in (0, 1):
        if not controls(state, 1 - player, OPPONENT_MANA_ENTERS_TAPPED):
            continue
        for mana in state.players[player].mana_zone:
            if mana.uid not in before[player]:
                mana.is_tapped = True


__all__ = [
    "after_enter", "free_play", "is_creature_now", "may_enter",
    "offer_from_hand_at_turn_end", "tap_new_mana",
]
