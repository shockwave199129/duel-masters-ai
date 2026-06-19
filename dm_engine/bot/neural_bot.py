"""
bot/neural_bot.py — generation-0 neural legal-action player.
"""

from __future__ import annotations

import random
from pathlib import Path

import torch

from bot.action_encoder import encode_action_v2, encode_action_v3
from bot.neural_model import ActionScoreNet, MODEL_INPUT_SIZE_V2, MODEL_INPUT_SIZE_V3, load_model
from bot.state_encoder import encode_observation_v2, encode_observation_v3
from core.actions import Action
from core.state import GameState
from rules import RuleAwareActionGenerator


class NeuralBot:
    """Scores legal actions with a randomly initialized or loaded model."""

    def __init__(
        self,
        *,
        model: ActionScoreNet | None = None,
        model_path: str | Path | None = None,
        epsilon: float = 0.05,
        seed: int | None = None,
        encoder_version: int | None = None,
        rule_service=None,
    ):
        if model is not None and model_path is not None:
            raise ValueError("Pass either model or model_path, not both")
        if model is not None:
            self.model = model
        elif model_path is not None:
            self.model = load_model(model_path)
        elif encoder_version == 2:
            self.model = ActionScoreNet(input_size=MODEL_INPUT_SIZE_V2)
        else:
            self.model = ActionScoreNet()
        self.model.eval()
        self.epsilon = epsilon
        self.rng = random.Random(seed)
        self.rule_service = rule_service
        self.action_generator = RuleAwareActionGenerator(rule_service)
        inferred_encoder_version = self._infer_encoder_version()
        if encoder_version is not None and encoder_version != inferred_encoder_version:
            raise ValueError(
                f"encoder_version={encoder_version} does not match model input "
                f"for encoder v{inferred_encoder_version}"
            )
        self.encoder_version = inferred_encoder_version
        if self.encoder_version not in (2, 3):
            raise ValueError("encoder_version must be 2 or 3")

    def choose_action(self, state: GameState, db=None) -> Action:
        actions = self.generate_candidate_actions(state, db=db)
        return self.choose_from_actions(state, actions, db=db)

    def generate_candidate_actions(self, state: GameState, db=None) -> list[Action]:
        """Generate executable candidate actions using current rule context."""
        return self.action_generator.generate(state, db=db)

    def choose_from_actions(self, state: GameState, actions: list[Action], db=None) -> Action:
        if not actions:
            raise ValueError("No legal actions available")
        if self.epsilon > 0.0 and self.rng.random() < self.epsilon:
            return self.rng.choice(actions)

        perspective = actions[0].player
        if self.encoder_version == 2:
            state_features = encode_observation_v2(state, perspective)
            rows = [
                state_features + encode_action_v2(action, state=state, db=db)
                for action in actions
            ]
        else:
            state_features = encode_observation_v3(
                state,
                perspective,
                rule_service=self.rule_service,
            )
            rows = [
                state_features + encode_action_v3(
                    action,
                    state=state,
                    db=db,
                    rule_service=self.rule_service,
                )
                for action in actions
            ]
        with torch.no_grad():
            inputs = torch.tensor(rows, dtype=torch.float32)
            scores = self.model(inputs).squeeze(-1)
            best_index = int(torch.argmax(scores).item())
        return actions[best_index]

    def score_actions(self, state: GameState, actions: list[Action], db=None) -> list[float]:
        """Return model scores for diagnostics without changing action legality."""
        if not actions:
            return []
        perspective = actions[0].player
        if self.encoder_version == 2:
            state_features = encode_observation_v2(state, perspective)
            rows = [state_features + encode_action_v2(action, state=state, db=db) for action in actions]
        else:
            state_features = encode_observation_v3(state, perspective, rule_service=self.rule_service)
            rows = [
                state_features + encode_action_v3(action, state=state, db=db, rule_service=self.rule_service)
                for action in actions
            ]
        with torch.no_grad():
            inputs = torch.tensor(rows, dtype=torch.float32)
            return [float(score) for score in self.model(inputs).view(-1).tolist()]

    def explain_action_score(self, state: GameState, action: Action, score: float | None = None) -> str:
        """Build a lightweight rule-citation explanation for reports/debugging."""
        if self.rule_service is None:
            return "Rule explanations are unavailable because no rule service is configured."
        query = f"{state.current_phase.name} {action.action_type.value} {action}"
        context = self.rule_service.build_context_for_event(query, state.current_phase, n=5)
        score_text = "" if score is None else f"score={score:.3f}\n"
        return f"{score_text}{context}".strip()

    def _infer_encoder_version(self) -> int:
        input_size = int(getattr(self.model, "input_size", 0) or 0)
        if not input_size and hasattr(self.model, "net"):
            layers = list(getattr(self.model, "net"))
            input_size = int(getattr(layers[0], "in_features", 0) or 0) if layers else 0
        if input_size == MODEL_INPUT_SIZE_V2:
            return 2
        if input_size == MODEL_INPUT_SIZE_V3:
            return 3
        return 3
