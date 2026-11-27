"""
tests/test_match_runner.py - paired match games, summaries and one-call scoring.
"""

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import torch

from bot.neural_model import ActionCriticNet
from core.cards import CardDefinition, DeckDefinition
from core.enums import CardSubtype, CardType, Civilization
from training.match_runner import (
    BotFactory, MatchConfig, contender_seat, new_match_state, parse_bot,
    play_match_game, run_match, summarize_games,
)

results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" - {detail}" if detail else ""))


def card(cid, civ):
    return CardDefinition(
        id=cid, slug=f"c{cid}", name=f"c{cid}", cost=1 + cid % 3, power=1000 * (1 + cid % 4),
        card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([civ]), races=frozenset(), keywords=frozenset(),
        effects=tuple(), evolution_source_races=frozenset(),
        evolution_source_types=frozenset(), is_multiface=False,
    )


@dataclass
class Spec:
    main_deck: DeckDefinition
    hyperspatial: tuple = ()
    ultra_gr: tuple = ()
    start_battle_zone: tuple = ()


class Pool:
    def __init__(self):
        self.ids, self.decks = [], {}
        for number, civ in enumerate((Civilization.FIRE, Civilization.WATER,
                                      Civilization.NATURE)):
            cards = [card(100 * number + i, civ) for i in range(10)]
            deck = DeckDefinition(
                name=f"deck{number}", owner="test",
                card_counts={c.id: 4 for c in cards},
                card_definitions={c.id: c for c in cards})
            self.ids.append(number)
            self.decks[number] = Spec(deck)


def deal(state):
    return [([c.id for c in p.hand], [s.id for s in p.shield_zone], [d.id for d in p.deck])
            for p in state.players]


print("DM ENGINE - MATCH RUNNER TESTS")
pool = Pool()

check("The contender swaps seats within a pair",
      [contender_seat(g) for g in range(4)] == [0, 1, 0, 1])
check("Both games of a pair get the same deal",
      deal(new_match_state(pool, 6, 10)) == deal(new_match_state(pool, 7, 10))
      and new_match_state(pool, 6, 10).active_player
      == new_match_state(pool, 7, 10).active_player)
check("Different pairs get different deals",
      deal(new_match_state(pool, 6, 10)) != deal(new_match_state(pool, 8, 10)))
check("The first seat alternates between pairs",
      {new_match_state(pool, g, 10).active_player for g in (0, 2)} == {0, 1})

check("Bot descriptions are read",
      parse_bot("mcts:a:b.pt") == ("mcts", "a:b.pt") and parse_bot("random") == ("random", None))
bad = []
for spec in ("mcts", "heuristic:x.pt", "wizard"):
    try:
        parse_bot(spec)
    except ValueError:
        bad.append(spec)
check("Wrong bot descriptions are refused", len(bad) == 3, str(bad))
try:
    run_match("unused", MatchConfig("neural:no-such-model.pt", "random"))
    refused = False
except FileNotFoundError:
    refused = True
check("A missing model file stops the match before it starts", refused)

config = MatchConfig("heuristic", "random", games=2, seed=3)
factory = BotFactory(config)
games = [play_match_game(pool, factory, g) for g in range(2)]
check("Games are played to a result",
      all(g["outcome"] in ("win", "loss", "unfinished") and not g["error"] and g["steps"] > 10
          for g in games), str([(g["outcome"], g["steps"], g["error"]) for g in games]))
check("Only the contender's real choices are timed",
      all(0 < g["decisions"] < g["steps"] for g in games))


class BrokenPool(Pool):
    def __init__(self):
        super().__init__()
        self.decks = {}


crashed = play_match_game(BrokenPool(), factory, 0)
check("A crash is reported instead of stopping the match",
      crashed["outcome"] == "error" and "KeyError" in crashed["error"])

rows = [dict(games[0], outcome=o, seconds=2.0, decisions=4, decision_seconds=1.0)
        for o in ("win", "win", "win", "loss", "unfinished", "error")]
summary = summarize_games(rows, 6.0)
check("The summary counts every kind of result",
      (summary["wins"], summary["losses"], summary["unfinished"], summary["errors"])
      == (3, 1, 1, 1))
check("The win rate is of decided games", summary["win_rate"] == 0.75)
check("Times are averaged",
      summary["seconds_per_game"] == 2.0 and summary["seconds_per_decision"] == 0.25)

# Search scores moves and position in one call; it must match the separate calls
torch.manual_seed(0)
model = ActionCriticNet(hidden_size=32, num_blocks=2).eval()
state_row = torch.rand(1, model.obs_size)
actions = torch.rand(5, model.act_size)
with torch.no_grad():
    scores, value = model.forward_policy_and_value(state_row, actions)
    separate = model(torch.cat([state_row.expand(5, -1), actions], dim=-1)).view(-1)
    separate_value = model.forward_value(state_row).view(-1)
check("One call gives the same move scores", torch.allclose(scores, separate, atol=1e-5))
check("One call gives the same position value",
      torch.allclose(value, separate_value, atol=1e-6))

passed = sum(results)
print(f"RESULTS: {passed}/{len(results)} passed, {len(results) - passed} failed")
sys.exit(0 if passed == len(results) else 1)
