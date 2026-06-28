"""engine/sba/actions/invalid_type.py — State-based action: Rule 302.3/309.7/313.3 — invalid type."""
from __future__ import annotations
from core.enums import CardType, CardSubtype, GlobalEffectType
from core.state import GameState
from core.zones import Creature
from core.zones import GraveyardCard


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


