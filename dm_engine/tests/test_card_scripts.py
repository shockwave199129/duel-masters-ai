"""
tests/test_card_scripts.py - scripted card abilities behave as printed.

Cards are built in memory with their scripted effects, so no database is needed.
"""

import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.actions import attack_player, summon_creature
from core.card_scripts import SCRIPTS, scripted_effects
from core.cards import CardDefinition
from core.enums import CardSubtype, CardType, Civilization, Keyword, Phase
from core.initializer import initialize_game
from decks.prebuilt import make_demo_decks
from core.player_state import PlayerState
from core.state import GameState, PendingTrigger, TurnInfo
from core.zones import Creature, GraveyardCard, HandCard, ShieldCard
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.cards.effect_actions.zone_ops import _do_return_to_hand
from engine.sba_checker import check_state_based_actions
from engine.phase_controller import _end_turn, _start_turn
from engine.shield_break_window import execute_free_from_hand
from engine.turn.action_gen import _compute_effective_cost
from engine.trigger_resolver import resolve_pending_triggers
from engine.zone_mover import move_battle_to_graveyard, move_hand_to_battle

FAILURES: list[str] = []
_ids = itertools.count(1000)


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


def card(name, cost=3, power=3000, slug=None, civs=(Civilization.DARKNESS,), races=(),
         card_type=CardType.CREATURE):
    cid = next(_ids)
    slug = slug or name
    return CardDefinition(
        id=cid, slug=slug, name=name, cost=cost,
        power=power if card_type == CardType.CREATURE else None,
        card_type=card_type, card_subtype=CardSubtype.NONE, civilizations=frozenset(civs),
        races=frozenset(races), keywords=frozenset(),
        effects=scripted_effects(slug, cid) or (), evolution_source_races=frozenset(),
        evolution_source_types=frozenset(), is_multiface=False,
    )


def filler(name="filler", cost=3, **kwargs):
    return card(name, cost=cost, **kwargs)


def make_state(deck=10):
    players = [
        PlayerState(player_index=i, player_name=f"P{i}", deck=[filler(f"deck{i}-{n}") for n in range(deck)])
        for i in range(2)
    ]
    return GameState(players=players, turn_info=TurnInfo(turn_number=3, active_player=0, phase=Phase.MAIN))


def creature(state, player, definition, **kwargs):
    made = Creature(definition=definition, controller=player, owner=player, **kwargs)
    state.players[player].battle_zone.append(made)
    made.apply_static_effects(state)
    return made


def hand(state, player, *definitions):
    made = [HandCard(definition=d) for d in definitions]
    state.players[player].hand.extend(made)
    return made


def grave(state, player, *definitions):
    made = [GraveyardCard(definition=d) for d in definitions]
    state.players[player].graveyard.extend(made)
    return made


def summon(state, definition, player=0):
    """Put a card from hand into the battle zone and resolve what it triggers."""
    (held,) = hand(state, player, definition)
    move_hand_to_battle(state, player, held.uid)
    return resolve_pending_triggers(state)


def attack(state, attacker):
    state.turn_info.phase = Phase.ATTACK
    return execute_action(state, attack_player(0, attacker.uid, attacker.definition.id), validate=False)


def waiting(state):
    return state.effect_stack.awaited_choice


def pick(state, option=None):
    """Answer the pending choice. option=None declines."""
    choice = waiting(state)
    actions = get_legal_actions(state)
    if option is None:
        action = next(a for a in actions if a.action_type.value == "pass")
    else:
        action = next(a for a in actions if a.target_uid == option)
    assert action.player == choice.player
    return execute_action(state, action, validate=False)


def names(cards):
    return sorted(c.definition.name for c in cards)


def done(state):
    return waiting(state) is None and not state.effect_stack.pending_triggers


def scripted(slug, **kwargs):
    return card(slug.replace("_", " "), slug=slug, **kwargs)


# ── "This card" abilities ignore other cards ────────────────────────────────
state = make_state()
creature(state, 0, scripted("Gaiash_Kaiser,_the_Meteor"))
after = summon(state, filler("other"), player=1)
check("'When you put this' ignores other creatures entering", not after.players[0].hand)

# ── Oa:Nateha ───────────────────────────────────────────────────────────────
state = make_state()
hand(state, 1, filler("a"), filler("b"))
state = summon(state, scripted("Oa:Nateha"))
check("Oa: opponent discards one at random", len(state.players[1].hand) == 1 and len(state.players[1].graveyard) == 1)
move_battle_to_graveyard(state, 0, state.players[0].battle_zone[0].uid, reason="battle")
state = resolve_pending_triggers(state)
check("Oa: opponent draws when it is destroyed", len(state.players[1].hand) == 2, str(len(state.players[1].hand)))

