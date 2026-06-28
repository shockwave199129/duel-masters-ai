"""engine/sba/actions/dream_rare.py — State-based action: Rule 817 — dream rare uniqueness."""
from __future__ import annotations
from core.enums import CardType, CardSubtype, GlobalEffectType
from core.state import GameState
from core.zones import Creature
from core.zones import GraveyardCard


def _sba_dream_rare_uniqueness(state: GameState) -> bool:
    """
    Rule 817: Dream Rare creatures must be unique per player.
    Only one of each Dream Rare card_id per player is allowed.

    If duplicates are found, keep the one that entered most recently,
    send extras to the graveyard.
    """
    fired = False
    for player_idx in range(2):
        dream_rares = [
            c for c in state.players[player_idx].battle_zone
            if c.definition.card_subtype == CardSubtype.DREAM
        ]

        # Group by card_id
        by_id: dict[int, list] = {}
        for creature in dream_rares:
            cid = creature.definition.id
            by_id.setdefault(cid, []).append(creature)

        for cid, creatures in by_id.items():
            if len(creatures) <= 1:
                continue
            # Keep the one with the highest entered_turn (most recent)
            creatures.sort(key=lambda c: c.entered_turn, reverse=True)
            for creature in creatures[1:]:
                state.players[player_idx].battle_zone.remove(creature)
                state.global_effects.remove_by_source(creature.uid)
                state.players[player_idx].graveyard.insert(
                    0,
                    GraveyardCard(
                        definition=creature.definition,
                        uid=creature.uid,
                        died_from="sba_dream_rare_duplicate",
                        died_on_turn=state.turn_number,
                    ),
                )
                fired = True

    return fired


# ─────────────────────────────────────────────────────────────────────────────
# Duel Mate cleanup (rule 820)
# ─────────────────────────────────────────────────────────────────────────────

