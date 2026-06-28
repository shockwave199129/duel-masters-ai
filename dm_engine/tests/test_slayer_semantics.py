"""
test_slayer_semantics.py — Slayer revenge-on-loss mechanic tests.

Tests the Slayer keyword (OCG): when a Slayer creature loses a battle,
the opposing creature is also destroyed via Slayer's revenge-on-loss.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.actions import pass_action, pass_block
from core.cards import CardDefinition
from core.enums import ActionType, CardSubtype, CardType, Civilization, GameResult, Keyword, Phase
from core.player_state import PlayerState
from core.state import AttackContext, GameState, TurnInfo
from core.zones import Creature, HandCard, ShieldCard
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.battle_resolver import resolve_battle
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

def state(phase=Phase.BATTLE):
    filler = card(99, "deck")
    shield_def = card(98, "shield")
    return GameState(
        players=(
            PlayerState(
                player_index=0, player_name="P0", deck=[filler],
                shield_zone=[ShieldCard(definition=shield_def)],
            ),
            PlayerState(
                player_index=1, player_name="P1", deck=[filler],
                shield_zone=[ShieldCard(definition=shield_def)],
            ),
        ),
        turn_info=TurnInfo(turn_number=2, active_player=0, phase=phase),
    )

# ── Scenario 1: Slayer 1000 vs non-Slayer 5000 ─────────────────────────────
# Slayer loses → revenge triggers → BOTH destroyed
print("\n── Scenario 1: Slayer 1000 vs non-Slayer 5000 ──")
s = state()
atk = creature(card(1, "slayer_weak", 1000, [Keyword.SLAYER]), controller=0)
dfn = creature(card(2, "normal_bulky", 5000), controller=1)
s.players[0].battle_zone = [atk]
s.players[1].battle_zone = [dfn]
s.attack_context = AttackContext(
    attacker_uid=atk.uid, attacker_player=0,
    target_type="creature", target_uid=dfn.uid,
)
after = resolve_battle(s)
check("Slayer (1000) dies against stronger foe",
      len(after.players[0].graveyard) == 1,
      f"graveyard={len(after.players[0].graveyard)}")
check("Slayer revenge destroys winner (5000)",
      len(after.players[1].graveyard) == 1,
      f"graveyard={len(after.players[1].graveyard)}")
check("Both battle zones empty",
      len(after.players[0].battle_zone) == 0 and len(after.players[1].battle_zone) == 0,
      f"bz0={len(after.players[0].battle_zone)} bz1={len(after.players[1].battle_zone)}")

# ── Scenario 2: Slayer 5000 vs non-Slayer 1000 ─────────────────────────────
# Slayer wins → no revenge → only defender destroyed
print("\n── Scenario 2: Slayer 5000 vs non-Slayer 1000 ──")
s = state()
atk = creature(card(3, "slayer_strong", 5000, [Keyword.SLAYER]), controller=0)
dfn = creature(card(4, "normal_weak", 1000), controller=1)
s.players[0].battle_zone = [atk]
s.players[1].battle_zone = [dfn]
s.attack_context = AttackContext(
    attacker_uid=atk.uid, attacker_player=0,
    target_type="creature", target_uid=dfn.uid,
)
after = resolve_battle(s)
check("Slayer (5000) survives against weaker foe",
      len(after.players[0].battle_zone) == 1,
      f"battle_zone={len(after.players[0].battle_zone)}")
check("Slayer (5000) not in graveyard",
      len(after.players[0].graveyard) == 0,
      f"graveyard={len(after.players[0].graveyard)}")
check("Defender (1000) destroyed",
      len(after.players[1].graveyard) == 1,
      f"graveyard={len(after.players[1].graveyard)}")
check("No revenge triggered (Slayer won)",
      len(after.players[0].graveyard) == 0 and len(after.players[1].graveyard) == 1,
      f"p0_grave={len(after.players[0].graveyard)} p1_grave={len(after.players[1].graveyard)}")

# ── Scenario 3: Slayer 3000 vs Slayer 3000 (tie) ───────────────────────────
# Equal power → both lose → each Slayer's revenge triggers → BOTH destroyed
print("\n── Scenario 3: Slayer 3000 vs Slayer 3000 (tie) ──")
s = state()
atk = creature(card(5, "slayer_a", 3000, [Keyword.SLAYER]), controller=0)
dfn = creature(card(6, "slayer_b", 3000, [Keyword.SLAYER]), controller=1)
s.players[0].battle_zone = [atk]
s.players[1].battle_zone = [dfn]
s.attack_context = AttackContext(
    attacker_uid=atk.uid, attacker_player=0,
    target_type="creature", target_uid=dfn.uid,
)
after = resolve_battle(s)
check("Both Slayers die from tie",
      len(after.players[0].graveyard) == 1 and len(after.players[1].graveyard) == 1,
      f"p0_grave={len(after.players[0].graveyard)} p1_grave={len(after.players[1].graveyard)}")
check("Both battle zones empty after mutual destruction",
      len(after.players[0].battle_zone) == 0 and len(after.players[1].battle_zone) == 0,
      f"bz0={len(after.players[0].battle_zone)} bz1={len(after.players[1].battle_zone)}")

# ── Scenario 4: Slayer 1000 vs Slayer 5000 ─────────────────────────────────
# Weaker Slayer loses → revenge triggers → stronger also dies → BOTH destroyed
print("\n── Scenario 4: Slayer 1000 vs Slayer 5000 ──")
s = state()
atk = creature(card(7, "slayer_weak", 1000, [Keyword.SLAYER]), controller=0)
dfn = creature(card(8, "slayer_strong", 5000, [Keyword.SLAYER]), controller=1)
s.players[0].battle_zone = [atk]
s.players[1].battle_zone = [dfn]
s.attack_context = AttackContext(
    attacker_uid=atk.uid, attacker_player=0,
    target_type="creature", target_uid=dfn.uid,
)
after = resolve_battle(s)
check("Weaker Slayer (1000) dies",
      len(after.players[0].graveyard) == 1,
      f"graveyard={len(after.players[0].graveyard)}")
check("Stronger Slayer (5000) also dies from revenge",
      len(after.players[1].graveyard) == 1,
      f"graveyard={len(after.players[1].graveyard)}")
check("Both battle zones empty",
      len(after.players[0].battle_zone) == 0 and len(after.players[1].battle_zone) == 0,
      f"bz0={len(after.players[0].battle_zone)} bz1={len(after.players[1].battle_zone)}")

# ── Summary ─────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"  RESULTS: {passed}/{total} passed")
if passed < total:
    for name, ok, detail in results:
        if not ok:
            print(f"    FAIL: {name} — {detail}")
print(f"{'='*60}")