# ── Kawagutsu ───────────────────────────────────────────────────────────────
state = make_state()
tough = creature(state, 1, filler("tough", power=5000))
weak = creature(state, 1, filler("weak", power=2000))
paused = summon(state, scripted("Kawagutsu_=_Aguus"))
offered = {a.card_id for a in get_legal_actions(paused)}
check("Kawagutsu: choice names the target cards", offered == {tough.id, weak.id}, str(offered))
after = pick(paused, weak.uid)
check("Kawagutsu: -2000 destroys a 2000 creature", names(after.players[1].battle_zone) == ["tough"] and done(after))
after = pick(paused, tough.uid)
check("Kawagutsu: power drops by 2000", after.find_creature_anywhere(tough.uid)[1].compute_power(after) == 3000)
_end_turn(after)
check("Opponent's creature recovers at end of turn",
      after.find_creature_anywhere(tough.uid)[1].compute_power(after) == 5000)

# ── Cobra ───────────────────────────────────────────────────────────────────
state = make_state()
cheap, dear = grave(state, 0, filler("cheap", cost=5), filler("dear", cost=6))
paused = summon(state, scripted("Cobra,_Snake_Ninja_Holy_Chaos"))
options = set(waiting(paused).valid_options)
check("Cobra: mills 2 first", len(paused.players[0].deck) == 8)
check("Cobra: only cost 5 or less", cheap.uid in options and dear.uid not in options)
after = pick(paused, cheap.uid)
check("Cobra: puts the chosen creature", "cheap" in names(after.players[0].battle_zone) and done(after))
for broken, survives in ((0, False), (1, False), (2, True)):
    state = make_state(deck=3)
    state.turn_info.active_player = 1
    state.players[0].deck = [filler("s", card_type=CardType.SPELL)] * 5
    state.players[0].shields_broken_this_turn = broken
    after = summon(state, scripted("Cobra,_Snake_Ninja_Holy_Chaos"))
    check(f"Cobra on the opponent's turn with {broken} shields broken survives={survives}",
          bool(after.players[0].battle_zone) == survives)

# ── Ater Gorghini ───────────────────────────────────────────────────────────
state = make_state()
victim = creature(state, 1, filler("victim", power=7000))
paused = summon(state, scripted("Ater_Gorghini"))
paused = pick(paused, "mode:weaken")
check("Ater: nothing resolves until both choices are made",
      paused.find_creature_anywhere(victim.uid)[1].compute_power(paused) == 7000 and waiting(paused) is not None)
paused = pick(paused, "mode:weaken")
if waiting(paused):
    paused = pick(paused, victim.uid)
if waiting(paused):
    paused = pick(paused, victim.uid)
check("Ater: the same mode twice stacks to -8000", not paused.players[1].battle_zone and done(paused))
state = make_state()
state = pick(pick(summon(state, scripted("Ater_Gorghini")), "mode:mill"), "mode:reanimate")
check("Ater: mill 4 happens before the later choice", len(state.players[0].deck) == 6)
if waiting(state):
    state = pick(state, waiting(state).valid_options[0])
check("Ater: returns a creature after milling", len(state.players[0].battle_zone) == 2 and done(state))

# ── Disaster Tune ───────────────────────────────────────────────────────────
state = make_state()
spell = scripted("Disaster_Tune", card_type=CardType.SPELL)
top = [d.name for d in state.players[0].deck[:4]]
state.effect_stack.add_trigger(PendingTrigger(
    effect=spell.effects[0], source_uid="spell", source_card_id=spell.id, controller=0))
state = resolve_pending_triggers(state)
for index in (2, 0):          # third card to hand, then first remaining to mana
    state = pick(state, f"deck:{index}")
state = pick(state, "deck:1")  # of the two left, the second to the graveyard
p0 = state.players[0]
check("Disaster Tune: one card to each place",
      [c.definition.name for c in p0.hand] == [top[2]] and [m.definition.name for m in p0.mana_zone] == [top[0]]
      and [g.definition.name for g in p0.graveyard] == [top[3]], f"{names(p0.hand)} {names(p0.mana_zone)}")
check("Disaster Tune: the last card goes to the bottom", p0.deck[-1].name == top[1] and len(p0.deck) == 7 and done(state))

# ── Broom, Gaiash ───────────────────────────────────────────────────────────
state = summon(make_state(), scripted("Broom_=_Proof"))
check("Broom: mills 1", len(state.players[0].graveyard) == 1 and len(state.players[0].deck) == 9)
state = summon(make_state(), scripted("Gaiash_Kaiser,_the_Meteor"))
check("Gaiash: draws 2", len(state.players[0].hand) == 2)

