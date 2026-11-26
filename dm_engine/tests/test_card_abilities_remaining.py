"""
tests/test_card_abilities_remaining.py - replacement choices, blocking, Hyper
Mode and the remaining abilities of the cards in decks 1 to 4.
"""

import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.card_scripts import EXTRA_KEYWORDS, scripted_effects
from core.cards import CardDefinition
from core.continuous_rules import shield_has_trigger
from core.enums import (
    ActionType, CardSubtype, CardType, Civilization, GameResult, Keyword, Phase,
)
from core.player_state import PlayerState
from core.state import AttackContext, GameState, TurnInfo
from core.zones import Creature, GraveyardCard, HandCard, ManaCard, ShieldCard
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.phase_controller import _start_turn
from engine.sba_checker import check_state_based_actions
from engine.shield_break_window import execute_free_from_hand
from engine.trigger_resolver import resolve_pending_triggers
from engine.zone_mover import move_battle_to_graveyard, move_battle_to_zone, move_hand_to_battle

FAILURES: list[str] = []
_ids = itertools.count(9000)
D, N, L, W, F = (Civilization.DARKNESS, Civilization.NATURE, Civilization.LIGHT,
                 Civilization.WATER, Civilization.FIRE)


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


def card(name, cost=3, power=3000, slug=None, civs=(D,), races=(),
         card_type=CardType.CREATURE, subtype=CardSubtype.NONE, keywords=()):
    cid = next(_ids)
    slug = slug or name
    return CardDefinition(
        id=cid, slug=slug, name=name, cost=cost,
        power=power if card_type == CardType.CREATURE else None, card_type=card_type,
        card_subtype=subtype, civilizations=frozenset(civs), races=frozenset(races),
        keywords=frozenset(keywords) | frozenset(EXTRA_KEYWORDS.get(slug, ())),
        effects=scripted_effects(slug, cid) or (),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(), is_multiface=False,
    )


def named(slug, **kwargs):
    return card(slug.replace("_", " "), slug=slug, **kwargs)


def spell(slug, cost=3, **kwargs):
    return named(slug, cost=cost, card_type=CardType.SPELL, **kwargs)


def make_state(phase=Phase.MAIN, deck=14):
    players = [
        PlayerState(player_index=i, player_name=f"P{i}",
                    deck=[card(f"deck{i}-{n}") for n in range(deck)])
        for i in range(2)
    ]
    return GameState(players=players,
                     turn_info=TurnInfo(turn_number=3, active_player=0, phase=phase))


def creature(state, player, definition, **kwargs):
    kwargs.setdefault("has_summoning_sickness", False)
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


def mana(state, player, count, civ=D):
    made = [ManaCard(definition=card(f"mana{i}", civs=(civ,))) for i in range(count)]
    state.players[player].mana_zone.extend(made)
    return made


def waiting(state):
    return state.effect_stack.awaited_choice


def legal(state, kind):
    return [a for a in get_legal_actions(state) if a.action_type == kind]


def act(state, kind, **match):
    action = next(a for a in legal(state, kind)
                  if all(getattr(a, k) == v for k, v in match.items()))
    return execute_action(state, action, validate=False)


def pick(state, option=None):
    if option is None:
        return act(state, ActionType.PASS)
    return act(state, ActionType.SELECT_TARGET, target_uid=option)


def finish(state):
    """Answer every choice with its first option."""
    for _ in range(40):
        if waiting(state) is None:
            return state
        state = pick(state, waiting(state).valid_options[0])
    raise AssertionError("choices never ended")


def names(zone):
    return [c.definition.name for c in zone]


def destroy(state, player, target):
    move_battle_to_graveyard(state, player, target.uid, reason="effect")
    return resolve_pending_triggers(state)


def enter(state, definition, player=0, **how):
    (held,) = hand(state, player, definition)
    move_hand_to_battle(state, player, held.uid, **how)
    return resolve_pending_triggers(state)


def cast(state, definition, player=0):
    (held,) = hand(state, player, definition)
    execute_free_from_hand(state, player, held.uid, reason="cast")
    return resolve_pending_triggers(state)


# ── Replacement effects: the player chooses (101.5, 609) ────────────────────
state = make_state()
ater = creature(state, 0, named("Ater_Gorghini"))
other = creature(state, 0, card("other"))
asked = destroy(state, 0, ater)
check("Ater: asks before leaving, and may decline",
      waiting(asked) is not None and f"destroy_other:{ater.uid}" in waiting(asked).valid_options
      and "leave" in waiting(asked).valid_options and len(asked.players[0].battle_zone) == 2)
kept = pick(asked, f"destroy_other:{ater.uid}")
check("Ater: another creature is destroyed instead",
      names(kept.players[0].battle_zone) == ["Ater Gorghini"]
      and names(kept.players[0].graveyard) == ["other"])
