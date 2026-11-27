"""
bot/mcts_bot.py - actor-critic model with tree search.

The bot never reads cards it could not know: it searches several sampled worlds
(search/determinize.py). It does know the opponent's deck list.
"""

from __future__ import annotations

import torch

from bot.neural_bot import NeuralBot
from bot.neural_model import ActionCriticNet
from core.actions import Action
from core.state import GameState
from search.ensemble import ensemble_search
from search.mcts import MCTS, SearchConfig, SearchResult


class ModelEvaluator:
    """Move priors from the actor and the position value from the critic."""

    def __init__(self, bot: NeuralBot, prior_temperature: float = 1.0):
        if not isinstance(bot.model, ActionCriticNet):
            raise ValueError("Search needs an actor-critic model to score positions")
        if prior_temperature <= 0:
            raise ValueError("prior_temperature must be positive")
        self.bot = bot
        # The actor's scores are not probabilities, so this has to be tuned
        self.prior_temperature = prior_temperature

    def __call__(self, state: GameState, actions: list[Action], db=None):
        state_features, rows = self.bot.encode_rows(state, actions, db=db)
        inputs = torch.tensor(rows, dtype=torch.float32)
        split = len(state_features)
        with torch.inference_mode():
            inputs = inputs.to(self.bot.device)
            scores, value = self.bot.model.forward_policy_and_value(
                inputs[:1, :split], inputs[:, split:])
            # One transfer back from the GPU for both results
            output = torch.cat([scores, value]).cpu().double()
            priors = torch.softmax(output[:-1] / self.prior_temperature, dim=-1).tolist()
        return priors, float(output[-1])


class MCTSBot:
    """Plays the move visited most over trees searched in sampled worlds."""

    def __init__(
        self,
        *,
        model=None,
        model_path=None,
        trees: int = 8,
        config: SearchConfig | None = None,
        prior_temperature: float = 1.0,
        rule_service=None,
        device=None,
        seed: int = 0,
    ):
        self.neural = NeuralBot(
            model=model, model_path=model_path, rule_service=rule_service,
            device=device, seed=seed)
        self.evaluator = ModelEvaluator(self.neural, prior_temperature)
        self.trees = trees
        # simulations are per tree
        self.config = config or SearchConfig(simulations=64)
        self.seed = seed
        self.decisions = 0
        self.last_search: SearchResult | None = None

    def choose_action(self, state: GameState, db=None) -> Action:
        actions = self.neural.generate_candidate_actions(state, db=db)
        return self.choose_from_actions(state, actions, db=db)

    def choose_from_actions(self, state: GameState, actions: list[Action], db=None) -> Action:
        self.decisions += 1
        self.last_search = ensemble_search(
            MCTS(self.evaluator, self.config, db=db), state, actions,
            trees=self.trees, seed=f"search-{self.seed}-{self.decisions}")
        self.last_selected_index = self.last_search.best_index
        return actions[self.last_search.best_index]
