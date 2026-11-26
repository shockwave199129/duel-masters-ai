"""
tests/test_card_text.py - translating card text into ability data, and refusing
text that is not fully understood.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.card_text import Face, translate
from core.cards import CardDefinition
from core.enums import (
    CardSubtype, CardType, Civilization, Keyword, Phase, TriggerEvent,
)
from core.player_state import PlayerState
from core.state import GameState, PendingTrigger, TurnInfo
from core.zones import GraveyardCard
from core.card_scripts import scripted_effects
from engine.trigger_resolver import resolve_pending_triggers

FAILURES: list[str] = []
RACES = frozenset({"Armored Dragon", "Cyber Virus", "Cyber Lord", "Origin", "Dragonoid",
                   "Abyss Dragon", "Oracle", "Demon Command"})


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


def one(text: str, card_type: str = "Creature"):
    return translate("Sample", [Face(text, card_type)], RACES)


# ── Parts combine: when, what, and what it affects ──────────────────────────
result = one("■ [[When you put]] this [[creature]], choose one of your opponent's creatures "
             "that costs 4 or less and destroy it.")
check("when put + destroy + cost limit", result.complete and len(result.abilities) == 1
      and result.abilities[0].event == TriggerEvent.ON_ENTER_BATTLE_ZONE
      and result.abilities[0].steps[0][1]["max_cost"] == 4, str(result.unknown))

result = one("■ Whenever this creature attacks, you may choose one of your opponent's creatures "
             "that has power 3000 or less and destroy it.")
check("the same action under another trigger, now optional",
      result.complete and result.abilities[0].event == TriggerEvent.ON_ATTACK
      and result.abilities[0].steps[0][1]["optional"]
      and result.abilities[0].steps[0][1]["max_power"] == 3000)

result = one("■ When you put this creature or it attacks, draw 2 cards. Then your opponent "
             "discards a card at random from their hand.")
check("two triggers and two actions in order",
      result.complete and len(result.abilities) == 2
      and [s[0] for s in result.abilities[0].steps] == ["draw", "random_discard"])

result = one("■ Draw a card. ■ Put the top 2 cards of your deck into your mana zone.", "Spell")
check("a spell's abilities form one cast ability",
      result.complete and len(result.abilities) == 1
      and result.abilities[0].event == TriggerEvent.ON_CAST)

result = one("■ Blocker (reminder text is ignored.) ■ Double breaker ■ This creature can't "
             "attack.")
check("keywords and a static ability",
      result.complete and result.keywords == {Keyword.BLOCKER, Keyword.DOUBLE_BREAKER}
      and result.tables["GRANTS"][0][0] == "self")

result = one("■ Fushigiverse {N13} (summon this creature from your graveyard for a cost equal "
             "to {N13} minus that creature's cost. It can't cost less than {N1} .)")
check("cost icons give the numbers",
      result.complete and result.tables["FUSHIGIVERSE"] ==
      (13, 1, frozenset({Civilization.NATURE})), str(result.unknown))

# ── Strictness: nothing is half understood ──────────────────────────────────
for label, text in (
    ("an unknown sentence", "■ When you put this creature, dance."),
    ("one unknown part", "■ When you put this creature, draw a card. Then dance."),
    ("an unknown trigger", "■ Whenever the moon rises, draw a card."),
    ("a civilization used as a race",
     "■ When you put this creature, destroy all non-darkness creatures."),
    ("a card property used as a race",
     "■ Each of your multicolored creatures in the battle zone gets \"slayer\"."),
    ("two races joined by 'or'", "■ Evolution: Put on one of your Cybers or Origins."),
    ("a keyword the engine lacks",
     "■ Each of your creatures gets \"revolution change\"."),
    ("cut-off text", "■ When you put this creature, [[draw"),
    ("different modes only",
     "■ When you put this creature, choose 2 of the following. ► Draw a card. ► Draw 2 cards."),
):
    result = one(text)
    check(f"refused: {label}", not result.complete, str(result.unknown))

check("a real race is accepted",
      one("■ Evolution: Put on one of your Cyber Virus creatures.").complete)
check("rule 203.2: part of a race name matches",
      one("■ Each of your Dragon creatures gets \"speed attacker\".").complete)

repeated = "■ This creature can't attack."
result = translate("Twin", [Face(repeated), Face(repeated, "Spell")], RACES)
check("refused: stored data that repeats one side", not result.complete)


# ── Bringing back evolution creatures (801.1a) ──────────────────────────────
def card(cid, name, subtype=CardSubtype.NONE, slug=None):
    return CardDefinition(
        id=cid, slug=slug or name, name=name, cost=2, power=2000, card_type=CardType.CREATURE,
        card_subtype=subtype, civilizations=frozenset({Civilization.DARKNESS}),
        races=frozenset(), keywords=frozenset(),
        effects=scripted_effects(slug or name, cid) or (),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


state = GameState(
    players=[PlayerState(player_index=i, player_name=f"P{i}", deck=[card(1, "d")] * 5)
             for i in range(2)],
    turn_info=TurnInfo(turn_number=3, active_player=0, phase=Phase.MAIN))
state.players[0].graveyard += [
    GraveyardCard(definition=card(2, "plain")),
    GraveyardCard(definition=card(3, "other-plain")),
    GraveyardCard(definition=card(4, "evolution", CardSubtype.EVOLUTION)),
]
cobra = card(5, "Cobra", slug="Cobra,_Snake_Ninja_Holy_Chaos")
state.effect_stack.add_trigger(PendingTrigger(
    effect=cobra.effects[0], source_uid="cobra", source_card_id=cobra.id, controller=0))
asked = resolve_pending_triggers(state)
choice = asked.effect_stack.awaited_choice
offered = sorted(c.definition.name for c in asked.players[0].graveyard
                 if choice and c.uid in choice.valid_options)
check("801.1a: an evolution creature can't come back without a source",
      "evolution" not in offered and {"other-plain", "plain"} <= set(offered), str(offered))

print(f"\n{len(FAILURES)} failed")
sys.exit(1 if FAILURES else 0)
