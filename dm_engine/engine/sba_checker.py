"""
engine/sba_checker.py — State-Based Actions checker.

Rule 703: "The game constantly checks whether any of the conditions for
state-based actions to occur are met, and if any apply, it processes all
those state-based actions simultaneously as a single event. If state-based
actions occur as a result of a check, the check is repeated again after
the processing."

The 13 SBAs from rules 703.4a–703.4m are implemented here as pure functions.
Each SBA takes a GameState and returns a (possibly modified) GameState.

Entry point:
    check_state_based_actions(state) -> GameState

Called after EVERY action and after EVERY effect resolves.
Loops until no SBA fires (rule 703.3: repeat if any SBA triggered).
"""

from __future__ import annotations

from core.enums import (
    GameResult, CardType, CardSubtype, Phase
)
from core.state import GameState
from core.zones import Creature, GraveyardCard


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def check_state_based_actions(state: GameState) -> GameState:
    """
    Rule 703.3: check all SBAs and repeat until none fire.
    Returns a new GameState (never mutates input).
    """
    while True:
        new_state, any_fired = _check_once(state)
        if not any_fired:
            return new_state
        state = new_state


def _check_once(state: GameState) -> tuple[GameState, bool]:
    """
    Run one simultaneous SBA check. Returns (new_state, any_fired).
    """
    events = _collect_sba_events(state)
    if not _has_sba_events(events):
        return state.copy(), False
    return _apply_sba_events(state, events), True


def _collect_sba_events(state: GameState) -> dict:
    """
    Collect every currently applicable SBA from an unchanged snapshot.
    Rule 703.3 requires applying these simultaneously as one event.
    """
    events = {
        "losers": set(),
        "destroy": [],      # (player_idx, creature_uid, reason)
        "graveyard": [],    # (player_idx, card_uid, reason)
        "seal_removal": [], # (player_idx, sealed_creature_uid)
        "d2_remove": [],    # (player_idx, field_uid)
        "castles": [],      # (player_idx, castle_defn)
    }

    if state.is_terminal():
        return events

    ctx = state.attack_context
    if ctx is not None and ctx.received_direct_attack:
        events["losers"].add(ctx.defending_player)

    for player_idx in range(2):
        player = state.players[player_idx]
        if player.deck_size == 0:
            events["losers"].add(player_idx)

        for creature in player.battle_zone:
            is_creature = creature.definition.card_type == CardType.CREATURE
            if creature.is_ignored:
                continue
            if is_creature and creature.compute_power(state) <= 0 and creature.can_be_destroyed():
                events["destroy"].append((player_idx, creature.uid, "sba_zero_power"))
            if is_creature and creature.temp_flags.get("lost_battle", False) and creature.can_be_destroyed():
                events["destroy"].append((player_idx, creature.uid, "battle"))

            if creature.definition.card_type in {CardType.CELL, CardType.WEAPON}:
                reason = (
                    "sba_standalone_cell"
                    if creature.definition.card_type == CardType.CELL
                    else "sba_standalone_weapon"
                )
                events["graveyard"].append((player_idx, creature.uid, reason))

            if creature.definition.card_type in {CardType.SPELL, CardType.CASTLE, CardType.CORE}:
                events["graveyard"].append((player_idx, creature.uid, "sba_invalid_type"))

        if player.detached_castles:
            for castle_defn in player.detached_castles:
                events["castles"].append((player_idx, castle_defn))

        d2_fields = [
            c for c in player.battle_zone
            if (c.definition.card_type == CardType.FIELD
                and c.definition.card_subtype == CardSubtype.D2)
        ]
        if len(d2_fields) > 1:
            newest = next(
                (f for f in d2_fields if f.temp_flags.get("just_entered", False)),
                d2_fields[-1],
            )
            for old_field in d2_fields:
                if old_field.uid != newest.uid:
                    events["d2_remove"].append((player_idx, old_field.uid))

        for command in player.battle_zone:
            if not command.temp_flags.get("just_entered_as_command", False):
                continue
            cmd_civs = command.civilizations
            for target in player.battle_zone:
                if target.seals and target.civilizations.intersection(cmd_civs):
                    events["seal_removal"].append((player_idx, target.uid))
                    break

    return events


def _has_sba_events(events: dict) -> bool:
    return any(bool(value) for value in events.values())


