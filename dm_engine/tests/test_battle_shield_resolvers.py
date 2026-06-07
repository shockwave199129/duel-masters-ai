"""
tests/test_battle_shield_resolvers.py — battle and shield/direct attack flow.
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
    return GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", deck=[filler]),
            PlayerState(player_index=1, player_name="P1", deck=[filler]),
        ),
        turn_info=TurnInfo(turn_number=2, active_player=0, phase=phase),
    )


print("\n" + "═"*60)
print("  DM ENGINE — BATTLE / SHIELD TESTS")
print("═"*60)

attacker = creature(card(1, "attacker", 1000), controller=0)
defender = creature(card(2, "defender", 1000), controller=1)
s = state(Phase.BATTLE)
s.players[0].battle_zone = [attacker]
s.players[1].battle_zone = [defender]
s.attack_context = AttackContext(
    attacker_uid=attacker.uid,
    attacker_player=0,
    target_type="creature",
    target_uid=defender.uid,
)
after = resolve_battle(s)
check("Equal power destroys attacker", len(after.players[0].graveyard) == 1)
check("Equal power destroys defender", len(after.players[1].graveyard) == 1)

weak_winner = creature(card(6, "weak winner", 1000), controller=0)
strong_defender = creature(card(7, "strong defender", 9000), controller=1)
weak_winner.set_flag("wins_battles", True)
s = state(Phase.BATTLE)
s.players[0].battle_zone = [weak_winner]
s.players[1].battle_zone = [strong_defender]
s.attack_context = AttackContext(
    attacker_uid=weak_winner.uid,
    attacker_player=0,
    target_type="creature",
    target_uid=strong_defender.uid,
)
after = resolve_battle(s)
check("Wins-battles attacker survives lower power", len(after.players[0].battle_zone) == 1)
check("Wins-battles attacker destroys defender", len(after.players[1].graveyard) == 1)

both_a = creature(card(8, "wins a", 1000), controller=0)
both_d = creature(card(9, "wins d", 9000), controller=1)
both_a.set_flag("wins_battles", True)
both_d.set_flag("wins_battles", True)
s = state(Phase.BATTLE)
s.players[0].battle_zone = [both_a]
s.players[1].battle_zone = [both_d]
s.attack_context = AttackContext(
    attacker_uid=both_a.uid,
    attacker_player=0,
    target_type="creature",
    target_uid=both_d.uid,
)
after = resolve_battle(s)
check("Both wins-battles creatures survive", len(after.players[0].battle_zone) == 1 and len(after.players[1].battle_zone) == 1)

s = state(Phase.BLOCK_DECLARE)
s.players[0].battle_zone = [attacker]
s.players[1].battle_zone = [defender]
s.attack_context = AttackContext(
    attacker_uid=attacker.uid,
    attacker_player=0,
    target_type="creature",
    target_uid=defender.uid,
)
after = execute_action(s, pass_block(1), validate=False)
check("Unblocked creature attack proceeds to battle", after.current_phase == Phase.BATTLE)

dbl = creature(card(3, "double", 2000, [Keyword.DOUBLE_BREAKER]), controller=0)
s = state(Phase.DIRECT_ATTACK)
s.players[0].battle_zone = [dbl]
s.players[1].shield_zone = [ShieldCard(definition=card(4, "s1")), ShieldCard(definition=card(5, "s2"))]
s.attack_context = AttackContext(
    attacker_uid=dbl.uid,
    attacker_player=0,
    target_type="player",
    target_uid="player_1",
)
after = resolve_shield_break_choice(s, 0)
check("Double breaker queues two shields", len(after.effect_stack.shield_trigger_queue) == 2)
check("Breaking final shields does not win", after.result == GameResult.IN_PROGRESS)

s = state(Phase.DIRECT_ATTACK)
s.players[0].battle_zone = [dbl]
s.players[1].shield_zone = []
s.attack_context = AttackContext(
    attacker_uid=dbl.uid,
    attacker_player=0,
    target_type="player",
    target_uid="player_1",
)
after = execute_action(s, pass_action(0, "direct_attack"), validate=False)
check("Direct attack with no shields wins", after.result == GameResult.PLAYER_0_WINS)

g_strike_shield = ShieldCard(definition=card(10, "g strike", keywords=[Keyword.G_STRIKE]))
s = state(Phase.END_OF_ATTACK)
s.effect_stack.add_shield_trigger(1, g_strike_shield)
g_actions = get_legal_actions(s)
g_action = next(a for a in g_actions if a.action_type == ActionType.USE_G_STRIKE)
after = execute_action(s, g_action)
check("G-Strike consumes standby shield", len(after.effect_stack.shield_trigger_queue) == 0)
check("G-Strike shield moves to hand", len(after.players[1].hand) == 1)

s_back_card = card(11, "s back", keywords=[Keyword.S_BACK])
broken_shield = ShieldCard(definition=card(12, "discarded shield"))
s = state(Phase.END_OF_ATTACK)
s.players[1].hand = [HandCard(definition=s_back_card)]
s.effect_stack.add_shield_trigger(1, broken_shield)
s_back_actions = get_legal_actions(s)
s_back_action = next(a for a in s_back_actions if a.action_type == ActionType.USE_S_BACK)
after = execute_action(s, s_back_action)
check("S-Back consumes standby shield", len(after.effect_stack.shield_trigger_queue) == 0)
check("S-Back discards broken shield", after.players[1].graveyard[0].died_from == "s_back_discard")
check("S-Back summons card from hand", len(after.players[1].battle_zone) == 1)

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