# ── Kernel ──────────────────────────────────────────────────────────────────
state = make_state()
target = creature(state, 1, filler("target"))
state = summon(state, scripted("Kernel,_Blue_Stagnation_Dragon_Elemental_/_Silent_Jealousy"))
_end_turn(state)
_start_turn(state)  # the opponent's turn
locked = state.find_creature_anywhere(target.uid)[1]
check("Kernel: the target can't attack on its own turn", not locked.can_attack())
_end_turn(state)
check("Kernel: still locked after the opponent's turn ends", not locked.can_attack())
_start_turn(state)  # the controller's next turn
check("Kernel: released when your next turn starts", "locked_until_turn_of" not in locked.temp_flags)

# ── Jashin Emperor ──────────────────────────────────────────────────────────
state = make_state()
grave(state, 0, filler("g1"), filler("g2"))
small = creature(state, 1, filler("cost3", cost=3))
creature(state, 1, filler("cost4", cost=4))
state = summon(state, scripted("Jashin_Emperor,_Abyssal_Darkness"))
check("Jashin: limit counts the card just milled (3)",
      names(state.players[1].battle_zone) == ["cost4"] and done(state), str(names(state.players[1].battle_zone)))
state = make_state()
boss = creature(state, 0, scripted("Jashin_Emperor,_Abyssal_Darkness"))
grave(state, 0, filler("six", cost=6), filler("seven", cost=7), scripted("CRYMAX_Jaouga", cost=5))
paused = attack(state, boss)
offered = [c.definition.name for c in paused.players[0].graveyard if c.uid in waiting(paused).valid_options]
check("Jashin attack: non-evolution cost 6 or less only", offered == ["six"], str(offered))
check("Jashin attack: may decline", len(pick(paused).players[0].battle_zone) == 1)

# ── Gotsunma ────────────────────────────────────────────────────────────────
state = make_state()
attacker = creature(state, 0, scripted("Gotsunma_=_Dhamma,_Soul_Abyss"))
cards = hand(state, 1, filler("h1"), filler("h2"), filler("h3"))
check("Gotsunma: nothing while Hyper Mode is not released (rule 816.3)",
      waiting(attack(state, attacker)) is None)
attacker.hyper_mode_released = True
paused = attack(state, attacker)
check("Gotsunma: the opponent chooses", waiting(paused).player == 1)
after = pick(pick(paused, cards[0].uid), cards[2].uid)
check("Gotsunma: discards exactly 2", names(after.players[1].hand) == ["h2"] and done(after))

# ── Gigandydanos ────────────────────────────────────────────────────────────
state = make_state()
hand(state, 1, filler("mono"), filler("multi", civs=(Civilization.FIRE, Civilization.WATER)))
state = summon(state, scripted("Gigandydanos,_Big_Tree_King"))
mana = {m.definition.name: m.is_tapped for m in state.players[1].mana_zone}
check("Gigandydanos: hand moves to mana, multicolor tapped",
      not state.players[1].hand and mana == {"mono": False, "multi": True}, str(mana))

# ── Jablood and Jabibirublood ───────────────────────────────────────────────
paused = summon(make_state(), scripted("Jablood,_Wicked_Dragon", races=("Abyss Dragon",)))
check("Jablood: milling is optional", len(pick(paused).players[0].deck) == 10)
check("Jablood: accepting mills 2", len(pick(paused, "yes").players[0].deck) == 8)

state = make_state()
jabibi = creature(state, 0, scripted("Jabibirublood,_Wicked_Soul_Dragon", races=("Abyss Dragon",)))
jabibi.hyper_mode_released = True  # the ability is a Hyper Mode ability (rule 816.3)
first = creature(state, 0, filler("abyss-a", races=("Abyss Royale",)))
second = creature(state, 0, filler("abyss-b", races=("Abyss Royale",)))
human = creature(state, 0, filler("human", races=("Human",)))
after_human = attack(state, human)
check("Jabibirublood: ignores non-Abyss attackers", len(after_human.players[0].deck) == 10)
after = attack(state, first)
while waiting(after):
    after = pick(after)
check("Jabibirublood: first Abyss attack mills 2", len(after.players[0].deck) == 8)
again = attack(after, after.find_creature_anywhere(second.uid)[1])
check("Jabibirublood: only once each turn", len(again.players[0].deck) == 8)
state = make_state()
creature(state, 1, scripted("Jabibirublood,_Wicked_Soul_Dragon", races=("Abyss Dragon",)))
foe = creature(state, 0, filler("their-abyss", races=("Abyss Royale",)))
check("Jabibirublood: ignores the opponent's Abyss", len(attack(state, foe).players[1].deck) == 10)

