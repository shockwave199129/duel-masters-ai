"""
engine/leave_rules.py - "when this would leave, ... instead" (rules 101.5, 609).

Only one replacement applies to one event (101.5, 609.3). When a single
compulsory replacement applies it happens at once. Otherwise the creature waits
in the battle zone while its controller chooses one (101.5b, 609.8).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.card_scripts import ETERNAL_OMEGA, LEAVE_REPLACEMENTS, LOSS_REPLACEMENTS
from core.continuous_rules import has_abilities, has_race
from core.enums import CardType, GameResult
from core.state import GameState

LEAVING_FLAG = "leaving"
DESTRUCTION_REASONS = frozenset({"destroyed", "battle", "sacrificed", "effect"})


@dataclass(frozen=True)
class Option:
    id: str           # "kind:source uid", also used to apply each one only once
    kind: str
    source_uid: str
    mandatory: bool = False


def leave_options(
    state: GameState, player: int, creature, *, reason: str, to_zone: str, used=(),
) -> list[Option]:
    """Every replacement that could apply to this creature leaving."""
    from engine.special_cards import should_apply_gneo_all_leave_replacement

    found: list[Option] = []
    working = has_abilities(creature)
    shield_uid = creature.temp_flags.get("ex_life_shield")
    p_state = state.players[player]
    if working and any(s.uid == shield_uid for s in p_state.shield_zone):
        found.append(Option(f"ex_life:{creature.uid}", "ex_life", creature.uid, True))
    if working and should_apply_gneo_all_leave_replacement(creature):
        found.append(Option(f"gneo:{creature.uid}", "gneo", creature.uid, True))
    if working and creature.definition.slug in ETERNAL_OMEGA and to_zone != "hand":
        found.append(Option(f"omega:{creature.uid}", "omega", creature.uid, True))

    destroyed = to_zone == "graveyard" and reason in DESTRUCTION_REASONS
    for source in p_state.battle_zone:
        rule = LEAVE_REPLACEMENTS.get(source.definition.slug)
        if rule is None or not has_abilities(source) or source.temp_flags.get(LEAVING_FLAG):
            continue
        if _applies(state, player, rule, source, creature, destroyed):
            found.append(Option(f"{rule['kind']}:{source.uid}", rule["kind"], source.uid))
    return [option for option in found if option.id not in used]


def _applies(state, player, rule, source, leaving, destroyed) -> bool:
    p_state = state.players[player]
    scope = rule["scope"]
    if scope == "self" and source.uid != leaving.uid:
        return False
    if rule.get("race") and not has_race(leaving.definition, rule["race"]):
        return False
    kind = rule["kind"]
    if kind == "saver":  # destroy the Saver instead of another creature being destroyed
        return (destroyed and source.uid != leaving.uid
                and rule["civilization"] in leaving.definition.civilizations
                and source.can_be_destroyed())
    # A replacement that cannot be carried out cannot be used (rule 609.4)
    if kind == "destroy_other":
        return any(_can_be_destroyed_instead(c, leaving.uid) for c in p_state.battle_zone)
    if kind == "discard":
        return len(p_state.hand) >= rule["count"]
    if kind == "mana":
        return len(p_state.mana_zone) >= rule["count"]
    if kind == "destroy_face_up":
        return len(face_up_cards(state, player, keep=leaving.uid)) >= rule["count"]
    if kind == "shuffle_grave":
        return len(p_state.graveyard) >= rule["count"]
    return False


def _can_be_destroyed_instead(creature, leaving_uid: str) -> bool:
    return (creature.uid != leaving_uid
            and creature.definition.card_type == CardType.CREATURE
            and not creature.temp_flags.get(LEAVING_FLAG)
            and creature.can_be_destroyed())


def face_up_cards(state: GameState, player: int, keep: str | None = None) -> list:
    """
    Cards that can be destroyed as "face-up cards": those in the battle zone and
    face-up shields. Mana does not count.
    """
    p_state = state.players[player]
    in_play = [
        c for c in p_state.battle_zone
        if c.uid != keep and not c.temp_flags.get(LEAVING_FLAG) and c.can_be_destroyed()
    ]
    return in_play + [s for s in p_state.shield_zone if s.is_face_up]


def lose_game(state: GameState, player: int) -> None:
    """This player would lose. Some cards let them replace that."""
    for source in state.players[player].battle_zone:
        needed = LOSS_REPLACEMENTS.get(source.definition.slug)
        if (needed and has_abilities(source) and not source.temp_flags.get(LEAVING_FLAG)
                and len(face_up_cards(state, player)) >= needed):
            from engine.shield_break_window import rules_trigger
            state.players[player].loss_pending = True
            trigger = rules_trigger("decide_loss", source.uid, source.id, player)
            trigger.trigger_data["count"] = needed
            state.effect_stack.add_immediate([trigger])
            return
    declare_loss(state, player)


def declare_loss(state: GameState, player: int) -> None:
    state.result = GameResult.PLAYER_1_WINS if player == 0 else GameResult.PLAYER_0_WINS


def intercept_leave(
    state: GameState, player: int, creature, *, reason: str, to_zone: str, used=(),
) -> bool:
    """
    Apply or offer a replacement. Returns True if the creature stays for now:
    either something replaced the move, or its controller has yet to choose.
    """
    if creature.temp_flags.get(LEAVING_FLAG):
        return True
    options = leave_options(state, player, creature, reason=reason, to_zone=to_zone, used=used)
    if not options:
        return False
    event = {"uid": creature.uid, "player": player, "reason": reason, "to_zone": to_zone,
             "used": tuple(used)}
    if len(options) == 1 and options[0].mandatory and options[0].kind != "omega":
        apply_simple(state, player, creature, options[0], event)
        return True

    from engine.shield_break_window import rules_trigger
    creature.temp_flags[LEAVING_FLAG] = True
    trigger = rules_trigger("decide_leave", creature.uid, creature.id, player)
    trigger.trigger_data["leave"] = event
    state.effect_stack.add_immediate([trigger])
    return True


def apply_simple(state: GameState, player: int, creature, option: Option, event: dict) -> None:
    """Replacements that need no further choice."""
    from engine import zone_mover

    if option.kind == "ex_life":
        zone_mover.spend_ex_life(state, player, creature)
    elif option.kind == "gneo":
        zone_mover.gneo_leave(
            state, player, creature, reason=event["reason"],
            place=zone_mover.place_in_zone(state, player, event["to_zone"], event["reason"]),
        )
    elif option.kind == "omega":
        zone_mover.leave_battle_zone(
            state, player, creature, reason="returned", to_zone="hand")