gone = pick(asked, "leave")
check("Ater: declining lets it leave", names(gone.players[0].battle_zone) == ["other"])

state = make_state()
a1 = creature(state, 0, named("Ater_Gorghini"))
a2 = creature(state, 0, named("Ater_Gorghini"))
run = pick(destroy(state, 0, a1), f"destroy_other:{a1.uid}")
run = pick(run, f"destroy_other:{a2.uid}")
check("609.3: two such cards cannot save each other forever",
      waiting(run) is None and len(run.players[0].battle_zone) == 1, str(waiting(run)))

state = make_state()
bell = creature(state, 0, named("Abyssbell_=_Jashin_Emperor", races=("Abyss Dragon",)))
hand(state, 0, card("a"), card("b"), card("c"))
run = pick(destroy(state, 0, bell), f"discard:{bell.uid}")
run = pick(pick(run, run.players[0].hand[0].uid), run.players[0].hand[1].uid)
run = finish(run)
check("Abyssbell: discards 2 and stays",
      len(run.players[0].hand) == 1 and len(run.players[0].battle_zone) == 1)
state = make_state()
bell = creature(state, 0, named("Abyssbell_=_Jashin_Emperor"))
hand(state, 0, card("only"))
check("609.4: not offered when it can't be paid",
      waiting(destroy(state, 0, bell)) is None)

state = make_state()
vejita = creature(state, 0, named("Million_Vejita,_Tyrant_T"))
friend = creature(state, 0, card("friend"))
mana(state, 0, 4)
run = finish(pick(destroy(state, 0, friend), f"mana:{vejita.uid}"))
check("Vejita: 3 mana go to the graveyard instead",
      len(run.players[0].mana_zone) == 1 and len(run.players[0].battle_zone) == 2
      and len(run.players[0].graveyard) == 3)

state = make_state()
jab = creature(state, 0, named("Jablood,_Wicked_Dragon", races=("Abyss Dragon",)))
abyss = creature(state, 0, card("an-abyss", races=("Abyss Royale",)))
grave(state, 0, *[card(f"g{n}") for n in range(5)])
run = finish(pick(destroy(state, 0, abyss), f"shuffle_grave:{jab.uid}"))
check("Jablood: 4 graveyard cards go back instead",
      len(run.players[0].graveyard) == 1 and len(run.players[0].deck) == 18
      and len(run.players[0].battle_zone) == 2)

state = make_state()
anago = creature(state, 0, named("T_Anago", civs=(W,)))
wet = creature(state, 0, card("water", civs=(W,)))
asked = destroy(state, 0, wet)
check("Saver: offered for a water creature", waiting(asked) is not None)
run = pick(asked, f"saver:{anago.uid}")
check("Saver: it is destroyed instead", names(run.players[0].battle_zone) == ["water"])
state = make_state()
anago = creature(state, 0, named("T_Anago", civs=(W,)))
wet = creature(state, 0, card("water", civs=(W,)))
move_battle_to_zone(state, 0, wet.uid, reason="returned", to_zone="hand")
check("Saver: not for returning to hand", not state.effect_stack.pending_triggers
      and names(state.players[0].hand) == ["water"])

state = make_state()
van = creature(state, 0, named("VAN_Beethoven,_Zenith_of_%22Shura%22"))
run = destroy(state, 0, van)
check("Eternal Omega: returns to hand instead",
      len(run.players[0].hand) == 1 and not run.players[0].graveyard)

# ── Blocking: several blockers may be used (507.1, 507.2a) ──────────────────
state = make_state(phase=Phase.BLOCK_DECLARE)
attacker = creature(state, 0, card("attacker", power=1000))
walls = [creature(state, 1, card(f"wall{i}", keywords=(Keyword.BLOCKER,))) for i in range(2)]
state.attack_context = AttackContext(attacker.uid, 0, "player", None)
one = act(state, ActionType.DECLARE_BLOCKER, card_uid=walls[0].uid)
check("507.1: after one blocker another may be used",
      one.current_phase == Phase.BLOCK_DECLARE
      and [a.card_uid for a in legal(one, ActionType.DECLARE_BLOCKER)] == [walls[1].uid])
two = act(one, ActionType.DECLARE_BLOCKER, card_uid=walls[1].uid)
blocking = [c.name for c in two.players[1].battle_zone if c.is_blocking]
check("507.2a: only the last one is blocking, both are tapped",
      two.current_phase == Phase.BATTLE and blocking == ["wall1"]
      and all(c.is_tapped for c in two.players[1].battle_zone))
stop = act(one, ActionType.PASS)
check("507.1: or the defender may stop after one",
      stop.current_phase == Phase.BATTLE
      and stop.find_creature_anywhere(walls[0].uid)[1].is_blocking)

