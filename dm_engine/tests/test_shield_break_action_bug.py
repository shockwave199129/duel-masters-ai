"""
test_shield_break_action_bug.py — verify shield break action parameter fix.

Tests that resolve_shield_break_choice:
  1. No longer crashes with NameError when called without an action parameter (backward compat)
  2. Correctly breaks 3 shields when action has break_mode "triple"
  3. Correctly breaks 2 shields when action has break_mode "double"
  4. Correctly breaks 1 shield (default) when action has no break_mode
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.cards import CardDefinition
from core.enums import ActionType, CardSubtype, CardType, Civilization, Keyword, Phase
from core.player_state import PlayerState
from core.state import AttackContext, GameState, TurnInfo
from core.zones import Creature, HandCard, ShieldCard, ManaCard
from engine.shield_resolver import resolve_shield_break_choice

PASS = "✅"
FAIL = "❌"
results = []

def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))

def card(cid, name, power=1000, keywords=()):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=1, power=power,
        card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]), races=frozenset(),
        keywords=frozenset(keywords), effects=tuple(),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )

def creature(defn, controller=0, sick=False):
    c = Creature(defn)
    c.controller = controller
    c.owner = controller
    c.has_summoning_sickness = sick
    return c


class MockAction:
    """Mock action with configurable break_mode in extra dict."""
    def __init__(self, break_mode=None):
        self._break_mode = break_mode

    def get_extra(self):
        return {"break_mode": self._break_mode} if self._break_mode else {}


def make_state(num_shields=5):
    """Create a game state with an attacking creature and the given number of shields."""
    filler = card(99, "deck")
    s = GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", deck=[filler]),
            PlayerState(player_index=1, player_name="P1", deck=[filler]),
        ),
        turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.BATTLE),
    )

    # Attacking creature in P0's battle zone
    attacker = creature(card(1, "Attacker", 3000), controller=0)
    s.players[0].battle_zone = [attacker]

    # Give P1 shields
    shield_card = card(100, "Shield")
    s.players[1].shield_zone = [ShieldCard(definition=shield_card) for _ in range(num_shields)]

    # Set up attack context: P0 attacking P1 (player attack)
    s.attack_context = AttackContext(
        attacker_uid=attacker.uid,
        attacker_player=0,
        target_type="player",
        target_uid=None,
    )

    return s


def main():
    print("\n" + "=" * 60)
    print("  SHIELD BREAK ACTION BUG — REGRESSION TESTS")
    print("=" * 60)

    # ── Test 1: Backward compat — no action parameter ──
    print("\n[Test 1] Backward compat: resolve_shield_break_choice(state, 0) without action")
    s1 = make_state(num_shields=5)
    try:
        after = resolve_shield_break_choice(s1, 0)
        remaining = len(after.players[1].shield_zone)
        # Attacker has no double/triple breaker keyword → breaks 1 shield by default
        check("No crash calling without action", True)
        check("Default breaks 1 shield", remaining == 4, f"expected 4 remaining, got {remaining}")
    except NameError as e:
        check("No crash calling without action", False, f"NameError: {e}")
    except Exception as e:
        check("No crash calling without action", False, f"{type(e).__name__}: {e}")

    # ── Test 2: Triple break ──
    print("\n[Test 2] Triple break: action with break_mode='triple'")
    s2 = make_state(num_shields=5)
    try:
        after = resolve_shield_break_choice(s2, 0, MockAction(break_mode="triple"))
        remaining = len(after.players[1].shield_zone)
        check("Triple break removes 3 shields", remaining == 2, f"expected 2 remaining, got {remaining}")
    except Exception as e:
        check("Triple break removes 3 shields", False, f"{type(e).__name__}: {e}")

    # ── Test 3: Double break ──
    print("\n[Test 3] Double break: action with break_mode='double'")
    s3 = make_state(num_shields=5)
    try:
        after = resolve_shield_break_choice(s3, 0, MockAction(break_mode="double"))
        remaining = len(after.players[1].shield_zone)
        check("Double break removes 2 shields", remaining == 3, f"expected 3 remaining, got {remaining}")
    except Exception as e:
        check("Double break removes 2 shields", False, f"{type(e).__name__}: {e}")

    # ── Test 4: Default (no break_mode in extra) ──
    print("\n[Test 4] Default break: action with no break_mode")
    s4 = make_state(num_shields=5)
    try:
        after = resolve_shield_break_choice(s4, 0, MockAction(break_mode=None))
        remaining = len(after.players[1].shield_zone)
        check("Default (no break_mode) removes 1 shield", remaining == 4, f"expected 4 remaining, got {remaining}")
    except Exception as e:
        check("Default (no break_mode) removes 1 shield", False, f"{type(e).__name__}: {e}")

    # ── Summary ──
    print("\n" + "-" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"  Results: {passed}/{total} passed")
    if passed < total:
        print("\n  Failures:")
        for name, ok, detail in results:
            if not ok:
                print(f"    {FAIL} {name}" + (f" — {detail}" if detail else ""))
    print("=" * 60)


if __name__ == "__main__":
    main()
