"""
tests/test_king_cell_rules.py — King Cell combine rules (rule 814).

Usage:
    python dm_engine/tests/test_king_cell_rules.py
"""

import sys

sys.path.insert(0, "dm_engine")

from core.cards import CardDefinition
from core.enums import CardType, Civilization, Phase
from core.zones import Creature, HandCard, ManaCard, _new_uid
from core.state import GameState, TurnInfo
from core.player_state import PlayerState
from core.actions import combine_king_creature, ManaUsage
from core.enums import ActionType
from engine.zone_mover import combine_king_cells, tap_mana_for_payment
from engine.sba_checker import check_state_based_actions
from engine.action_generator import _generate_main_actions, _actions_for_hand_card

failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global failed
    if condition:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}")
        if detail:
            print(f"     {detail}")
        failed += 1


def make_king_cell(id: int, slug: str, name: str, civ: Civilization, target: str) -> CardDefinition:
    return CardDefinition(
        id=id,
        slug=slug,
        name=name,
        cost=0,
        power=None,
        card_type=CardType.CELL,
        card_subtype=__import__("core.enums", fromlist=["CardSubtype"]).CardSubtype.NONE,
        civilizations=frozenset([civ]),
        races=frozenset(),
        keywords=frozenset(),
        effects=(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
        king_combine_target_slug=target,
        king_combine_required_slugs=frozenset(),
    )


def make_volzeos(id: int = 100) -> CardDefinition:
    return CardDefinition(
        id=id,
        slug="Volzeos_Balamord",
        name="Volzeos Balamord",
        cost=3,
        power=12000,
        card_type=CardType.CREATURE,
        card_subtype=__import__("core.enums", fromlist=["CardSubtype"]).CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]),
        races=frozenset(["King"]),
        keywords=frozenset(),
        effects=(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
        king_combine_target_slug=None,
        king_combine_required_slugs=frozenset(
            [
                "New_World_King's_Fighting_Spirit",
                "New_World_King's_Authority",
                "New_World_King's_Thoughts",
            ]
        ),
    )


print("\n── King Cell rules (814) ───────────────────────────────────")

fighting = make_king_cell(
    1, "New_World_King's_Fighting_Spirit", "Fighting Spirit", Civilization.LIGHT, "Volzeos_Balamord"
)
authority = make_king_cell(
    2, "New_World_King's_Authority", "Authority", Civilization.DARKNESS, "Volzeos_Balamord"
)
thoughts = make_king_cell(
    3, "New_World_King's_Thoughts", "Thoughts", Civilization.NATURE, "Volzeos_Balamord"
)
volzeos = make_volzeos()

check("King cell detected", fighting.is_king_cell())
check("King creature detected", volzeos.is_king_creature())
check("King cell effective cost is 0", fighting.effective_cost() == 0)

# Cannot summon king cell from hand
p0 = PlayerState(
    player_index=0,
    player_name="P0",
    hand=[HandCard(definition=fighting, uid="h1")],
    mana_zone=[],
    deck_composition={},
)
p1 = PlayerState(player_index=1, player_name="P1", deck_composition={})
state = GameState(
    players=(p0, p1),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)
hand_actions = _actions_for_hand_card(0, "h1", fighting, state)
check("King cell cannot be summoned alone", not hand_actions)

# Cannot normal-summon king creature from hand
state.players[0].hand = [HandCard(definition=volzeos, uid="h2")]
volzeos_actions = _actions_for_hand_card(0, "h2", volzeos, state)
check("King creature cannot be normal summoned", not volzeos_actions)

# Combine from hand + mana
fire_mana = __import__("core.cards", fromlist=["CardDefinition"]).CardDefinition(
    id=50,
    slug="Fire_Mana",
    name="Bolshack Dragon",
    cost=2,
    power=2000,
    card_type=CardType.CREATURE,
    card_subtype=__import__("core.enums", fromlist=["CardSubtype"]).CardSubtype.NONE,
    civilizations=frozenset([Civilization.FIRE]),
    races=frozenset(),
    keywords=frozenset(),
    effects=(),
    evolution_source_races=frozenset(),
    evolution_source_types=frozenset(),
    is_multiface=False,
)
mana_light = ManaCard(definition=fighting, is_tapped=False)
mana_dark = ManaCard(definition=authority, is_tapped=False)
state.players[0].hand = [HandCard(definition=thoughts, uid="h3")]
state.players[0].mana_zone = [
    mana_light,
    mana_dark,
    ManaCard(definition=fire_mana, is_tapped=False),
    ManaCard(definition=fire_mana, is_tapped=False),
    ManaCard(definition=fire_mana, is_tapped=False),
]

class _MiniDB:
    def all_cards(self):
        return [fighting, authority, thoughts, volzeos]


main_actions = _generate_main_actions(state, _MiniDB())
combine_types = [a for a in main_actions if a.action_type == ActionType.COMBINE_KING_CREATURE]
check("Combine action generated with 3 cells", len(combine_types) >= 1)

# Execute combine
if combine_types:
    action = combine_types[0]
    tap_mana_for_payment(state, 0, action.mana_used)
    creature = combine_king_cells(state, 0, volzeos, list(action.selected_uids))
    check("Combined creature in battle zone", creature in state.players[0].battle_zone)
    check("Combined has 3 linked king cells", len(creature.linked_cells) == 3)
    check("All linked cells marked king", all(c.is_king_cell for c in creature.linked_cells))
    check("Hand cell consumed", len(state.players[0].hand) == 0)
    check(
        "King mana cells consumed",
        not any(m.definition.is_king_cell() for m in state.players[0].mana_zone),
    )

# Standalone king cell in BZ → graveyard (not hyperspatial)
solo_p0 = PlayerState(
    player_index=0,
    player_name="P0",
    battle_zone=[
        Creature(definition=fighting, uid=_new_uid(), controller=0, owner=0),
    ],
    deck_composition={},
)
solo_p1 = PlayerState(player_index=1, player_name="P1", deck_composition={})
solo_state = GameState(
    players=(solo_p0, solo_p1),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)
after_sba = check_state_based_actions(solo_state)
check("Standalone king cell removed from BZ", len(after_sba.players[0].battle_zone) == 0)
check("Standalone king cell in graveyard", len(after_sba.players[0].graveyard) == 1)
check(
    "King cell not sent to hyperspatial",
    len(after_sba.players[0].hyperspatial_zone) == 0,
)

print(f"\n{'All passed' if failed == 0 else f'{failed} FAILED'}")