# ── Choosing each shield (509.3) ────────────────────────────────────────────
state = make_state(phase=Phase.DIRECT_ATTACK)
double = creature(state, 0, card("double", keywords=(Keyword.DOUBLE_BREAKER,)))
state.players[1].shield_zone = [ShieldCard(definition=card(f"s{i}")) for i in range(4)]
state.attack_context = AttackContext(double.uid, 0, "player", None)
first = act(state, ActionType.SELECT_ATTACK_ORDER, shield_index=3)
check("509.3: nothing breaks until all are chosen",
      len(first.players[1].shield_zone) == 4
      and 3 not in [a.shield_index for a in legal(first, ActionType.SELECT_ATTACK_ORDER)])
both = act(first, ActionType.SELECT_ATTACK_ORDER, shield_index=1)
check("509.3: the chosen shields break", sorted(names(both.players[1].shield_zone)) == ["s0", "s2"])

state = make_state(phase=Phase.DIRECT_ATTACK)
bolme = creature(state, 0, named("Bolmeteus_Sapphire_Dragon"))
state.players[1].shield_zone = [ShieldCard(definition=card(f"s{i}")) for i in range(3)]
state.attack_context = AttackContext(bolme.uid, 0, "player", None)
run = act(state, ActionType.SELECT_ATTACK_ORDER, shield_index=2)
check("Bolmeteus: broken shields go to the graveyard",
      names(run.players[1].graveyard) == ["s2"] and not run.players[1].hand)

# ── Hyper Mode (816) ────────────────────────────────────────────────────────
state = make_state()
jabibi = creature(state, 0, named("Jabibirublood,_Wicked_Soul_Dragon", power=4000))
helper = creature(state, 0, card("helper"))
check("816.3: before release, normal power and one shield",
      jabibi.compute_power(state) == 4000 and jabibi.shields_broken_on_attack() == 1
      and jabibi.is_blocker())
offers = legal(state, ActionType.HYPERIZE)
check("Hyperify: paid by tapping another creature",
      [a.target_uid for a in offers] == [helper.uid])
released = execute_action(state, offers[0], validate=False)
big = released.find_creature_anywhere(jabibi.uid)[1]
check("816.2: hyper power and Double breaker, once only",
      big.compute_power(released) == 10000 and big.shields_broken_on_attack() == 2
      and released.find_creature_anywhere(helper.uid)[1].is_tapped
      and not legal(released, ActionType.HYPERIZE))
released.turn_info.turn_number += 2
_start_turn(released)
check("Hyper Mode ends at your next turn",
      released.find_creature_anywhere(jabibi.uid)[1].compute_power(released) == 4000)

# ── Keywords from other cards ───────────────────────────────────────────────
state = make_state()
plain = creature(state, 0, card("plain"))
shrine = creature(state, 0, named("Memento_Guardian_Shrine,_Prison_Court_of_D",
                                  card_type=CardType.FIELD, subtype=CardSubtype.D2))
check("Memento: your creatures get blocker", plain.is_blocker())
state.players[0].battle_zone.remove(shrine)
shrine.remove_static_effects(state)
after = check_state_based_actions(state)
check("...only while it is there", not after.find_creature_anywhere(plain.uid)[1].is_blocker())

state = make_state()
dragon = creature(state, 1, card("dragon", races=("Armored Dragon",)))
dragonoid = creature(state, 1, card("dragonoid", races=("Dragonoid",)))
creature(state, 0, named("Codeking_Mozart", races=("King Command Dragon",)))
check("Mozart: opposing Dragons can't attack; a Dragonoid is not one",
      not dragon.can_attack() and dragonoid.can_attack())

state = make_state()
creature(state, 0, named("Ryusei_Kaiser,_the_Eternal"))
sick = creature(state, 0, card("fresh"), has_summoning_sickness=True)
theirs = creature(state, 1, card("theirs"))
check("Ryusei Kaiser: yours get speed attacker, theirs arrive tapped",
      sick.can_attack() and theirs.is_tapped and not sick.is_tapped)
state = make_state()
anago = creature(state, 0, named("T_Anago"))
mari = creature(state, 0, named("Mariflo-1,_Viral", keywords=(Keyword.BLOCKER,)))
check("Anago and Mariflo can't attack", not anago.can_attack() and not mari.can_attack())

# ── Deck 1 and 2 abilities ──────────────────────────────────────────────────
state = make_state()
run = pick(enter(state, named("Astral_Reef", subtype=CardSubtype.EVOLUTION)), "yes")
check("Astral Reef: may draw 3", len(run.players[0].hand) == 3)
from engine.evolution_rules import get_valid_evolution_bases
state = make_state()
virus = creature(state, 0, card("virus", races=("Cyber Virus",)))
creature(state, 0, card("human", races=("Human",)))
reef = named("Astral_Reef", subtype=CardSubtype.EVOLUTION)
check("Astral Reef evolves only on a Cyber Virus",
      get_valid_evolution_bases(reef, state.players[0].battle_zone) == [virus])

