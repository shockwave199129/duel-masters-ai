"""engine/sba/actions/duel_mate_cleanup.py — State-based action: Rule 820 — duel mate cleanup."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature
from engine.zone_mover import creature_to_hyperspatial_card


def _sba_duel_mate_cleanup(state: GameState) -> bool:
    """
    Rule 820: Duel Mates that are in the Battle Zone but not properly
    summoned should be moved to the Hyperspatial Zone.
    Established Duel Mates (those that survived a full turn cycle) stay.

    A Duel Mate is properly summoned if it came via the Duel Mate summon
    action path (temp_flags["properly_summoned_as_duel_mate"]).
    Otherwise, evict freshly-arrived Duel Mates that still have summoning sickness.
    """
    from core.cards import is_duel_mate

    fired = False
    for player_idx in range(2):
        duel_mates = [
            c for c in state.players[player_idx].battle_zone
            if is_duel_mate(c.definition)
        ]

        for creature in duel_mates:
            # Evict Duel Mates that were not properly summoned and no longer
            # have summoning sickness (illegal battle zone entry).
            properly_summoned = creature.temp_flags.get("properly_summoned_as_duel_mate", False)
            if not properly_summoned and not creature.has_summoning_sickness:
                creature.remove_static_effects(state)
                state.players[player_idx].battle_zone.remove(creature)
                state.global_effects.remove_by_source(creature.uid)
                state.players[creature.owner].hyperspatial_zone.append(
                    creature_to_hyperspatial_card(creature)
                )
                fired = True

    return fired


# ─────────────────────────────────────────────────────────────────────────────
# G-Castle shield zone (rule 822)
# ─────────────────────────────────────────────────────────────────────────────

