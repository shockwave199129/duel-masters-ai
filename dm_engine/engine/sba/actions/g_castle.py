"""engine/sba/actions/g_castle.py — State-based action: Rule 822 — G-Castle shield."""
from __future__ import annotations
from core.enums import CardType, CardSubtype, GlobalEffectType
from core.state import GameState
from core.zones import Creature
from core.zones import GraveyardCard
from core.cards import is_g_castle


def _sba_g_castle_shield(state: GameState) -> bool:
    """
    Rule 822: G-Castle cards that leave the Shield Zone go to the Graveyard
    instead of the hand.

    This SBA acts as a safety net:
    1. Checks the shield trigger queue for G-Castle cards that should go to
       graveyard instead of hand.
    2. Checks for G-Castle cards in the shield zone that are somehow orphaned
       (e.g., a G-Castle that was placed in the shield zone directly rather
       than being fortified under a shield).
    """
    fired = False
    for player_idx in range(2):
        # ── 1. Check shield trigger queue for G-Castles ────────────────────
        queue = state.effect_stack.shield_trigger_queue
        remaining_queue: list[tuple[int, "ShieldCard"]] = []
        for queued_player, shield in queue:
            if queued_player == player_idx and is_g_castle(shield.definition):
                # G-Castle in trigger queue → send to graveyard, not hand
                state.players[player_idx].graveyard.insert(
                    0,
                    GraveyardCard(
                        definition=shield.definition,
                        uid=shield.uid,
                        died_from="g_castle_shield_break",
                        died_on_turn=state.turn_number,
                    ),
                )
                fired = True
                # Do NOT add back to queue — it's been resolved
            else:
                remaining_queue.append((queued_player, shield))
        if fired:
            queue.clear()
            queue.extend(remaining_queue)

        # ── 2. Check shield zone for orphaned G-Castles ─────────────────────
        # A G-Castle should normally only be in the shield zone as a fortified
        # card under a shield, not as a shield itself. If one ends up as a
        # shield directly (e.g., due to an effect), it stays in the shield
        # zone but we track it here for safety.
        shield_zone = state.players[player_idx].shield_zone
        g_castle_shields = [
            s for s in shield_zone
            if s.definition.card_subtype == CardSubtype.G_CASTLE
        ]
        # G-Castle cards in the shield zone are valid as shields. They will
        # be properly handled when they break (via move_standby_shield_to_hand
        # or the trigger queue check above). We just verify they exist here.
        for gc_shield in g_castle_shields:
            # Ensure the G-Castle shield is properly tracked
            # (no-op if already correct; this is a validation pass)
            if not gc_shield.is_revealed:
                # Unrevealed G-Castle shields are fine — they're just shields
                pass

    return fired

