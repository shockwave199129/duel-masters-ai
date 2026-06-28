"""engine/sba/actions/seal_removal.py — State-based action: Rule 116.4 — seal removal."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature
from core.zones import GraveyardCard


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