state = make_state()
hand(state, 0, card("light-dragon", cost=7, civs=(L,), races=("Angel Command Dragon",)),
     card("too-big", cost=8, civs=(L,), races=("Angel Command Dragon",)),
     card("dark-dragon", cost=5, races=("Demon Command Dragon",)))
asked = cast(state, spell("Dragon%27s_Sign"))
check("Dragon's Sign: only a Light Dragon costing 7 or less", len(waiting(asked).valid_options) == 1)
run = pick(asked, waiting(asked).valid_options[0])
check("...which gets blocker", run.players[0].battle_zone[0].is_blocker())
run.turn_info.turn_number += 2
_start_turn(run)
run = check_state_based_actions(run)
check("...until your next turn", not run.players[0].battle_zone[0].is_blocker())

state = make_state()
foes = [creature(state, 1, card(f"foe{i}")) for i in range(2)]
run = cast(state, spell("Holy_Awe"))
check("Holy Awe: taps them all", all(c.is_tapped for c in run.players[1].battle_zone))

for stored, expected in ((3, 1), (7, 3)):
    state = make_state()
    mana(state, 0, stored)
    run = cast(state, spell("Seventh_Tower"))
    check(f"Seventh Tower with {stored} mana adds {expected}",
          len(run.players[0].mana_zone) == stored + expected)

state = make_state()
state.players[0].deck[0:2] = [card("d", races=("Armored Dragon",)), card("not-dragon")]
run = cast(state, spell("Mendelssohn"))
check("Mendelssohn: the Dragon to mana tapped, the rest to graveyard",
      [(m.definition.name, m.is_tapped) for m in run.players[0].mana_zone] == [("d", True)]
      and "not-dragon" in names(run.players[0].graveyard))

state = make_state()
state.players[0].deck[0:3] = [card("c1"), card("sp", card_type=CardType.SPELL), card("c2")]
asked = enter(state, named("Teranesque,_Hard_Battling_Ancient_Dragon"))
run = pick(pick(asked, "deck:0"))
check("Teranesque: chosen creatures to hand, the rest to mana tapped",
      names(run.players[0].hand) == ["c1"] and names(run.players[0].mana_zone) == ["sp", "c2"]
      and all(m.is_tapped for m in run.players[0].mana_zone))

state = make_state()
state.players[0].deck = [card(f"spell{i}", card_type=CardType.SPELL) for i in range(4)]
run = cast(state, spell("Mystery_Cube"))
check("Mystery Cube: a non-creature goes to mana", len(run.players[0].mana_zone) == 1)
state = make_state()
run = cast(state, spell("Mystery_Cube"))
run = pick(run, "deck:0")
check("Mystery Cube: may put the revealed creature",
      len(run.players[0].battle_zone) == 1 and len(run.players[0].deck) == 13)

state = make_state()
used = mana(state, 0, 3)
for m in used:
    m.tap()
run = enter(state, named("Bolbalzak_Ex"))
check("Bolbalzak: untaps your mana", not any(m.is_tapped for m in run.players[0].mana_zone))
state = make_state()
creature(state, 0, card("own-dragon", races=("Armored Dragon",)))
creature(state, 0, card("own-human"))
creature(state, 1, card("their-human"))
run = enter(state, named("Codeking_Mozart", races=("King Command Dragon",)))
check("Mozart: destroys every non-Dragon",
      sorted(names(run.players[0].battle_zone)) == ["Codeking Mozart", "own-dragon"]
      and not run.players[1].battle_zone)

for summoned in (True, False):
    state = make_state()
    creature(state, 1, card("enemy"))
    run = enter(state, named("VAN_Beethoven,_Zenith_of_%22Shura%22"), summoned=summoned)
    check(f"VAN: returns opposing creatures only when summoned ({summoned})",
          (names(run.players[1].hand) == ["enemy"]) == summoned)
state = make_state()
creature(state, 1, named("VAN_Beethoven,_Zenith_of_%22Shura%22"))
run = enter(state, card("cmd", races=("Demon Command",)))
check("VAN: an opposing Command goes to the graveyard instead",
      not run.players[0].battle_zone and names(run.players[0].graveyard) == ["cmd"])

state = make_state()
creature(state, 0, named("Shura_Beethoven,_Zenith_of_%22Hadou%22",
                         races=("King Command Dragon",)))
asked = enter(state, card("new-dragon", races=("Armored Dragon",)))
check("Shura: summoning a Dragon may add 2 mana",
      len(pick(asked, "yes").players[0].mana_zone) == 2)
state = make_state()
creature(state, 0, named("Shura_Beethoven,_Zenith_of_%22Hadou%22",
                         races=("King Command Dragon",)))
check("Shura: not for other creatures", waiting(enter(state, card("man"))) is None)