# ── Felix ───────────────────────────────────────────────────────────────────
state = make_state()
felix = creature(state, 0, scripted("Felix_Misery"))
dark, = grave(state, 0, filler("dark6", cost=6))
grave(state, 0, filler("fire", civs=(Civilization.FIRE,)))
paused = attack(state, felix)
choices = {c.definition.name for c in paused.players[0].graveyard if c.uid in waiting(paused).valid_options}
check("Felix: mills 2 and offers darkness creatures only", len(paused.players[0].deck) == 8 and "fire" not in choices)
check("Felix: puts the chosen creature", "dark6" in names(pick(paused, dark.uid).players[0].battle_zone))

# ── Babelginus ──────────────────────────────────────────────────────────────
state = make_state()
theirs = creature(state, 1, filler("theirs"))
returned, = grave(state, 1, filler("returned"))
paused = summon(state, scripted("Babelginus,_Demonic_Dragon"))
paused = pick(paused, theirs.uid)
if waiting(paused):
    paused = pick(paused, returned.uid)
check("Babelginus: destroys, then that player's graveyard returns a creature",
      len(paused.players[1].battle_zone) == 1 and done(paused), str(names(paused.players[1].battle_zone)))
state = make_state()
grave(state, 1, filler("stays"))
creature(state, 1, filler("safe"))
declined = pick(summon(state, scripted("Babelginus,_Demonic_Dragon")))
check("Babelginus: declining does nothing more",
      names(declined.players[1].battle_zone) == ["safe"] and done(declined))

# ── CRYMAX ──────────────────────────────────────────────────────────────────
state = make_state()
for player, count in ((0, 5), (1, 4)):
    state.players[player].shield_zone = [ShieldCard(definition=filler(f"sh{i}")) for i in range(count)]
state = summon(state, scripted("CRYMAX_Jaouga"))
hidden = waiting(state) is not None and not waiting(state).option_cards
while waiting(state):
    state = pick(state, waiting(state).valid_options[0])
check("CRYMAX: shield identities stay hidden while choosing", hidden)
check("CRYMAX: each player keeps 3 shields",
      [len(p.shield_zone) for p in state.players] == [3, 3] and [len(p.graveyard) for p in state.players] == [2, 1])
state = make_state()
crymax = creature(state, 0, scripted("CRYMAX_Jaouga"))
creature(state, 1, filler("doomed"))
hand(state, 1, filler("x"), filler("y"), filler("z"))
state = attack(state, crymax)
check("CRYMAX attack: destroys and discards 2 at random",
      not state.players[1].battle_zone and len(state.players[1].hand) == 1)

# ── Saga ────────────────────────────────────────────────────────────────────
state = make_state()
hand(state, 1, filler("o1"), filler("o2"), filler("o3"))
paused = summon(state, scripted("Saga_In_Zenaku"))
check("Saga: draws 3 first", len(paused.players[0].hand) == 3)
one = pick(pick(paused, waiting(paused).valid_options[0]))
check("Saga: one discard costs the opponent one",
      len(one.players[0].hand) == 2 and len(one.players[1].hand) == 2 and done(one))
none = pick(paused)
check("Saga: discarding nothing costs nothing", len(none.players[1].hand) == 3 and done(none))

# ── The Triple Three ────────────────────────────────────────────────────────
def cast_triple_three(state):
    spell = scripted("The_Triple_Three", card_type=CardType.SPELL)
    state.effect_stack.add_trigger(PendingTrigger(
        effect=spell.effects[0], source_uid="triple", source_card_id=spell.id, controller=0))
    return resolve_pending_triggers(state)


state = make_state()
foes = [creature(state, 1, filler(f"foe{i}")) for i in range(4)]
paused = cast_triple_three(state)
check("Triple Three: offers three options",
      sorted(waiting(paused).valid_options) == ["mode:discount", "mode:draw", "mode:tap"])
drawn = pick(paused, "mode:draw")
check("Triple Three draw: draws 3 and taps nothing",
      len(drawn.players[0].hand) == 3 and not any(c.is_tapped for c in drawn.players[1].battle_zone)
      and drawn.players[0].next_creature_discount == 0 and done(drawn))
tapping = pick(paused, "mode:tap")
for _ in range(3):
    tapping = pick(tapping, waiting(tapping).valid_options[0])
check("Triple Three tap: exactly 3 creatures, no cards drawn",
      sum(c.is_tapped for c in tapping.players[1].battle_zone) == 3
      and not tapping.players[0].hand and done(tapping))
state = make_state()
creature(state, 1, filler("lonely"))
tapping = pick(cast_triple_three(state), "mode:tap")
check("Triple Three tap: taps what exists", tapping.players[1].battle_zone[0].is_tapped and done(tapping))

