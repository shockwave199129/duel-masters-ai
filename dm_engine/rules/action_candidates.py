"""Rule-aware legal action candidate generation.

This adapter lets bots ask the rules layer for phase/keyword context before
building candidate actions. The concrete Action objects still come from the
deterministic engine so generated candidates remain executable.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.actions import Action
from core.state import GameState
from engine.action_generator import get_legal_actions
from rules.knowledge import RuleKnowledgeService


@dataclass(frozen=True)
class RuleAwareActionGenerator:
    """Generate executable candidates after consulting structured rule facts."""

    rule_service: RuleKnowledgeService | None = None

    def generate(self, state: GameState, db=None) -> list[Action]:
        """Return candidate actions for the current rules context."""
        if self.rule_service is not None:
            # Warm deterministic rule context for the current decision. These
            # facts are also reused by v3 encoders and explanations.
            self.rule_service.get_phase_info(state.current_phase)
            self.rule_service.get_phase_rules(state.current_phase)
            self.rule_service.get_keyword_rules(self._visible_keyword_names(state))
        return get_legal_actions(state, db)

    @staticmethod
    def _visible_keyword_names(state: GameState) -> set[str]:
        return {
            keyword.value
            for player in state.players
            for creature in player.battle_zone
            for keyword in getattr(creature.definition, "keywords", frozenset())
        }