def _apply_sba_events(state: GameState, events: dict) -> GameState:
    """Apply a collected SBA batch to a copied state."""
    s = state.copy()

    if events["losers"]:
        if len(events["losers"]) == 1:
            loser = next(iter(events["losers"]))
            winner = 1 - loser
            s.result = (
                GameResult.PLAYER_0_WINS if winner == 0
                else GameResult.PLAYER_1_WINS
            )
        else:
            s.result = GameResult.DRAW

    destroyed: set[tuple[int, str]] = set()
    for player_idx, creature_uid, reason in events["destroy"]:
        key = (player_idx, creature_uid)
        if key in destroyed:
            continue
        creature = s.players[player_idx].find_creature(creature_uid)
        if creature is None:
            continue
        creature.clear_flag("lost_battle")
        _destroy_creature(s, player_idx, creature, reason)
        destroyed.add(key)

    moved_to_graveyard: set[tuple[int, str]] = set()
    for player_idx, creature_uid, reason in events["graveyard"]:
        key = (player_idx, creature_uid)
        if key in moved_to_graveyard or key in destroyed:
            continue
        creature = s.players[player_idx].find_creature(creature_uid)
        if creature is None:
            continue
        s.players[player_idx].battle_zone.remove(creature)
        s.players[player_idx].graveyard.insert(
            0,
            GraveyardCard(
                definition=creature.definition,
                uid=creature.uid,
                died_from=reason,
                died_on_turn=s.turn_number,
            )
        )
        moved_to_graveyard.add(key)

    for player_idx, target_uid in events["seal_removal"]:
        target = s.players[player_idx].find_creature(target_uid)
        if target is None or not target.seals:
            continue
        seal_defn = target.seals.pop(0)
        s.players[player_idx].graveyard.insert(
            0,
            GraveyardCard(
                definition=seal_defn,
                died_from="sba_seal_removal",
                died_on_turn=s.turn_number,
            )
        )

    for player_idx, castle_defn in events["castles"]:
        s.players[player_idx].graveyard.insert(
            0,
            GraveyardCard(
                definition=castle_defn,
                died_from="sba_castle_detach",
                died_on_turn=s.turn_number,
            )
        )
        s.players[player_idx].detached_castles = []

    for player_idx, field_uid in events["d2_remove"]:
        field = s.players[player_idx].find_creature(field_uid)
        if field is None:
            continue
        s.players[player_idx].battle_zone.remove(field)
        s.players[player_idx].graveyard.insert(
            0,
            GraveyardCard(
                definition=field.definition,
                uid=field.uid,
                died_from="sba_d2_field",
                died_on_turn=s.turn_number,
            )
        )
        s.global_effects.remove_by_source(field.uid)

    for player_idx in range(2):
        for creature in s.players[player_idx].battle_zone:
            creature.clear_flag("just_entered")
            creature.clear_flag("just_entered_as_command")

    return s


# ─────────────────────────────────────────────────────────────────────────────
# Individual SBA implementations
# ─────────────────────────────────────────────────────────────────────────────

def _sba_direct_attack(state: GameState) -> bool:
    """
    Rule 703.4a: A player who received a Direct Attack loses the game.
    This is checked when the attack context indicates a direct attack
    was completed (shield_count == 0 and attack targeted the player).
    """
    if state.is_terminal():
        return False

    ctx = state.attack_context
    if ctx is None:
        return False

    # Direct attack = targeting player AND defender has 0 shields
    if not ctx.is_attacking_player:
        return False

    defender = ctx.defending_player
    if state.players[defender].shield_count == 0 and ctx.shields_broken >= 0:
        # Check if this attack actually reached the player
        if state.current_phase in (Phase.DIRECT_ATTACK, Phase.END_OF_ATTACK):
            if state.players[defender].shield_count == 0:
                winner = 1 - defender
                state.result = (
                    GameResult.PLAYER_0_WINS if winner == 0
                    else GameResult.PLAYER_1_WINS
                )
                return True

    return False


def _sba_deck_empty(state: GameState) -> bool:
    """
    Rule 703.4b: A player whose deck has reached 0 cards loses the game.
    Rule 104.2b: "If there are 0 cards in the deck even for a split second
    during the processing of an effect, it is considered 0 cards."
    """
    if state.is_terminal():
        return False

    for i in range(2):
        if state.players[i].deck_size == 0:
            # The player who runs out loses
            winner = 1 - i
            state.result = (
                GameResult.PLAYER_0_WINS if winner == 0
                else GameResult.PLAYER_1_WINS
            )
            return True

    return False