state = make_state()
grave_card = named("Ryusei_Kaiser,_the_Eternal")
(held,) = hand(state, 1, grave_card)
asked = enter(state, named("Oa:Nateha"))
check("Ryusei Kaiser: discarded by an opponent, it may be put",
      waiting(asked) is not None and waiting(asked).player == 1)
check("...putting it", names(pick(asked, held.uid).players[1].battle_zone) == [grave_card.name])
check("...or discarding it", names(pick(asked).players[1].graveyard) == [grave_card.name])

# ── Deck 3 and 4 abilities ──────────────────────────────────────────────────
justin = spell("Just_In_You")
for count in (1, 2):
    state = make_state()
    for n in range(count):
        creature(state, 0, card(f"dk{n}"))
    check(f"Just In You with {count} darkness: shield trigger={count >= 2}",
          shield_has_trigger(state, 0, justin) == (count >= 2))

state = make_state(phase=Phase.ATTACK_DECLARE)
big = creature(state, 0, card("big-nature", power=5000, civs=(N,)))
(kaiser,) = hand(state, 0, spell("Kaiser_Victory,_Secret_Extreme_Triumph", civs=(N,)))
state.attack_context = AttackContext(big.uid, 0, "player", None)
check("Attack Chance: offered for a Nature creature with 4000 or more",
      [a.card_uid for a in legal(state, ActionType.USE_ATTACK_CHANCE)] == [kaiser.uid])
big.definition = card("small", power=3000, civs=(N,))
check("...not for a smaller one", not legal(state, ActionType.USE_ATTACK_CHANCE))

state = make_state()
grave(state, 0, spell("Kaiser_Victory,_Secret_Extreme_Triumph", civs=(N,)))
mana(state, 0, 4, N)
recast = legal(state, ActionType.CAST_SPELL)
check("Recycle: castable from the graveyard for 4", recast and len(recast[0].mana_used) == 4)
run = finish(execute_action(state, recast[0], validate=False))
check("...then to the bottom of the deck",
      run.players[0].deck[-1].name.startswith("Kaiser") and not run.players[0].graveyard)

for darkness in (3, 0):
    state = make_state(phase=Phase.ATTACK_DECLARE)
    raider = creature(state, 0, card("raider"))
    hand(state, 1, spell("Abyssal_Counterattack"))
    mana(state, 1, darkness)
    state.attack_context = AttackContext(raider.uid, 0, "player", None)
    offer = [a for a in legal(state, ActionType.USE_ATTACK_CHANCE)
             if a.get_extra().get("counterattack")]
    run = finish(execute_action(state, offer[0], validate=False))
    check("Counterattack: free now, and only once an attack",
          not run.players[1].mana_debts == [] and run.attack_context.counterattack_used)
    run.turn_info.active_player = 1
    run.turn_info.turn_number += 1
    _start_turn(run)
    if darkness:
        check("...paid by tapping 3 at your next turn",
              sum(m.is_tapped for m in run.players[1].mana_zone) == 3
              and run.result == GameResult.IN_PROGRESS)
    else:
        check("...and you lose if you can't pay", run.result == GameResult.PLAYER_0_WINS)

state = make_state()
sacrifice = creature(state, 0, card("nine", cost=9, civs=(N,)))
(brachio,) = grave(state, 0, named("Brachiodrange", cost=11, civs=(N,)))
mana(state, 0, 4, N)
offers = [a for a in legal(state, ActionType.SUMMON_CREATURE) if a.get_extra().get("fushigiverse")]
check("Fushigiverse: costs 13 minus the creature's cost",
      offers and all(len(a.mana_used) == 4 for a in offers))
run = execute_action(state, offers[0], validate=False)
check("...the creature goes to mana tapped and the card enters",
      names(run.players[0].battle_zone) == ["Brachiodrange"]
      and run.players[0].find_mana(sacrifice.uid).is_tapped)

state = make_state()
twin = named("Kuuzen,_Sinister_General_of_Thunderbolt_/_Dark_Life", cost=5, civs=(D, N))
(held,) = hand(state, 0, twin)
mana(state, 0, 1, D)
mana(state, 0, 1, N)
sides = [a for a in legal(state, ActionType.CAST_SPELL) if a.get_extra().get("twinpact_spell")]
run = finish(execute_action(state, sides[0], validate=False))
check("Dark Life: one top card to mana, one to graveyard, then the whole card",
      len(run.players[0].mana_zone) == 3 and len(run.players[0].graveyard) == 2
      and any(g.uid == held.uid and g.definition.card_type == CardType.CREATURE
              for g in run.players[0].graveyard))

state = make_state()
creature(state, 0, named("Saga_In_Zenaku", subtype=CardSubtype.G_NEO))
(lost,) = hand(state, 1, card("their-spell", card_type=CardType.SPELL))
from engine.zone_mover import move_hand_to_graveyard
move_hand_to_graveyard(state, 1, lost.uid, reason="effect")
asked = resolve_pending_triggers(state)
check("Saga: may cast the spell the opponent discarded",
      waiting(asked) is not None and waiting(asked).player == 0)
