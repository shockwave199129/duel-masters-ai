"""
tests/test_determinize.py - sampled worlds and the search over them.
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.card_scripts import OPPONENT_HAND_REVEALED
from core.cards import CardDefinition
from core.enums import CardSubtype, CardType, Civilization, Keyword, Phase
from core.player_state import PlayerState
from core.state import AwaitedChoice, GameState, ShieldBreakWindow, TurnInfo
from core.zones import Creature, HandCard, ManaCard, ShieldCard
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.zone_mover import place_in_zone
from search.determinize import sample_world
from search.ensemble import ensemble_search
from search.mcts import MCTS, SearchConfig

results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" - {detail}" if detail else ""))


def card(cid, name=None, power=1000, keywords=(), slug=None):
    name = name or f"card{cid}"
    return CardDefinition(
        id=cid, slug=slug or name, name=name, cost=1, power=power,
        card_type=CardType.CREATURE, card_subtype=CardSubtype.NONE,
        civilizations=frozenset([Civilization.FIRE]), races=frozenset(),
        keywords=frozenset(keywords), effects=tuple(),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(),
        is_multiface=False,
    )


CARDS = {i: card(i) for i in range(1, 41)}


def make_state(order=None):
    """
    ``order`` lists the 15 card ids hidden from player 0: the opponent's five
    hand cards, then their three shields, then their deck.
    """
    order = order or list(range(1, 16))
    opponent = PlayerState(
        player_index=1, player_name="P1",
        hand=[HandCard(CARDS[c], uid=f"oh{i}") for i, c in enumerate(order[:5])],
        shield_zone=[ShieldCard(CARDS[c], uid=f"os{i}") for i, c in enumerate(order[5:8])],
        deck=[CARDS[c] for c in order[8:]],
        mana_zone=[ManaCard(CARDS[30], uid="om0")],
    )
    me = PlayerState(
        player_index=0, player_name="P0",
        hand=[HandCard(CARDS[31], uid="mh0"), HandCard(CARDS[32], uid="mh1")],
        shield_zone=[ShieldCard(CARDS[c], uid=f"ms{i}") for i, c in enumerate((16, 17))],
        deck=[CARDS[c] for c in range(18, 28)],
        battle_zone=[Creature(card(33, "attacker", 3000), uid="mc0", controller=0,
                              owner=0, has_summoning_sickness=False)],
    )
    return GameState(players=(me, opponent),
                     turn_info=TurnInfo(turn_number=3, active_player=0, phase=Phase.ATTACK),
                     game_id="world-test")


def ids(cards):
    return [getattr(c, "definition", c).id for c in cards]


def hidden_from_me(state):
    me, them = state.players
    return (ids(them.hand), ids(them.shield_zone), ids(them.deck),
            ids(me.shield_zone), ids(me.deck))


def worlds(state, observer=0, count=40):
    return [sample_world(state, observer, random.Random(seed)) for seed in range(count)]


print("DM ENGINE - SAMPLED WORLD TESTS")

real = make_state()
before = hidden_from_me(real)
sampled = worlds(real)
check("The real game is left unchanged",
      hidden_from_me(real) == before and real.rng_salt == "")
check("Zone sizes stay the same",
      all([len(z) for z in hidden_from_me(w)] == [len(z) for z in before] for w in sampled))
check("The opponent keeps the same unseen cards",
      all(sorted(sum(hidden_from_me(w)[:3], [])) == list(range(1, 16)) for w in sampled))
check("My own unseen cards stay mine",
      all(sorted(sum(hidden_from_me(w)[3:], [])) == list(range(16, 28)) for w in sampled))
check("My hand and public zones are unchanged",
      all(ids(w.players[0].hand) == [31, 32] and ids(w.players[1].mana_zone) == [30]
          and ids(w.players[0].battle_zone) == [33] for w in sampled))
check("The opponent's hand is guessed differently",
      len({tuple(hidden_from_me(w)[0]) for w in sampled}) > 30)
check("My shields are guessed too, since I cannot look at them",
      len({tuple(hidden_from_me(w)[3]) for w in sampled}) > 20)
check("Each world has its own chance", len({w.rng_salt for w in sampled}) == len(sampled))

# Where the hidden cards really are must not matter
shuffled = list(range(1, 16))
random.Random(4).shuffle(shuffled)
other = make_state(shuffled)
other.players[0].shield_zone[0].definition, other.players[0].deck[0] = CARDS[18], CARDS[16]
check("The two real games differ only in hidden places",
      hidden_from_me(other) != before)
check("The same seed gives the same world whatever the real hidden cards are",
      all(hidden_from_me(a) == hidden_from_me(b) for a, b in zip(sampled, worlds(other))))

# A card that was public stays known when it goes back to the hand
real = make_state()
real.players[1].battle_zone = [Creature(CARDS[40], uid="seen", controller=1, owner=1)]
after = execute_action(real, get_legal_actions(real)[-1], validate=False)
check("The engine remembers cards in public zones",
      {"seen", "om0", "mc0"} <= after.public_uids and "oh0" not in after.public_uids)
returned = after.players[1].battle_zone.pop()
place_in_zone(after, 1, "hand", "returned")(returned.definition, returned.uid)
check("A returned creature stays in the opponent's guessed hand",
      all(w.players[1].find_in_hand("seen").definition.id == 40 for w in worlds(after)))

# Face-up shields are known
real = make_state()
real.players[1].shield_zone[0].is_face_up = True
check("A face-up shield is not guessed",
      all(w.players[1].shield_zone[0].definition.id == 6 for w in worlds(real)))

# A card that shows the opponent's hand
real = make_state()
real.players[0].battle_zone.append(Creature(
    card(34, "watcher", slug=next(iter(OPPONENT_HAND_REVEALED))),
    uid="watch", controller=0, owner=0))
check("A hand being shown to me is not guessed",
      all(ids(w.players[1].hand) == [1, 2, 3, 4, 5] for w in worlds(real)))

# Cards I am looking at while I choose
real = make_state()
real.effect_stack.set_choice(AwaitedChoice(
    choice_type="select_card", player=0, effect=None, source_uid="mc0",
    valid_options=["deck:0", "deck:1"], option_cards={"deck:0": 18, "deck:1": 19}))
sampled = worlds(real)
check("Deck cards I am looking at stay where they are",
      all(ids(w.players[0].deck[:2]) == [18, 19] for w in sampled))
check("The rest of my deck is still guessed",
      len({tuple(ids(w.players[0].deck[2:])) for w in sampled}) > 20)

# A broken shield: its owner looks at it, the attacker cannot
real = make_state()
broken = real.players[1].shield_zone.pop(0)
broken.is_revealed = True
real.effect_stack.shield_break_window = ShieldBreakWindow(
    defending_player=1, standby_shields=[broken])
check("The owner knows the broken shield",
      all(w.effect_stack.shield_break_window.standby_shields[0].definition.id == 6
          for w in worlds(real, observer=1)))
check("The attacker does not",
      len({w.effect_stack.shield_break_window.standby_shields[0].definition.id
           for w in worlds(real)}) > 5)
real.effect_stack.shield_break_window.declared_s_triggers.add(broken.uid)
check("Once its use is declared, both players know it",
      all(w.effect_stack.shield_break_window.standby_shields[0].definition.id == 6
          for w in worlds(real)))


class PeekingEvaluator:
    """
    Equal priors, and a value that reads cards the chooser cannot see. A search
    that saw the real hidden cards would therefore depend on where they are.
    """

    def __call__(self, state, actions, db=None):
        hidden = state.players[1 - actions[0].player].hand
        weight = sum(card.definition.id * (i + 1) for i, card in enumerate(hidden))
        return [1.0 / len(actions)] * len(actions), (weight % 19) / 20


# The search itself must not depend on the real hidden cards
def searched(state):
    search = MCTS(PeekingEvaluator(), SearchConfig(simulations=40))
    return ensemble_search(search, state, get_legal_actions(state), trees=4, seed="s1")


def peeked(state):
    """One search of the real state, hidden cards and all."""
    return MCTS(PeekingEvaluator(), SearchConfig(simulations=40)).search(state)


check("This test would notice a search that sees hidden cards",
      peeked(make_state()).values != peeked(make_state(shuffled)).values,
      f"{peeked(make_state()).values} {peeked(make_state(shuffled)).values}")
first = searched(make_state())
second = searched(make_state(shuffled))
check("Search over sampled worlds ignores where hidden cards really are",
      first.visits == second.visits and first.values == second.values,
      f"{first.visits} {first.values} / {second.visits} {second.values}")
check("Visits from all trees are added up", sum(first.visits) == 4 * 40)
only = get_legal_actions(make_state())[:1]
forced = ensemble_search(MCTS(PeekingEvaluator()), make_state(), only, trees=4, seed="s")
check("A single legal move needs no worlds", forced.best_index == 0 and sum(forced.visits) == 0)

passed = sum(results)
print(f"RESULTS: {passed}/{len(results)} passed, {len(results) - passed} failed")
sys.exit(0 if passed == len(results) else 1)