state = pick(cast_triple_three(make_state()), "mode:discount")
for printed, expected in ((5, 2), (3, 1), (1, 1)):
    got = _compute_effective_cost(filler("c", cost=printed), state, 0)
    check(f"Triple Three discount: cost {printed} becomes {expected}", got == expected, str(got))
check("Triple Three discount: spells are not discounted",
      _compute_effective_cost(filler("s", cost=5, card_type=CardType.SPELL), state, 0) == 5)
held, = hand(state, 0, filler("fast", cost=5))
summoned = execute_action(
    state, summon_creature(0, held.uid, held.definition.id, []), validate=False)
check("Triple Three: the next creature can attack at once", summoned.players[0].battle_zone[0].can_attack())
check("Triple Three: the discount is used once",
      summoned.players[0].next_creature_discount == 0
      and _compute_effective_cost(filler("c2", cost=5), summoned, 0) == 5)
_end_turn(state)
check("Triple Three: an unused discount ends with the turn", state.players[0].next_creature_discount == 0)

# ── Galingmar ───────────────────────────────────────────────────────────────
state = summon(make_state(), scripted("Galingmar,_Hybrid_Starve_Mobile"))
galingmar = state.players[0].battle_zone[0]
check("Galingmar: is put tapped", galingmar.is_tapped)
check("Galingmar: EX Life adds a shield", len(state.players[0].shield_zone) == 1)
_end_turn(state); _end_turn(state); _start_turn(state)
check("Galingmar: does not untap at the start of your turn", state.players[0].battle_zone[0].is_tapped)


def cast_by(state, player):
    (held,) = hand(state, player, filler("some spell", card_type=CardType.SPELL))
    execute_free_from_hand(state, player, held.uid, reason="shield_trigger")
    return resolve_pending_triggers(state)


state = cast_by(state, 1)
check("Galingmar: the opponent's spell does not untap it", state.players[0].battle_zone[0].is_tapped)
state = cast_by(state, 0)
check("Galingmar: your spell untaps it", not state.players[0].battle_zone[0].is_tapped)

move_battle_to_graveyard(state, 0, state.players[0].battle_zone[0].uid, reason="effect")
check("Galingmar: EX Life shield is lost instead",
      len(state.players[0].battle_zone) == 1 and not state.players[0].shield_zone)
move_battle_to_graveyard(state, 0, state.players[0].battle_zone[0].uid, reason="effect")
check("Galingmar: leaves once EX Life is spent", not state.players[0].battle_zone)

# EX Life is part of entering, so nothing has to resolve first
state = make_state()
(held,) = hand(state, 0, scripted("Galingmar,_Hybrid_Starve_Mobile"))
move_hand_to_battle(state, 0, held.uid)
check("EX Life: the shield exists as soon as the creature enters", len(state.players[0].shield_zone) == 1)
state.players[0].battle_zone[0].apply_static_effects(state)
check("EX Life: re-applying effects adds no second shield", len(state.players[0].shield_zone) == 1)
state = make_state()
bau_mad = card("Bau Mad", slug="Bau_Mad_Romaion,_Concatenated_True_Wicked")
state = summon(state, bau_mad)
check("EX Life: Bau Mad Romaion has it too", len(state.players[0].shield_zone) == 1)
state.players[0].battle_zone[0].temp_flags["lost_battle"] = True
state = check_state_based_actions(state)
check("EX Life: saves from a lost battle once",
      len(state.players[0].battle_zone) == 1 and not state.players[0].shield_zone
      and "lost_battle" not in state.players[0].battle_zone[0].temp_flags)
state = make_state()
bounced = creature(state, 0, scripted("Galingmar,_Hybrid_Starve_Mobile"))
_do_return_to_hand(state, 1, PendingTrigger(
    effect=scripted("Disaster_Tune").effects[0], source_uid="x", source_card_id=1,
    controller=1, trigger_data={"target_uid": bounced.uid}))
check("EX Life: also replaces returning to hand",
      len(state.players[0].battle_zone) == 1 and not state.players[0].hand)
state = make_state()
creature(state, 1, scripted("Wald_Brachio,_Absolute_World_King"))
state = summon(state, scripted("Galingmar,_Hybrid_Starve_Mobile"))
check("EX Life: Wald Brachio cannot stop it", len(state.players[0].shield_zone) == 1)

# ── Bau Mad Romaion ─────────────────────────────────────────────────────────
BAU = "Bau_Mad_Romaion,_Concatenated_True_Wicked"


def spell_card(name, cost):
    return filler(name, cost=cost, card_type=CardType.SPELL)