run = pick(asked, lost.uid)
check("...which returns to its owner's graveyard",
      names(run.players[1].graveyard) == ["their-spell"] and not run.players[0].graveyard)

state = make_state()
hand(state, 0, card("under"))
justice = named("Endless_Justice_Romanov_Bible", subtype=CardSubtype.G_NEO)
run = enter(state, justice)
run = pick(run, "yes")
run = pick(run, run.players[0].hand[0].uid)
top = run.players[0].battle_zone[0]
check("Endless Justice: draws, then puts a card under itself",
      len(top.evolution_stack) == 1 and len(run.players[0].hand) == 1)
victim = creature(run, 1, card("victim"))
run = destroy(run, 0, top)
run = pick(run, "mode:destroy")
run = finish(run)
check("...which leaves instead, setting off its other ability",
      len(run.players[0].battle_zone) == 1 and not run.players[1].battle_zone)

state = make_state()
creature(state, 0, named("Romanov_the_4th,_Demonic_Eye_Gaia"))
target = creature(state, 1, card("target"))
hustle = named("Hustle_Castle,_Garde_Seventh_Floor", cost=3, civs=(D,),
               card_type=CardType.CASTLE, subtype=CardSubtype.G_CASTLE)
hand(state, 0, hustle)
mana(state, 0, 3)
run = finish(act(state, ActionType.FORTIFY_CASTLE))
check("Hustle Castle: face up, and a card goes to mana tapped",
      run.players[0].shield_zone[0].is_face_up and len(run.players[0].mana_zone) == 4
      and run.players[0].mana_zone[-1].is_tapped)
check("Romanov: a face-up shield lets you destroy a creature",
      not run.players[1].battle_zone)
run = enter(run, card("sneaky"), player=1, summoned=False)
check("Hustle Castle: a creature that wasn't summoned goes to mana",
      not run.players[1].battle_zone and "sneaky" in names(run.players[1].mana_zone))
run = enter(run, card("honest"), player=1)
check("...a summoned one enters", names(run.players[1].battle_zone) == ["honest"])

# ── Only Darkness creatures and Tamaseeds count toward the 4 ───────────────
from core.continuous_rules import is_creature_now

for extra_type, expected in ((CardType.TAMASEED, True), (CardType.FIELD, False),
                             (CardType.CROSS_GEAR, False)):
    state = make_state()
    jashin = creature(state, 0, named("Jashin_Emperor,_Abyssal_Darkness"))
    for n in range(2):
        creature(state, 0, card(f"dk{n}"))
    creature(state, 0, card("fourth", card_type=extra_type))
    check(f"Jashin: a darkness {extra_type.value} counts={expected}",
          is_creature_now(state, jashin) == expected)

# ── Last Burst and Mega Last Burst ─────────────────────────────────────────
KUUZEN = "Kuuzen,_Sinister_General_of_Thunderbolt_/_Dark_Life"
state = make_state()
twin = creature(state, 0, named(KUUZEN, cost=5, civs=(D, N)))
asked = destroy(state, 0, twin)
check("Last Burst: offered when destroyed", waiting(asked) is not None)
run = finish(pick(asked, twin.uid))
check("...casting Dark Life, then the card returns to the graveyard",
      len(run.players[0].mana_zone) == 1 and len(run.players[0].graveyard) == 2
      and any(g.uid == twin.uid for g in run.players[0].graveyard))
declined = pick(asked)
check("...and it is optional",
      not declined.players[0].mana_zone and len(declined.players[0].graveyard) == 1)

state = make_state()
twin = creature(state, 0, named(KUUZEN, civs=(D, N)))
move_battle_to_zone(state, 0, twin.uid, reason="returned", to_zone="hand")
check("Last Burst: not when returned to hand",
      waiting(resolve_pending_triggers(state)) is None)

from dataclasses import replace as _replace
mega = _replace(named(KUUZEN, civs=(D, N)), keywords=frozenset({Keyword.MEGA_LAST_BURST}))
for zone, offered in (("hand", True), ("mana_zone", True), ("graveyard", True),
                      ("shield_zone", False), ("deck", False)):
    state = make_state()
    twin = creature(state, 0, mega)
    move_battle_to_zone(state, 0, twin.uid, reason="moved", to_zone=zone)
    asked = resolve_pending_triggers(state)
    check(f"Mega Last Burst: leaving to {zone} offers it={offered}",
          (waiting(asked) is not None) == offered)
    if offered:
        run = finish(pick(asked, twin.uid))
        check(f"...cast from the {zone}, ending in the graveyard",
              any(g.uid == twin.uid for g in run.players[0].graveyard)
              and not any(c.uid == twin.uid
                          for c in [*run.players[0].hand, *run.players[0].mana_zone]))

