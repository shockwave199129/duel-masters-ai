"""
tests/test_sba_checker.py — state-based action behavior.
"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.cards import CardDefinition
from core.enums import CardSubtype, CardType, Civilization, GameResult, Phase
from core.player_state import PlayerState
from core.state import AttackContext, GameState, TurnInfo
from core.zones import Creature
from engine.sba_checker import check_state_based_actions

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))


def card(cid, name, power=1000, card_type=CardType.CREATURE, keywords=frozenset()):
    return CardDefinition(
        id=cid, slug=name, name=name, cost=1, power=power if card_type == CardType.CREATURE else None,
        card_type=card_type, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]), races=frozenset(),
        keywords=keywords, effects=tuple(),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


def bare_state(phase=Phase.MAIN):
    filler = card(99, "deck", 1000)
    return GameState(
        players=(
            PlayerState(player_index=0, player_name="P0", deck=[filler]),
            PlayerState(player_index=1, player_name="P1", deck=[filler]),
        ),
        turn_info=TurnInfo(turn_number=2, active_player=0, phase=phase),
    )


print("\n" + "═"*60)
print("  DM ENGINE — SBA TESTS")
print("═"*60)

s = bare_state()
s.players[0].battle_zone = [Creature(definition=card(1, "zero1", 0), controller=0)]
s.players[1].battle_zone = [Creature(definition=card(2, "zero2", 0), controller=1)]
after = check_state_based_actions(s)
check("Both zero-power creatures destroyed simultaneously",
      len(after.players[0].graveyard) == 1 and len(after.players[1].graveyard) == 1)

s = bare_state(Phase.DIRECT_ATTACK)
s.players[1].shield_zone = []
s.attack_context = AttackContext(
    attacker_uid="atk",
    attacker_player=0,
    target_type="player",
    target_uid="player_1",
    shields_broken=1,
)
after = check_state_based_actions(s)
check("Breaking last shield is not direct attack win", after.result == GameResult.IN_PROGRESS)

s.attack_context.received_direct_attack = True
after = check_state_based_actions(s)
check("Explicit direct attack event wins", after.result == GameResult.PLAYER_0_WINS)

# ── 703.4b: Player with empty deck loses ──────────────────────────────────
s = bare_state()
s.players[1].deck = []
after = check_state_based_actions(s)
check("703.4b: Empty deck → P0 wins",
      after.result == GameResult.PLAYER_0_WINS)

# ── 703.4d: Creature that lost battle destroyed ──────────────────────────
s = bare_state()
loser = Creature(definition=card(10, "loser", 1000), controller=0)
loser.temp_flags["lost_battle"] = True
s.players[0].battle_zone = [loser]
after = check_state_based_actions(s)
check("703.4d: Battle loser → destroyed",
      len(after.players[0].graveyard) == 1 and len(after.players[0].battle_zone) == 0)

# ── 703.4e: Cannot attack → tapped ────────────────────────────────────────
from core.enums import Keyword
s = bare_state()
creat = Creature(
    definition=card(20, "decrepit", keywords=frozenset({Keyword.CANNOT_ATTACK})),
    controller=0, is_tapped=False,
)
s.players[0].battle_zone = [creat]
after = check_state_based_actions(s)
check("703.4e: Cannot attack → tapped",
      after.players[0].battle_zone[0].is_tapped)

# ── 703.4f: Standalone Cross Gear → destroyed ─────────────────────────────
s = bare_state()
gear = Creature(definition=card(30, "lone_blade", card_type=CardType.CROSS_GEAR), controller=0)
s.players[0].battle_zone = [gear]
after = check_state_based_actions(s)
check("703.4f: Standalone Cross Gear → graveyard",
      len(after.players[0].battle_zone) == 0 and len(after.players[0].graveyard) == 1)

# ── 703.4g: Standalone Cell → graveyard ───────────────────────────────────
s = bare_state()
cell = Creature(definition=card(40, "lone_cell", card_type=CardType.CELL), controller=0)
s.players[0].battle_zone = [cell]
after = check_state_based_actions(s)
check("703.4g: Standalone Cell → not in battle zone",
      len(after.players[0].battle_zone) == 0)

# ── 703.4i: Invalid type (Spell standalone) → graveyard ───────────────────
s = bare_state()
spell = Creature(definition=card(50, "lone_spell", card_type=CardType.SPELL), controller=0)
s.players[0].battle_zone = [spell]
after = check_state_based_actions(s)
check("703.4i: Standalone Spell → graveyard",
      len(after.players[0].battle_zone) == 0 and len(after.players[0].graveyard) == 1)

# ── 703.4m: Standalone Weapon → hyperspatial (not graveyard) ──────────────
s = bare_state()
weapon = Creature(definition=card(60, "lone_weapon", card_type=CardType.WEAPON), controller=0)
s.players[0].battle_zone = [weapon]
after = check_state_based_actions(s)
check("703.4m: Standalone Weapon → not in battle zone",
      len(after.players[0].battle_zone) == 0)

# ── 703.4l: D2 Field uniqueness ───────────────────────────────────────────
s = bare_state()
d2_field = CardDefinition(
    id=70, slug="d2_field_1", name="D2 Field", cost=0, power=None,
    card_type=CardType.FIELD, card_subtype=CardSubtype.D2,
    civilizations=frozenset(), races=frozenset(), keywords=frozenset(),
    effects=tuple(), evolution_source_races=frozenset(),
    evolution_source_types=frozenset(), is_multiface=False,
)
f1 = Creature(definition=d2_field, controller=0)
f2 = Creature(definition=card(71, "d2_field_2", card_type=CardType.FIELD), controller=0)
f2.definition = CardDefinition(
    id=71, slug="d2_field_2", name="D2 Field 2", cost=0, power=None,
    card_type=CardType.FIELD, card_subtype=CardSubtype.D2,
    civilizations=frozenset(), races=frozenset(), keywords=frozenset(),
    effects=tuple(), evolution_source_races=frozenset(),
    evolution_source_types=frozenset(), is_multiface=False,
)
s.players[0].battle_zone = [f1, f2]
after = check_state_based_actions(s)
d2_count = len([c for c in after.players[0].battle_zone
                if c.definition.card_type == CardType.FIELD
                and c.definition.card_subtype == CardSubtype.D2])
check("703.4l: D2 Field uniqueness: only 1 remains",
      d2_count == 1, f"got {d2_count}")

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