state = make_state()
s1, s2, s3 = grave(state, 0, spell_card("s1", 2), spell_card("s2", 9), spell_card("s3", 1))
beast, = grave(state, 0, filler("beast"))
paused = summon(state, scripted(BAU))
check("Bau Mad: offers graveyard spells only",
      set(waiting(paused).valid_options) == {s1.uid, s2.uid, s3.uid}, str(waiting(paused).valid_options))
after = pick(pick(paused, s1.uid), s2.uid)
check("Bau Mad: returns 2 spells, no cost limit, no third",
      names(after.players[0].hand) == ["s1", "s2"] and done(after), str(names(after.players[0].hand)))
check("Bau Mad: may return none", not pick(paused).players[0].hand and done(pick(paused)))


def bau_attack(hand_cards):
    state = make_state()
    boss = creature(state, 0, scripted(BAU))
    held = hand(state, 0, *hand_cards)
    return attack(state, boss), held


paused, (cheap, limit, dear, minion) = bau_attack(
    [spell_card("cheap", 3), spell_card("limit", 8), spell_card("dear", 9), filler("minion")])
check("Bau Mad attack: spells costing 8 or less only",
      set(waiting(paused).valid_options) == {cheap.uid, limit.uid}, str(waiting(paused).valid_options))
cast = pick(paused, limit.uid)
check("Bau Mad attack: casts for free",
      "limit" in names(cast.players[0].graveyard) and "limit" not in names(cast.players[0].hand) and done(cast))
check("Bau Mad attack: may decline", len(pick(paused).players[0].hand) == 4)


def with_ex_life():
    state = summon(make_state(), scripted(BAU))
    (held,) = hand(state, 0, spell_card("answer", 4))
    return state, held


state, held = with_ex_life()
shield_uid = state.players[0].shield_zone[0].uid
broken = check_state_based_actions(state)
check("EX Life shield still there: nothing triggers", not broken.effect_stack.pending_triggers)
broken.players[0].shield_zone = [s for s in broken.players[0].shield_zone if s.uid != shield_uid]
broken = resolve_pending_triggers(check_state_based_actions(broken))
check("EX Life shield broken or removed: may cast a spell",
      waiting(broken) is not None and waiting(broken).valid_options == [held.uid])
broken = pick(broken, held.uid)
again = resolve_pending_triggers(check_state_based_actions(broken))
check("EX Life shield leaving triggers only once", done(again))

state, held = with_ex_life()
state.players[0].battle_zone[0].temp_flags["lost_battle"] = True
saved = resolve_pending_triggers(check_state_based_actions(state))
check("EX Life used to survive: the shield leaving triggers",
      len(saved.players[0].battle_zone) == 1 and waiting(saved) is not None)

state, held = with_ex_life()
uid = state.players[0].battle_zone[0].uid
move_battle_to_graveyard(state, 0, uid, reason="effect")
move_battle_to_graveyard(state, 0, uid, reason="effect")  # removed again before anything resolves
gone = resolve_pending_triggers(state)
check("Still triggers if the creature leaves right after",
      not gone.players[0].battle_zone and waiting(gone) is not None)

# ── Wald Brachio ────────────────────────────────────────────────────────────
for suppressor in ("Wald_Brachio,_Absolute_World_King", "Brachiodrange"):
    state = make_state()
    creature(state, 1, scripted(suppressor))
    state = summon(state, scripted("Gaiash_Kaiser,_the_Meteor"))
    check(f"{suppressor}: opponent's 'when you put this' does not trigger", not state.players[0].hand)
    state = summon(state, scripted("Gaiash_Kaiser,_the_Meteor"), player=1)
    check(f"{suppressor}: its controller's creatures still trigger", len(state.players[1].hand) == 2)
state = make_state()
boss = creature(state, 0, scripted("Felix_Misery"))
creature(state, 1, scripted("Wald_Brachio,_Absolute_World_King"))
check("Wald Brachio: attack abilities still trigger", len(attack(state, boss).players[0].deck) == 8)

# ── Names a player may declare come from the game's decks ──────────────────
deck_a, deck_b = make_demo_decks()
game = initialize_game(deck_a, deck_b, first_player=0, seed=1, game_id="names")
expected_names = {d.name for deck in (deck_a, deck_b) for d in deck.card_definitions.values() if d.is_creature()}
check("A new game can name every creature in its decks", set(game.nameable_creatures) == expected_names)

# ── Zardiclica ──────────────────────────────────────────────────────────────
ZAR = "Zardiclica,_Hybrid_Winds_Dragon"
state = make_state()
in_hand, too_dear = hand(state, 0, spell_card("from-hand", 7), spell_card("dear", 8))
in_grave, = grave(state, 0, spell_card("from-grave", 2))
paused = summon(state, scripted(ZAR))
check("Zardiclica: has EX Life", len(paused.players[0].shield_zone) == 1)
check("Zardiclica: spells 7 or less from hand or graveyard",
      set(waiting(paused).valid_options) == {in_hand.uid, in_grave.uid})