# ── CRYMAX: 3 face-up cards instead of leaving or losing ───────────────────
def crymax_setup(shields_face_up=1, others=2):
    state = make_state()
    boss = creature(state, 0, named("CRYMAX_Jaouga", subtype=CardSubtype.STAR_MAX))
    fodder = [creature(state, 0, card(f"fodder{i}")) for i in range(others)]
    for i in range(shields_face_up):
        state.players[0].shield_zone.append(
            ShieldCard(definition=card(f"open{i}"), is_face_up=True))
    state.players[0].shield_zone.append(ShieldCard(definition=card("hidden")))
    mana(state, 0, 5)
    return state, boss, fodder


def destroy_three(state):
    """Choose face-up cards, keeping CRYMAX itself."""
    while waiting(state) is not None:
        boss_uids = {c.uid for c in state.players[0].battle_zone
                     if c.definition.slug == "CRYMAX_Jaouga"}
        state = pick(state, next(o for o in waiting(state).valid_options if o not in boss_uids))
    return state


state, boss, fodder = crymax_setup()
asked = destroy(state, 0, boss)
check("CRYMAX: may replace leaving",
      waiting(asked) is not None and "leave" in waiting(asked).valid_options)
choosing = pick(asked, f"destroy_face_up:{boss.uid}")
offered = set(waiting(choosing).valid_options)
check("...choosing battle zone cards and face-up shields, never mana or hidden shields",
      offered == {f.uid for f in fodder} | {choosing.players[0].shield_zone[0].uid})
run = destroy_three(choosing)
check("...which are destroyed while CRYMAX stays",
      names(run.players[0].battle_zone) == ["CRYMAX Jaouga"]
      and names(run.players[0].shield_zone) == ["hidden"] and len(run.players[0].mana_zone) == 5)

state, boss, _ = crymax_setup(shields_face_up=0)
check("CRYMAX: not offered with only 2 face-up cards",
      waiting(destroy(state, 0, boss)) is None)

state, boss, _ = crymax_setup()
state.players[0].deck = []
asked = resolve_pending_triggers(check_state_based_actions(state))
check("CRYMAX: asks before the game is lost",
      asked.result == GameResult.IN_PROGRESS and waiting(asked) is not None)
check("...declining loses",
      pick(asked, "lose").result == GameResult.PLAYER_1_WINS)
paid = destroy_three(pick(asked, "replace"))
check("...and with the deck still empty and too few cards left, the game is then lost",
      paid.result == GameResult.PLAYER_1_WINS, f"{paid.result} {waiting(paid)}")

state, boss, _ = crymax_setup()
state.players[0].shield_zone = [s for s in state.players[0].shield_zone if s.is_face_up]
state.players[0].shield_zone.clear()
creature(state, 0, card("third"))
state.turn_info.active_player = 1
raider = creature(state, 1, card("raider"))
state.turn_info.phase = Phase.DIRECT_ATTACK
state.attack_context = AttackContext(raider.uid, 1, "player", None)
asked = act(state, ActionType.PASS)
check("CRYMAX: a direct attack can be replaced", waiting(asked) is not None
      and asked.result == GameResult.IN_PROGRESS)
saved = destroy_three(pick(asked, "replace"))
check("...and the game goes on", saved.result == GameResult.IN_PROGRESS
      and names(saved.players[0].battle_zone) == ["CRYMAX Jaouga"], str(saved.result))

# ── Gotsunma in and out of Hyper Mode ──────────────────────────────────────
state = make_state()
gotsunma = creature(state, 0, named("Gotsunma_=_Dhamma,_Soul_Abyss", power=3000))
check("Gotsunma: normally one shield", gotsunma.shields_broken_on_attack() == 1
      and gotsunma.compute_power(state) == 3000)
gotsunma.hyper_mode_released = True
check("Gotsunma: in Hyper Mode, 6000 power and two shields",
      gotsunma.shields_broken_on_attack() == 2 and gotsunma.compute_power(state) == 6000)

# ── Vejita and Bolmeteus ───────────────────────────────────────────────────
state = make_state()
vejita = creature(state, 0, named("Million_Vejita,_Tyrant_T"))
check("Vejita: Quattro breaker", vejita.shields_broken_on_attack() == 4)
bolme = creature(state, 0, named("Bolmeteus_Sapphire_Dragon", power=11000))
idle = bolme.compute_power(state)
state.attack_context = AttackContext(bolme.uid, 0, "player", None)
check("Bolmeteus: +3000 only while attacking",
      idle == 11000 and bolme.compute_power(state) == 14000)

# ── Sasagale ───────────────────────────────────────────────────────────────
state = make_state()
mariflo = creature(state, 0, named("Mariflo-1,_Viral", civs=(W,)))
hand(state, 0, card("dispector", cost=4, races=("Black Command Dispector",)),
     card("other", cost=4))
