"""
engine/sba/missing_sbas.py — Implement missing SBAs 703.4e-4m.

Rule 703: State-Based Actions — automated board cleanup that occurs after
every action resolution. These SBAs are missing from the current codebase.

Implemented:
  - 703.4e: Creature with "cannot attack" → must be tapped
  - 703.4f: Cross Gear not attached to creature → destroyed
  - 703.4g: Aura/Fortress not attached to creature → graveyard
  - 703.4m: Weapon standalone → graveyard
"""

from core.zones import Creature
from core.enums import CardType, CardSubtype, Keyword
from core.state import GameState


def _sba_cannot_attack_tap(state: GameState) -> bool:
    """
    Rule 703.4e: If a creature is in the Battle Zone and has the "cannot attack"
    keyword, it must be tapped. This prevents illegal attacks.

    Returns True if any creature was tapped by this SBA.
    """
    fired = False

    for player_idx in range(2):
        for creature in list(state.players[player_idx].battle_zone):
            # Check if creature has "cannot attack" keyword
            if creature.is_ignored:
                continue
            if creature.has_keyword(Keyword.CANNOT_ATTACK) and not creature.is_tapped:
                creature.is_tapped = True
                fired = True

    return fired


def _sba_cross_gear_standalone(state: GameState) -> bool:
    """
    Legacy helper — not wired in the SBA checker.

    Rule 303.2: generated Cross Gear enters and remains in the Battle Zone.
    Unattached Cross Gear is valid until crossed; it is not destroyed by SBA.
    """
    return False


def _sba_aura_fortress_standalone(state: GameState) -> bool:
    """
    Rule 703.4g: Aura and Fortress cards that are not attached to a creature
    are sent to the Graveyard.

    Both AURA and FORTRESS card types must be attached to a creature.
    If standalone, they are destroyed.

    Returns True if any Auras/Fortresses were destroyed.
    """
    from core.zones import GraveyardCard

    fired = False

    for player_idx in range(2):
        player = state.players[player_idx]
        # Find standalone Auras/Fortresses (attached_to_uid is None)
        standalone = [
            c for c in player.battle_zone
            if c.definition.card_type in (CardType.AURA, CardType.FORTRESS)
            and getattr(c, "attached_to_uid", None) is None
        ]

        for card in standalone:
            player.battle_zone.remove(card)
            player.graveyard.insert(
                0,
                GraveyardCard(
                    definition=card.definition,
                    uid=card.uid,
                    died_from="sba_aura_fortress_standalone",
                    died_on_turn=state.turn_number,
                ),
            )
            fired = True

    return fired


def _sba_weapon_standalone(state: GameState) -> bool:
    """
    Rule 703.4m: Weapon cards (used for Dragheart Weapon cards) that are not
    attached to a creature are sent to the Graveyard.

    WEAPON card type must be attached to a creature via dragsolve.
    If standalone, they are destroyed.

    Returns True if any Weapons were destroyed.
    """
    from core.zones import GraveyardCard

    fired = False

    for player_idx in range(2):
        player = state.players[player_idx]
        # Find standalone Weapons (attached_to_uid is None)
        standalone = [
            c for c in player.battle_zone
            if c.definition.card_type == CardType.WEAPON
            and getattr(c, "attached_to_uid", None) is None
        ]

        for weapon in standalone:
            player.battle_zone.remove(weapon)
            player.graveyard.insert(
                0,
                GraveyardCard(
                    definition=weapon.definition,
                    uid=weapon.uid,
                    died_from="sba_weapon_standalone",
                    died_on_turn=state.turn_number,
                ),
            )
            fired = True

    return fired