for chosen, label in ((in_hand, "from-hand"), (in_grave, "from-grave")):
    cast = pick(paused, chosen.uid)
    check(f"Zardiclica: the {label} spell goes to the bottom of the deck",
          cast.players[0].deck[-1].name == label and cast.players[0].spells_cast_this_turn == 1
          and label not in names(cast.players[0].graveyard) + names(cast.players[0].hand))
check("Zardiclica: may decline", pick(paused).players[0].spells_cast_this_turn == 0)


def end_turn_action(state):
    _end_turn(state)
    return resolve_pending_triggers(state)


for spells, label in ((0, "without a spell"), (1, "after a spell")):
    state = make_state()
    creature(state, 0, scripted(ZAR))
    small = creature(state, 1, filler("small", power=5500))
    creature(state, 1, filler("big", power=5501))
    state.players[0].spells_cast_this_turn = spells
    ended = end_turn_action(state)
    if not spells:
        check("Zardiclica: nothing at turn end without a spell", waiting(ended) is None)
        continue
    check("Zardiclica turn end: only power 5500 or less", waiting(ended).valid_options == [small.uid])
    hit = pick(ended, small.uid)
    check("Zardiclica turn end: destroys then draws",
          names(hit.players[1].battle_zone) == ["big"] and len(hit.players[0].hand) == 1)
    check("Zardiclica turn end: declining draws nothing", not pick(ended).players[0].hand)
state = make_state()
creature(state, 0, scripted(ZAR))
creature(state, 1, filler("small", power=1000))
state.turn_info.active_player = 1
state.players[0].spells_cast_this_turn = 1
check("Zardiclica: not at the end of the opponent's turn", waiting(end_turn_action(state)) is None)

# ── Digorpion ───────────────────────────────────────────────────────────────
DIG = "Digorpion,_Ground_Digging_Snakekind"
state = summon(make_state(), scripted(DIG, races=("Jurassic Command Dragon",)))
check("Digorpion alone: goes to mana itself",
      not state.players[0].battle_zone and names(state.players[0].mana_zone) == ["Digorpion, Ground Digging Snakekind"]
      and len(state.players[0].deck) == 10, str(names(state.players[0].mana_zone)))
state = make_state()
creature(state, 0, filler("wyrm", races=("Armored Dragon",)))
state = summon(state, scripted(DIG, races=("Jurassic Command Dragon",)))
check("Digorpion with another Dragon: stays and charges the top card",
      len(state.players[0].battle_zone) == 2 and names(state.players[0].mana_zone) == ["deck0-0"])
state = make_state()
creature(state, 1, filler("their-wyrm", races=("Armored Dragon",)))
state = summon(state, scripted(DIG, races=("Jurassic Command Dragon",)))
check("Digorpion: the opponent's Dragon does not count", not state.players[0].battle_zone)

# ── Mitsurugi Boost ─────────────────────────────────────────────────────────
MIT = "Mitsurugi_Boost,_Matchless_Dragon_Demon"
state = make_state()
weak = creature(state, 1, filler("weak", power=6000))
creature(state, 1, filler("strong", power=6001))
paused = summon(state, scripted(MIT))
kept = pick(paused)
check("Mitsurugi: declining keeps it and destroys nothing",
      len(kept.players[0].battle_zone) == 1 and len(kept.players[1].battle_zone) == 2 and done(kept))
used = pick(paused, "yes")
check("Mitsurugi: goes to mana, then destroys 6000 or less",
      not used.players[0].battle_zone and len(used.players[0].mana_zone) == 1
      and names(used.players[1].battle_zone) == ["strong"] and done(used), str(names(used.players[1].battle_zone)))

# ── Onimaru ─────────────────────────────────────────────────────────────────
ONI = "Onimaru_%22Head%22,_Victory_Rush"
for mine, theirs, wins in ((5, 4, True), (4, 4, True), (3, 4, False)):
    state = make_state()
    oni = creature(state, 0, scripted(ONI, power=9000))
    state.players[0].deck = [filler("mine", cost=mine), filler("next")]
    state.players[1].deck = [filler("theirs", cost=theirs), filler("next")]
    after = attack(state, oni)
    check(f"Onimaru Gachinko {mine} vs {theirs}: extra turn={wins}", after.players[0].extra_turns == int(wins))
    check("Gachinko puts revealed cards on the bottom",
          after.players[0].deck[-1].name == "mine" and after.players[1].deck[-1].name == "theirs")