mana(state, 0, 4)
offers = [a for a in legal(state, ActionType.SUMMON_CREATURE) if a.get_extra().get("sasagale")]
check("Sasagale: only for a Dispector, costing 1 less",
      offers and all(len(a.mana_used) == 3 for a in offers)
      and {state.players[0].find_in_hand(a.card_uid).definition.name for a in offers}
      == {"dispector"})
run = execute_action(state, offers[0], validate=False)
check("...and the Sasagale creature is destroyed",
      names(run.players[0].battle_zone) == ["dispector"]
      and "Mariflo-1, Viral" in names(run.players[0].graveyard))

# ── Denjara Switch ─────────────────────────────────────────────────────────
from engine.zone_mover import draw_card


def shrine_setup():
    state = make_state()
    shrine = creature(state, 0, named("Memento_Guardian_Shrine,_Prison_Court_of_D",
                                      card_type=CardType.FIELD, subtype=CardSubtype.D2))
    creature(state, 1, card("theirs"))
    state.turn_info.active_player = 1
    return state, shrine


state, shrine = shrine_setup()
draw_card(state, 1)
asked = resolve_pending_triggers(state)
check("Denjara Switch: either player's first draw of their turn offers it",
      waiting(asked) is not None and waiting(asked).player == 0)
run = pick(asked, "yes")
turned = run.find_creature_anywhere(shrine.uid)[1]
check("...turning it over taps the opponent's creatures",
      turned.field_orientation == "upside_down"
      and all(c.is_tapped for c in run.players[1].battle_zone))
run.players[1].has_drawn_this_turn = False
draw_card(run, 1)
check("...only once a game", waiting(resolve_pending_triggers(run)) is None)
state, _ = shrine_setup()
state.players[1].has_drawn_this_turn = True
draw_card(state, 1)
draw_card(state, 0)  # not that player's turn
check("...not on later draws, or draws outside your turn",
      waiting(resolve_pending_triggers(state)) is None)

# ── Super Soul Cross ───────────────────────────────────────────────────────
from core.zones import EvolutionStackEntry


def leave_graveyard(state):
    state = check_state_based_actions(state)          # what is there now
    card_gone = state.players[0].graveyard.pop(0)
    state.players[0].hand.append(HandCard(definition=card_gone.definition, uid=card_gone.uid))
    return resolve_pending_triggers(check_state_based_actions(state))


for placed in ("itself", "underneath", "absent"):
    state = make_state()
    kawagutsu = named("Kawagutsu_=_Aguus")
    if placed == "itself":
        creature(state, 0, kawagutsu)
    elif placed == "underneath":
        top = creature(state, 0, card("top"))
        top.evolution_stack.append(EvolutionStackEntry(definition=kawagutsu))
    grave(state, 0, card("g1"), card("g2"))
    run = leave_graveyard(state)
    offered = waiting(run) is not None
    check(f"Super Soul Cross with Kawagutsu {placed}: offered={placed != 'absent'}",
          offered == (placed != "absent"))
    if offered:
        run = pick(run, "yes")
        check("...mills 3", len(run.players[0].graveyard) == 4
              and len(run.players[0].deck) == 11)
        check("...only the first time each turn", waiting(leave_graveyard(run)) is None)

# ── Silent Jealousy ────────────────────────────────────────────────────────
state = make_state()
KERNEL = "Kernel,_Blue_Stagnation_Dragon_Elemental_/_Silent_Jealousy"
(held,) = hand(state, 0, named(KERNEL, cost=5, civs=(L, W)))
hand(state, 1, card("their-card"))
state.players[0].shield_zone = [ShieldCard(definition=card(
    "my-shield", card_type=CardType.SPELL, keywords=(Keyword.SHIELD_TRIGGER,)))]
mana(state, 0, 2)
sides = [a for a in legal(state, ActionType.CAST_SPELL) if a.get_extra().get("twinpact_spell")]
run = finish(execute_action(state, sides[0], validate=False))
check("Silent Jealousy: they discard, and you take a shield without its trigger",
      not run.players[1].hand and not run.players[0].shield_zone
      and names(run.players[0].hand) == ["my-shield"]
      and run.effect_stack.shield_break_window is None
      and any(g.uid == held.uid for g in run.players[0].graveyard))

# ── Spell Del Fin ──────────────────────────────────────────────────────────
from core.observation import Observation

for present in (False, True):
    state = make_state()
    if present:
        creature(state, 0, named("Spell_Del_Fin,_Light_Divine_Dragon"))
    hand(state, 0, card("mine"))
    hand(state, 1, card("secret"))
    seen = Observation.build(state, 0).opponent_state.hand_cards[0].name
    back = Observation.build(state, 1).opponent_state.hand_cards[0].name
    check(f"Spell Del Fin present={present}: you see the opponent's hand={present}",
          (seen == "secret") == present and back is None)

print(f"\n{len(FAILURES)} failed")
sys.exit(1 if FAILURES else 0)
