"""
tests/test_rule_knowledge.py - hybrid rule knowledge service tests.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.enums import Phase
from core.actions import pass_action
from core.player_state import PlayerState
from core.state import GameState, TurnInfo
from rules import RuleAwareActionGenerator, RuleKnowledgeService, phase_key_for_engine_phase

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" - {detail}" if detail else ""))


class FakeCursor:
    def __init__(self):
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        if "FROM dm_rules" in query and "rule_number = ANY" in query:
            self.rows = [
                ("112.2a", "Pay civilizations separately.", "cost_payment", ["any"], ["mana_zone"], False, False, False, 100),
                ("506.1", "Choose an attacker.", "turn_structure", ["attack"], ["battle_zone"], False, True, False, 20),
            ]
        elif "FROM dm_game_phases" in query:
            self.rows = [("attack", "Attack Step", 5, True, True, "505", "Attack with creatures")]
        elif "FROM dm_keywords" in query:
            self.rows = [("Speed Attacker", "Can attack immediately", "301.5", True, False, False, True, False, False, ["attack"])]
        elif "FROM dm_state_based_actions" in query:
            self.rows = [
                ("703.4a", "win_loss", "Check win/loss", {"type": "win_loss"}, {"effect": "end_game"}, 5),
                ("703.4c", "zero_power", "Destroy 0 power creatures", {"power_lte": 0}, {"move": "graveyard"}, 10),
            ]
        else:
            self.rows = []

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor()


class FakeSemanticRetriever:
    def search(self, query, n=5, phase=None, zone=None, category=None):
        return [
            {
                "rule_number": "112.3c",
                "text": "Ninja Strike can be used during attack timing.",
                "score": 0.9,
                "metadata": {"phase": phase},
            }
        ][:n]


def fake_connect(_dsn):
    return FakeConnection()


print("\n" + "=" * 60)
print("  DM ENGINE - RULE KNOWLEDGE TESTS")
print("=" * 60)

service = RuleKnowledgeService(
    "postgresql://example",
    connect_factory=fake_connect,
    semantic_retriever=FakeSemanticRetriever(),
)

check("Phase key maps engine attack phase", phase_key_for_engine_phase(Phase.ATTACK) == "attack")

rules = service.get_rules(["112.2a", "506.1"])
check("Exact rule lookup returns requested rules", set(rules) == {"112.2a", "506.1"})
check("Rule category is preserved", rules["112.2a"].rule_category == "cost_payment")

phase = service.get_phase_info(Phase.ATTACK)
check("Phase metadata loads", phase is not None and phase.can_repeat and phase.is_optional)

keyword = service.get_keyword_rule("speed_attacker")
check("Keyword metadata loads", keyword is not None and keyword.overrides_summoning_sickness)

sbas = service.get_state_based_actions()
check("State-based actions are priority ordered", [s.priority for s in sbas] == [5, 10])

semantic = service.search_semantic_rules("when can Ninja Strike be used", phase="attack")
check("Semantic retrieval is optional diagnostic context", semantic and semantic[0].rule_number == "112.3c")

empty_service = RuleKnowledgeService()
check("Unconfigured service fails closed for semantic search", empty_service.search_semantic_rules("x") == [])
check("Unconfigured service returns no SBAs", empty_service.get_state_based_actions() == [])

state = GameState(
    players=(
        PlayerState(player_index=0, player_name="P0"),
        PlayerState(player_index=1, player_name="P1"),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.DRAW),
)
generator = RuleAwareActionGenerator(service)
candidates = generator.generate(state)
check("Rule-aware generator returns executable candidates", candidates == [pass_action(0, "draw")])
check("Rule-aware generator warmed phase rule cache", service.get_phase_info(Phase.DRAW) is not None)

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