attacker_now = after.find_creature_anywhere(oni.uid)[1]
check("Onimaru: +5000 while attacking", attacker_now.compute_power(after) == 14000,
      str(attacker_now.compute_power(after)))
after.attack_context = None
check("Onimaru: no bonus when not attacking", attacker_now.compute_power(after) == 9000)
state = make_state()
state.players[0].extra_turns = 1
_end_turn(state)
check("Extra turn: the same player goes again", state.turn_info.active_player == 0 and state.players[0].extra_turns == 0)
_end_turn(state)
check("Extra turn: then play passes on", state.turn_info.active_player == 1)

# ── Emotional Hardcore ──────────────────────────────────────────────────────
EMO = "Emotional_Hardcore,_Sacred_Dragon"
state = make_state()
victim_def = scripted("Gaiash_Kaiser,_the_Meteor")
shown = creature(state, 1, victim_def)
grave(state, 1, filler("other-name"))
hand(state, 1, filler("secret"))
creature(state, 0, filler("my-own"))
state.nameable_creatures = {"not-seen-yet": 5001, "Emotional Hardcore, Sacred Dragon": 5002}
paused = summon(state, scripted(EMO, races=("Oracle Dragon",)))
offered = set(waiting(paused).valid_options)
check("Emotional Hardcore: any creature name, mine, theirs or not yet seen (never itself)",
      offered == {f"name:{victim_def.name}", "name:other-name", "name:my-own", "name:not-seen-yet"},
      str(offered))
check("Emotional Hardcore: hidden hand cards are not revealed", "name:secret" not in offered)

# A card named before anyone has seen it is still ignored when it arrives
future = filler("not-seen-yet")
object.__setattr__(future, "keywords", frozenset({Keyword.BLOCKER, Keyword.DOUBLE_BREAKER}))
later = pick(paused, "name:not-seen-yet")
later = summon(later, future, player=1)
arrived = next(c for c in later.players[1].battle_zone if c.definition.name == "not-seen-yet")
check("Naming ahead: abilities are ignored when it arrives",
      not arrived.is_blocker() and arrived.shields_broken_on_attack() == 1)

# Naming your own creature affects it too
mine = pick(paused, "name:my-own")
check("Naming my own creature marks it",
      next(c for c in mine.players[0].battle_zone if c.definition.name == "my-own")
      .temp_flags.get("abilities_ignored") is True)
named = pick(paused, f"name:{victim_def.name}")
entered = summon(named, victim_def, player=1)
check("Named creature: its 'when you put' ability is ignored", not entered.players[1].hand[1:])
check("Named creature: hand unchanged apart from the secret card", len(entered.players[1].hand) == 1)
other = summon(named, scripted("Oa:Nateha"), player=1)
check("Unnamed creatures still work", done(other))
double = card("Twin", slug="twin")
object.__setattr__(double, "keywords", frozenset({Keyword.DOUBLE_BREAKER, Keyword.BLOCKER}))
state = make_state()
twin = creature(state, 1, double)
# Only one card name is on show, so it is chosen automatically
state = summon(state, scripted(EMO))
check("Exactly one Emotional Hardcore is in play",
      [c.definition.slug for c in state.players[0].battle_zone] == [EMO] and done(state))
ignored = state.find_creature_anywhere(twin.uid)[1]
check("Named creature: loses Blocker and Double Breaker",
      not ignored.is_blocker() and ignored.shields_broken_on_attack() == 1)
source = next(c for c in state.players[0].battle_zone if c.definition.slug == EMO)
move_battle_to_graveyard(state, 0, source.uid, reason="effect")
state = check_state_based_actions(state)
restored = state.find_creature_anywhere(twin.uid)[1]
check("Abilities return when Emotional Hardcore leaves",
      restored.is_blocker() and restored.shields_broken_on_attack() == 2)

for oracle, protected in ((False, False), (True, True)):
    state = make_state()
    emo = creature(state, 1, scripted(EMO, races=("Oracle Dragon",), power=1000))
    if oracle:
        creature(state, 1, filler("friend", races=("Oracle",), power=1000))
    paused = summon(state, scripted("Kawagutsu_=_Aguus"))
    targets = waiting(paused).valid_options if waiting(paused) else []
    chosen_automatically = not waiting(paused) and bool(paused.find_creature_anywhere(emo.uid))
    can_pick = emo.uid in targets or (not oracle and paused.find_creature_anywhere(emo.uid) is None)
    check(f"Emotional Hardcore can't be chosen={protected} (another Oracle={oracle})", can_pick != protected)

check("Every scripted card has a unique ability key",
      all(len({a.key for a in abilities}) == len(abilities) for abilities in SCRIPTS.values()))

if FAILURES:
    print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
    sys.exit(1)
