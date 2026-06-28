"""engine/sba/actions/deck_empty.py — State-based action: Rule 104.2b — deck empty."""
from __future__ import annotations
from core.state import GameState
from core.zones import Creature


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


