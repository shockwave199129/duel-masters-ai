"""
tests/test_negative_power.py — Rule 108.1b: negative power treated as 0 when referenced.

Rule 108.1b: A creature's power can become negative due to minus modifications.
However, when referencing that creature's power, it is treated as 0.

This means:
  - compute_power() can return negative (so the SBA can still detect the creature
    for destruction — that's the "loses the battle" path)
  - effective_power() returns max(0, compute_power()) for battle comparisons and
    any other "referencing" context
  - Battle comparisons must use effective_power so that a defender with -1000 power
    is treated as 0 (and is destroyed by the SBA afterwards)
  - For example: a 5000 attacker vs a -1000 defender -> defender treated as 0,
    attacker wins; then the SBA destroys the defender (and possibly the attacker
    if its real power is also <= 0)
"""

import sys
sys.path.insert(0, "dm_engine")

from core.enums import (
    ActionType, CardSubtype, CardType, Civilization, INFINITY, Keyword, Phase
)
from core.cards import CardDefinition
from core.state import AttackContext, GameState, PlayerState, TurnInfo
from core.zones import Creature, PowerModifier
from engine.battle_resolver import resolve_battle
from engine.sba_checker import check_state_based_actions


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition


def make_card(card_id: int, name: str, power: int, keywords=()):
    return CardDefinition(
        id=card_id,
        slug=name,
        name=name,
        cost=1,
        power=power,
        card_type=CardType.CREATURE,
        card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]),
        races=frozenset(),
        keywords=frozenset(keywords),
        effects=[],
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
    )


def make_state() -> GameState:
    p0 = PlayerState(player_index=0, player_name="P0")
    p1 = PlayerState(player_index=1, player_name="P1")
    return GameState(
        players=[p0, p1],
        turn_info=TurnInfo(active_player=0, turn_number=2, phase=Phase.BATTLE),
    )


def make_creature(card_def: CardDefinition, controller: int = 0) -> Creature:
    c = Creature(definition=card_def, controller=controller, owner=controller)
    return c


print("\n" + "="*60)
print("  DM ENGINE - NEGATIVE POWER (Rule 108.1b) TESTS")
print("="*60)

# Section 1: effective_power property
print("\nSection 1: effective_power property")

# 1a: positive power - no change
c = make_creature(make_card(1, "Strong", 5000))
check("Positive power: effective == 5000", c.effective_power() == 5000,
      f"got {c.effective_power()}")

# 1b: zero power - no change
c = make_creature(make_card(2, "Zero", 0))
check("Zero power: effective == 0", c.effective_power() == 0,
      f"got {c.effective_power()}")

# 1c: negative base power (rare but possible) - effective is 0
c = make_creature(make_card(3, "AlreadyNeg", -500))
check("Negative base power: effective == 0", c.effective_power() == 0,
      f"got {c.effective_power()}")

# 1d: debuff pushes to negative - effective is 0
c = make_creature(make_card(4, "Debuffed", 2000))
c.power_modifiers.append(PowerModifier(amount=-3000, source_uid="test", duration="permanent"))
check("Debuffed to -1000: effective == 0", c.effective_power() == 0,
      f"got {c.effective_power()}")
check("Debuffed to -1000: compute_power() returns -1000", c.compute_power() == -1000,
      f"got {c.compute_power()}")

# 1e: -inf modifier - effective_power preserves -inf sentinel
c = make_creature(make_card(5, "InfinityHit", 5000))
c.power_modifiers.append(PowerModifier(amount=-INFINITY, source_uid="test", duration="permanent"))
check("-inf modifier: effective_power preserves -INFINITY", c.effective_power() == -INFINITY,
      f"got {c.effective_power()}")

# 1f: regular INFINITY power - effective is INFINITY
inf_card = make_card(6, "Inf", 9999)
object.__setattr__(inf_card, "is_infinite_power", True)
c = make_creature(inf_card)
check("INFINITY base: effective == INFINITY", c.effective_power() == INFINITY,
      f"got {c.effective_power()}")


# Section 2: Battle resolver uses effective_power
print("\nSection 2: Battle resolver uses effective_power")

# 2a: 5000 attacker vs -1000 defender (treated as 0) - attacker wins
attacker_card = make_card(10, "Atk", 5000)
defender_card = make_card(11, "Def", 2000)
attacker = make_creature(attacker_card, controller=0)
defender = make_creature(defender_card, controller=1)
defender.power_modifiers.append(PowerModifier(amount=-3000, source_uid="test", duration="permanent"))