def _sba_zero_power(state: GameState) -> bool:
    """
    Rule 703.4c: A creature with power 0 or less is destroyed.
    Rule 700.3: if a creature "cannot be destroyed", it is not destroyed even
    at 0 power (the cannot_be_destroyed flag overrides this SBA).
    """
    fired = False
    for player_idx in range(2):
        to_destroy = []
        for creature in state.players[player_idx].battle_zone:
            if creature.is_ignored:
                continue  # sealed creatures are not evaluated for power
            power = creature.compute_power(state)
            if power <= 0 and creature.can_be_destroyed():
                to_destroy.append(creature)

        for creature in to_destroy:
            _destroy_creature(state, player_idx, creature, "sba_zero_power")
            fired = True

    return fired


def _sba_battle_loser(state: GameState) -> bool:
    """
    Rule 703.4d: A creature that lost a battle is destroyed.
    The 'lost_battle' flag is set by the battle resolver on the
    creature that had lower power (or was slain by Slayer).
    """
    fired = False
    for player_idx in range(2):
        to_destroy = [
            c for c in state.players[player_idx].battle_zone
            if c.temp_flags.get("lost_battle", False) and c.can_be_destroyed()
        ]
        for creature in to_destroy:
            creature.clear_flag("lost_battle")
            _destroy_creature(state, player_idx, creature, "battle")
            fired = True

    return fired


def _sba_standalone_cell(state: GameState) -> bool:
    """
    Rule 703.4g: A standalone Cell in the Battle Zone is placed in the Graveyard.
    Cells are component cards of combined creatures. They cannot exist alone.
    """
    fired = False
    for player_idx in range(2):
        to_remove = [
            c for c in state.players[player_idx].battle_zone
            if c.definition.card_type == CardType.CELL
        ]
        for creature in to_remove:
            state.players[player_idx].battle_zone.remove(creature)
            state.players[player_idx].graveyard.insert(
                0, GraveyardCard(definition=creature.definition,
                                  died_from="sba_standalone_cell",
                                  died_on_turn=state.turn_number)
            )
            fired = True

    return fired


def _sba_invalid_type(state: GameState) -> bool:
    """
    Rule 703.4i: A face-up card in the Battle Zone that does not have a
    valid type is placed in the Graveyard.

    Valid standalone types in the battle zone (rule 316, 403.1):
    Creature, Cross Gear, Weapon (attached), Fortress, Heartbeat,
    Field, Aura (attached), Ritual, Nebula, Artifact, Tamaseed.

    Invalid standalone: Spell, Castle, Core (alone), Weapon (alone),
    Aura (alone).
    """
    INVALID_STANDALONE = {
        CardType.SPELL,
        CardType.CASTLE,
        CardType.CORE,   # rule 309.7: Core cannot exist standalone
    }
    fired = False
    for player_idx in range(2):
        to_remove = [
            c for c in state.players[player_idx].battle_zone
            if c.definition.card_type in INVALID_STANDALONE
        ]
        for creature in to_remove:
            state.players[player_idx].battle_zone.remove(creature)
            state.players[player_idx].graveyard.insert(
                0, GraveyardCard(definition=creature.definition,
                                  died_from="sba_invalid_type",
                                  died_on_turn=state.turn_number)
            )
            fired = True

    return fired


def _sba_seal_removal(state: GameState) -> bool:
    """
    Rule 703.4j: When a Command enters the Battle Zone, its owner places
    one seal into the Graveyard from among the cards with seals attached
    that share the same civilization as that Command.

    This SBA fires when a creature flagged as "just_entered_as_command"
    is present. The flag is set by the action executor when a Command
    creature enters the battle zone.
    """
    fired = False
    for player_idx in range(2):
        for creature in state.players[player_idx].battle_zone:
            if not creature.temp_flags.get("just_entered_as_command", False):
                continue

            creature.clear_flag("just_entered_as_command")
            cmd_civs = creature.civilizations

            # Find all creatures on THIS player's side with seals of matching civ
            # Rule 703.4j: "cards with seals attached that share the same civilization
            # as that Command" — seals are on the owner's own side
            for target in state.players[player_idx].battle_zone:
                if not target.seals:
                    continue
                # Check if any seal shares civilization with the command
                # Rule 116.2: we reference the sealed creature's civilizations
                # even though it's ignored
                if target.civilizations.intersection(cmd_civs):
                    # Remove one seal (rule 703.4j: owner chooses which creature's seal)
                    # Simplified: remove first seal from first matching creature
                    seal_defn = target.seals.pop(0)
                    state.players[player_idx].graveyard.insert(
                        0, GraveyardCard(definition=seal_defn,
                                          died_from="sba_seal_removal",
                                          died_on_turn=state.turn_number)
                    )
                    fired = True
                    break  # only ONE seal per Command entry

    return fired