s = make_state()
s.players[0].battle_zone = [attacker]
s.players[1].battle_zone = [defender]
s.attack_context = AttackContext(
    attacker_uid=attacker.uid,
    attacker_player=0,
    target_type="creature",
    target_uid=defender.uid,
)
after = resolve_battle(s)
check("5000 vs -1000: defender (effective 0) loses battle",
      defender.uid in [c.uid for c in after.players[1].graveyard])
check("5000 vs -1000: attacker survives",
      attacker.uid in [c.uid for c in after.players[0].battle_zone])

# 2b: equal effective power (both reduced to 0) - both lose
attacker_card = make_card(12, "Atk2", 1000)
defender_card = make_card(13, "Def2", 1000)
attacker = make_creature(attacker_card, controller=0)
defender = make_creature(defender_card, controller=1)
attacker.power_modifiers.append(PowerModifier(amount=-2000, source_uid="test", duration="permanent"))
defender.power_modifiers.append(PowerModifier(amount=-2000, source_uid="test", duration="permanent"))

s = make_state()
s.players[0].battle_zone = [attacker]
s.players[1].battle_zone = [defender]
s.attack_context = AttackContext(
    attacker_uid=attacker.uid,
    attacker_player=0,
    target_type="creature",
    target_uid=defender.uid,
)
after = resolve_battle(s)
check("Both reduced to 0: both lose (tie)",
      attacker.uid in [c.uid for c in after.players[0].graveyard] and
      defender.uid in [c.uid for c in after.players[1].graveyard])

# 2c: -inf defender is still destroyed
attacker_card = make_card(14, "Atk3", 5000)
defender_card = make_card(15, "Def3", 5000)
attacker = make_creature(attacker_card, controller=0)
defender = make_creature(defender_card, controller=1)
defender.power_modifiers.append(PowerModifier(amount=-INFINITY, source_uid="test", duration="permanent"))

s = make_state()
s.players[0].battle_zone = [attacker]
s.players[1].battle_zone = [defender]
s.attack_context = AttackContext(
    attacker_uid=attacker.uid,
    attacker_player=0,
    target_type="creature",
    target_uid=defender.uid,
)
after = resolve_battle(s)
check("-inf defender: still destroyed",
      defender.uid in [c.uid for c in after.players[1].graveyard])

# 2d: attacker with negative effective power loses to positive-power defender
attacker_card = make_card(16, "Weak", 1000)
defender_card = make_card(17, "Strong2", 5000)
attacker = make_creature(attacker_card, controller=0)
defender = make_creature(defender_card, controller=1)
attacker.power_modifiers.append(PowerModifier(amount=-2000, source_uid="test", duration="permanent"))

s = make_state()
s.players[0].battle_zone = [attacker]
s.players[1].battle_zone = [defender]
s.attack_context = AttackContext(
    attacker_uid=attacker.uid,
    attacker_player=0,
    target_type="creature",
    target_uid=defender.uid,
)
after = resolve_battle(s)
check("Attacker effective 0 vs defender 5000: attacker loses",
      attacker.uid in [c.uid for c in after.players[0].graveyard])


# Section 3: Negative-power creature is still destroyed by SBA
print("\nSection 3: Negative-power creature destroyed by SBA")

c = make_creature(make_card(20, "Doomed", 1000), controller=0)
c.power_modifiers.append(PowerModifier(amount=-1500, source_uid="test", duration="permanent"))
s = make_state()
s.players[0].battle_zone = [c]
after = check_state_based_actions(s)
check("Negative-power creature destroyed by SBA",
      c.uid in [x.uid for x in after.players[0].graveyard])


# Section 4: Rule example - Hell Scrapper scenario
print("\nSection 4: Hell Scrapper scenario (Rule 108.1b example)")

c1 = make_creature(make_card(30, "HellScraped", 5000), controller=0)
c1.power_modifiers.append(PowerModifier(amount=-6000, source_uid="test", duration="permanent"))

total_with_effective = c1.effective_power() + 11000
total_with_raw = c1.compute_power() + 11000
check("Hell Scrapper: reduced creature contributes 0 to total",
      total_with_effective == 11000,
      f"effective total = {total_with_effective}, raw total = {total_with_raw}")