def _sba_castle_graveyard(state: GameState) -> bool:
    """
    Rule 703.4k: When a fortified shield leaves the Shield Zone,
    the Castle is placed in the owner's Graveyard.

    Tracked by the "detached_castle" list on PlayerState.
    The action executor populates this when a shield is broken.
    """
    fired = False
    for player_idx in range(2):
        p = state.players[player_idx]
        if p.detached_castles:
            for castle_defn in p.detached_castles:
                p.graveyard.insert(
                    0, GraveyardCard(definition=castle_defn,
                                      died_from="sba_castle_detach",
                                      died_on_turn=state.turn_number)
                )
            p.detached_castles = []
            fired = True

    return fired


def _sba_d2_field(state: GameState) -> bool:
    """
    Rule 703.4l: When another D2 Field enters the Battle Zone, the D2 Field
    that was previously in the Battle Zone is placed in its owner's Graveyard.

    Only ONE D2 Field can exist per player. If a second one is played,
    the old one is immediately destroyed.
    """
    fired = False
    for player_idx in range(2):
        d2_fields = [
            c for c in state.players[player_idx].battle_zone
            if (c.definition.card_type == CardType.FIELD
                and c.definition.card_subtype == CardSubtype.D2)
        ]
        if len(d2_fields) > 1:
            # Keep the most recently entered (flagged by just_entered)
            # If no flag, keep the last in the list (most recent)
            newest = None
            for f in d2_fields:
                if f.temp_flags.get("just_entered", False):
                    newest = f
                    break
            if newest is None:
                newest = d2_fields[-1]

            for old_field in d2_fields:
                if old_field is not newest:
                    state.players[player_idx].battle_zone.remove(old_field)
                    state.players[player_idx].graveyard.insert(
                        0, GraveyardCard(definition=old_field.definition,
                                          died_from="sba_d2_field",
                                          died_on_turn=state.turn_number)
                    )
                    # Remove global effects from the old field
                    state.global_effects.remove_by_source(old_field.uid)
                    fired = True

            # Clear the just_entered flag
            if newest:
                newest.clear_flag("just_entered")

    return fired


def _sba_standalone_weapon(state: GameState) -> bool:
    """
    Rule 703.4m: A standalone Weapon in the Battle Zone is placed in the Graveyard.
    Weapons (Dragheart Weapons) must be equipped to a creature.
    """
    fired = False
    for player_idx in range(2):
        to_remove = [
            c for c in state.players[player_idx].battle_zone
            if c.definition.card_type == CardType.WEAPON
        ]
        for weapon in to_remove:
            state.players[player_idx].battle_zone.remove(weapon)
            state.players[player_idx].graveyard.insert(
                0, GraveyardCard(definition=weapon.definition,
                                  died_from="sba_standalone_weapon",
                                  died_on_turn=state.turn_number)
            )
            fired = True

    return fired


# ─────────────────────────────────────────────────────────────────────────────
# Turn limit helper (deprecated; max_steps handles training cutoffs)
# ─────────────────────────────────────────────────────────────────────────────

def check_turn_limit(state: GameState) -> GameState:
    """
    No-op compatibility helper.

    Duel Masters games should end by win/loss conditions. Training runners use
    max_steps to stop long simulations and mark them unfinished instead of
    turning them into game draws.
    """
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Helper: destroy a creature (move to graveyard, remove global effects)
# ─────────────────────────────────────────────────────────────────────────────

def _destroy_creature(
    state: GameState,
    player_idx: int,
    creature: Creature,
    reason: str,
) -> None:
    """
    Move a creature from the battle zone to the graveyard.
    Also removes any global effects that were sourced from that creature.

    Does NOT trigger "when destroyed" effects — those are queued by
    trigger_resolver.py after the destroy action is applied.
    """
    p = state.players[player_idx]

    if creature in p.battle_zone:
        p.battle_zone.remove(creature)

    # Remove any global effects this creature was providing
    state.global_effects.remove_by_source(creature.uid)

    # Move to graveyard (newest first)
    p.graveyard.insert(
        0,
        GraveyardCard(
            definition=creature.definition,
            uid=creature.uid,
            died_from=reason,
            died_on_turn=state.turn_number,
        )
    )
