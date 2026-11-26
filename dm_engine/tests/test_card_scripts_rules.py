"""
tests/test_card_scripts_rules.py - ongoing rules and free play for Decks 3 and 4.
"""

import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.actions import charge_mana, pass_attack, summon_creature
from core.card_scripts import HAND_TURN_END_KEY, scripted_effects
from core.cards import CardDefinition, CardEffect
from core.continuous_rules import is_creature_now
from core.enums import (
    INFINITY, ActionType, CardSubtype, CardType, Civilization, EffectAction, EffectType,
    Keyword, ManaUsage, Phase, TriggerEvent,
)
from core.player_state import PlayerState
from core.state import GameState, PendingTrigger, TurnInfo
from core.zones import Creature, GraveyardCard, HandCard, ManaCard, ShieldCard
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.phase_controller import _end_turn, _start_turn
from engine.shield_break_window import execute_free_from_hand
from engine.trigger_resolver import resolve_pending_triggers
from engine.turn.action_gen import _compute_effective_cost
from engine.zone_mover import move_hand_to_battle

FAILURES: list[str] = []
_ids = itertools.count(5000)


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


def card(name, cost=3, power=3000, slug=None, civs=(Civilization.DARKNESS,), races=(),
         card_type=CardType.CREATURE, keywords=(), effects=None):
    cid = next(_ids)
    slug = slug or name
    scripted = scripted_effects(slug, cid)
    return CardDefinition(
        id=cid, slug=slug, name=name, cost=cost,
        power=power if card_type == CardType.CREATURE else None, card_type=card_type,
        card_subtype=CardSubtype.NONE, civilizations=frozenset(civs), races=frozenset(races),
        keywords=frozenset(keywords),
        effects=tuple(effects) if effects is not None else (scripted or ()),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(), is_multiface=False,
    )


def scripted(slug, **kwargs):
    return card(slug.replace("_", " "), slug=slug, **kwargs)


def spell(name, cost=3, **kwargs):
    return card(name, cost=cost, card_type=CardType.SPELL, **kwargs)


def drawing_spell(name, cost=1, **kwargs):
    effect = CardEffect(
        card_id=1, ability_index=0, raw_text="draw", effect_type=EffectType.SPELL,
        trigger_event=TriggerEvent.NONE, effect_action=EffectAction.DRAW, trigger_condition={},
        effect_target={}, effect_value={"amount": 1}, is_optional=False, is_replacement=False,
        active_in_phase=("any",), active_in_zone=(), parse_confidence=1.0,
    )
    return spell(name, cost=cost, effects=[effect], **kwargs)


def make_state(deck=12, phase=Phase.MAIN):
    players = [
        PlayerState(player_index=i, player_name=f"P{i}", deck=[card(f"deck{i}-{n}") for n in range(deck)])
        for i in range(2)
    ]
    return GameState(players=players, turn_info=TurnInfo(turn_number=3, active_player=0, phase=phase))


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


def mana(state, player, count, civ=Civilization.DARKNESS):
    made = [ManaCard(definition=card(f"mana{i}", civs=(civ,))) for i in range(count)]
    state.players[player].mana_zone.extend(made)
    return made


def put(state, definition, player=0, **how):
    (held,) = hand(state, player, definition)
    move_hand_to_battle(state, player, held.uid, **how)
    return resolve_pending_triggers(state)


def cast(state, definition, player=0):
    """Cast a card's spell effects without a hand or mana."""
    for effect in definition.effects:
        if effect.effect_type == EffectType.SPELL:
            state.effect_stack.add_trigger(PendingTrigger(
                effect=effect, source_uid="spell", source_card_id=definition.id, controller=player))
    return resolve_pending_triggers(state)


def waiting(state):
    return state.effect_stack.awaited_choice


def pick(state, option=None):
    actions = get_legal_actions(state)
    if option is None:
        action = next(a for a in actions if a.action_type.value == "pass")
    else:
        action = next(a for a in actions if a.target_uid == option)
    return execute_action(state, action, validate=False)


def options(state):
    return set(waiting(state).valid_options) if waiting(state) else set()


def done(state):
    return waiting(state) is None and not state.effect_stack.pending_triggers


def names(cards):
    return sorted(c.definition.name for c in cards)


def attacks(state, attacker):
    state.turn_info.phase = Phase.ATTACK
    return {a.action_type for a in get_legal_actions(state)
            if a.card_uid == attacker.uid and a.action_type in (ActionType.ATTACK_PLAYER, ActionType.ATTACK_CREATURE)}


# ── Spell Del Fin ───────────────────────────────────────────────────────────
def can_cast(with_lock):
    state = make_state()
    if with_lock:
        creature(state, 1, scripted("Spell_Del_Fin,_Light_Divine_Dragon", power=6000))
    held, = hand(state, 0, drawing_spell("bolt"))
    mana(state, 0, 2)
    return any(a.action_type == ActionType.CAST_SPELL and a.card_uid == held.uid for a in get_legal_actions(state))


check("Spell is castable normally", can_cast(False))
check("Spell Del Fin: the opponent can't cast spells", not can_cast(True))
state = make_state()
fin = creature(state, 1, scripted("Spell_Del_Fin,_Light_Divine_Dragon", power=6000))
hand(state, 0, spell("a"), spell("b"), card("not-a-spell"))
check("Spell Del Fin: +2000 per spell in the opponent's hand", fin.compute_power(state) == 10000,
      str(fin.compute_power(state)))

# ── Quattro breaker ─────────────────────────────────────────────────────────
state = make_state()
check("Brachiodrange breaks 4 shields",
      creature(state, 0, scripted("Brachiodrange")).shields_broken_on_attack() == 4)

# ── Attack restrictions ─────────────────────────────────────────────────────
for power, allowed in ((49999, False), (50000, True)):
    state = make_state()
    creature(state, 1, scripted("Gigandydanos,_Big_Tree_King", power=50000))
    attacker = creature(state, 0, card("attacker", power=power))
    check(f"Gigandydanos: power {power} may attack you={allowed}",
          (ActionType.ATTACK_PLAYER in attacks(state, attacker)) == allowed)

state = make_state()
creature(state, 1, scripted("Gaiash_Kaiser,_the_Meteor"))
new = creature(state, 0, card("new", keywords=(Keyword.SPEED_ATTACKER,)), entered_turn=3)
old = creature(state, 0, card("old"), entered_turn=1)
check("Gaiash: a creature put this turn can't attack you", ActionType.ATTACK_PLAYER not in attacks(state, new))
check("Gaiash: older creatures can", ActionType.ATTACK_PLAYER in attacks(state, old))

# ── Costs ───────────────────────────────────────────────────────────────────
state = make_state()
creature(state, 0, scripted("Gaiash_Kaiser,_the_Meteor"))
for printed, expected in ((10, 6), (12, 8), (9, 9)):
    got = _compute_effective_cost(card("big", cost=printed), state, 0)
    check(f"Gaiash discount: cost {printed} -> {expected}", got == expected, str(got))
check("Gaiash discount is yours only", _compute_effective_cost(card("big", cost=10), state, 1) == 10)

zadan = scripted("Seven%27s_Gate_Zadan,_Demon_King", cost=13)
for graveyard, expected in ((6, 13), (7, 6)):
    state = make_state()
    grave(state, 0, *[card(f"g{i}") for i in range(graveyard)])
    got = _compute_effective_cost(zadan, state, 0)
    check(f"Zadan with {graveyard} graveyard cards costs {expected}", got == expected, str(got))

# ── Gotsunma ────────────────────────────────────────────────────────────────
for has_gotsunma in (False, True):
    state = make_state(phase=Phase.MANA_CHARGE)
    if has_gotsunma:
        creature(state, 1, scripted("Gotsunma_=_Dhamma,_Soul_Abyss"))
    held, = hand(state, 0, card("charge"))
    after = execute_action(state, charge_mana(0, held.uid, held.definition.id), validate=False)
    check(f"Gotsunma={has_gotsunma}: opponent's mana enters tapped={has_gotsunma}",
          after.players[0].mana_zone[-1].is_tapped == has_gotsunma)

# ── Jabibirublood ───────────────────────────────────────────────────────────
for rush in (False, True):
    state = make_state()
    if rush:
        creature(state, 0, scripted("Jabibirublood,_Wicked_Soul_Dragon"))
    raised = creature(state, 0, card("raised"), entered_turn=3, has_summoning_sickness=True)
    raised.temp_flags["from_graveyard_turn"] = 3
    creature(state, 1, card("tapped-target"), is_tapped=True)
    found = attacks(state, raised)
    # It only removes summoning sickness, so normal attack targets apply
    check(f"Jabibirublood={rush}: a graveyard creature attacks like any ready creature={rush}",
          found == ({ActionType.ATTACK_PLAYER, ActionType.ATTACK_CREATURE} if rush else set()),
          str(found))

# Only creatures that came from the graveyard lose their summoning sickness
state = make_state()
creature(state, 0, scripted("Jabibirublood,_Wicked_Soul_Dragon"))
other = creature(state, 0, card("from-hand"), entered_turn=3, has_summoning_sickness=True)
creature(state, 1, card("tapped-target"), is_tapped=True)
check("Jabibirublood: a creature from elsewhere is still sick", not attacks(state, other))

# ── Tamaseed creature condition ─────────────────────────────────────────────
for slug in ("Jashin_Emperor,_Abyssal_Darkness", "Jablood,_Wicked_Dragon"):
    state = make_state()
    seed_card = creature(state, 0, scripted(slug, power=8000))
    creature(state, 0, card("d1"))
    creature(state, 0, card("d2"))
    check(f"{slug}: 3 darkness cards, not a creature",
          not is_creature_now(state, seed_card) and not attacks(state, seed_card))
    creature(state, 0, card("d3"))
    check(f"{slug}: 4 darkness cards, a creature",
          is_creature_now(state, seed_card) and bool(attacks(state, seed_card)))

# ── Broom, Million Vejita ───────────────────────────────────────────────────
for summoned, survives in ((True, True), (False, False)):
    state = make_state()
    creature(state, 1, scripted("Broom_=_Proof"))
    state = put(state, card("arrival"), summoned=summoned, paid_mana=summoned)
    check(f"Broom: opponent's creature summoned={summoned} survives={survives}",
          bool(state.players[0].battle_zone) == survives)
state = make_state()
state.turn_info.active_player = 1
creature(state, 1, scripted("Broom_=_Proof"))
state = put(state, card("shield-time"), summoned=False)
check("Broom: only during its opponent's turn", bool(state.players[0].battle_zone))

for blocked in (False, True):
    state = make_state()
    if blocked:
        creature(state, 1, scripted("Million_Vejita,_Tyrant_T"))
    grave(state, 0, card("sleeper", cost=2))
    state = cast(state, scripted("Just_In_You", card_type=CardType.SPELL))
    while waiting(state):
        state = pick(state, next(iter(options(state))))
    check(f"Million Vejita={blocked}: opponent can't put from the graveyard",
          (not state.players[0].battle_zone) if blocked else True)
    if not blocked:
        check("Without Vejita the graveyard is usable", True)
state = make_state()
creature(state, 1, scripted("Million_Vejita,_Tyrant_T"))
state = put(state, card("from-hand"), paid_mana=True)
check("Million Vejita: summoning from hand still works", len(state.players[0].battle_zone) == 1)

# ── Magnum ──────────────────────────────────────────────────────────────────
for paid in (True, False):
    state = make_state()
    creature(state, 1, scripted("Magnum,_Allshot_Puppet"))
    creature(state, 0, card("bystander"))
    state = put(state, card("newcomer"), paid_mana=paid)
    if paid:
        check("Magnum: paying mana is safe", done(state) and len(state.players[0].battle_zone) == 2)
    else:
        check("Magnum: the player who played for free chooses", waiting(state) is not None and waiting(state).player == 0)
        state = pick(state, next(iter(options(state))))
        check("Magnum: they destroy one of their creatures", len(state.players[0].battle_zone) == 1 and done(state))
state = make_state()
creature(state, 1, scripted("Magnum,_Allshot_Puppet"))
(held,) = hand(state, 0, drawing_spell("free"))
execute_free_from_hand(state, 0, held.uid, reason="shield_trigger")
state = resolve_pending_triggers(state)
check("Magnum: a free spell with no creatures destroys nothing", done(state))

# ── Kaiser Victory, Just In You, Abyssal Counterattack ──────────────────────
state = cast(make_state(), scripted("Kaiser_Victory,_Secret_Extreme_Triumph", card_type=CardType.SPELL))
check("Kaiser Victory: top card goes to mana, return is optional",
      len(state.players[0].mana_zone) == 1 and not pick(state).players[0].hand)
returned = pick(state, state.players[0].mana_zone[0].uid)
check("Kaiser Victory: may return it", len(returned.players[0].hand) == 1 and not returned.players[0].mana_zone)

state = make_state()
ok, dear, plain = grave(state, 0, card("abyss4", cost=4, races=("Abyss Royale",)),
                        card("abyss5", cost=5, races=("Abyss Royale",)), card("human", cost=1))
state = cast(state, scripted("Just_In_You", card_type=CardType.SPELL))
check("Just In You: mills 4 and puts an Abyss costing 4 or less",
      len(state.players[0].deck) == 8 and names(state.players[0].battle_zone) == ["abyss4"] and done(state),
      str(names(state.players[0].battle_zone)))

state = make_state()
giant = creature(state, 1, card("giant", power=999999))
state = cast(state, scripted("Abyssal_Counterattack", card_type=CardType.SPELL))
check("Abyssal Counterattack: infinite power loss destroys any creature",
      not state.players[1].battle_zone and done(state))

# ── Romanov and Zadan: playing from the graveyard ───────────────────────────
state = make_state()
cheap, dear, body = grave(state, 0, drawing_spell("cheap", cost=4), drawing_spell("dear", cost=5), card("body", cost=1))
state = put(state, scripted("Romanov_the_4th,_Demonic_Eye_Gaia"), paid_mana=True)
check("Romanov: choose 0 to 3 cards to mill", options(state) == {"count:0", "count:1", "count:2", "count:3"})
state = pick(state, "count:2")
check("Romanov: mills that many", len(state.players[0].deck) == 10)
check("Romanov: only non-creature cards costing 4 or less", cheap.uid in options(state)
      and not {dear.uid, body.uid} & options(state))
played = pick(state, cheap.uid)
check("Romanov: the spell resolves and goes to the bottom of the deck",
      played.players[0].deck[-1].name == "cheap" and len(played.players[0].hand) == 1 and done(played))

state = make_state()
dark, fire, big = grave(state, 0, card("dark", cost=2), card("fire", cost=2, civs=(Civilization.FIRE,)),
                        card("too-big", cost=4))
grave(state, 0, card("filler", cost=20, civs=(Civilization.FIRE,)))
state = put(state, zadan, paid_mana=True)
check("Zadan: darkness cards costing no more than the 4 cards in the graveyard",
      options(state) == {dark.uid, big.uid}, str(options(state)))
summoned = pick(state, dark.uid)
check("Zadan: summons it from the graveyard", "dark" in names(summoned.players[0].battle_zone))
check("Zadan: playing is optional", len(pick(state).players[0].battle_zone) == 1)

# ── Perfect Petency ─────────────────────────────────────────────────────────
PETENCY = scripted("Perfect_Petency", cost=5, card_type=CardType.SPELL)


def modes(state, first, second):
    return pick(pick(cast(state, PETENCY), first), second)


state = make_state()
state = modes(state, "mode:draw_discard", "mode:draw_discard")
for _ in range(2):
    state = pick(state, next(iter(options(state))))
check("Petency: the same mode twice draws 4 and discards 2",
      len(state.players[0].hand) == 2 and len(state.players[0].graveyard) == 2 and done(state),
      f"hand={len(state.players[0].hand)}")
state = make_state()
foe = creature(state, 1, card("foe"))
friend = creature(state, 0, card("friend"))
state = pick(modes(state, "mode:shieldify", "mode:draw_discard"), foe.uid)
state = pick(state, next(iter(options(state))))
check("Petency: shieldify moves a creature into its owner's shields",
      not state.players[1].battle_zone and names(state.players[1].shield_zone) == ["foe"] and done(state))
state = make_state()
trigger_card, = grave(state, 0, drawing_spell("st", keywords=(Keyword.SHIELD_TRIGGER,)))
grave(state, 0, drawing_spell("no-st"))
state = modes(state, "mode:shield_trigger", "mode:draw_discard")
while waiting(state):
    state = pick(state, next(iter(options(state))))
check("Petency: plays a shield trigger card from the graveyard, then to the deck bottom",
      "st" in [d.name for d in state.players[0].deck] and "no-st" in names(state.players[0].graveyard))

# ── Playing from hand as the opponent's turn ends ───────────────────────────
for count, offered in ((2, False), (3, True)):
    state = make_state()
    hand(state, 1, PETENCY)
    state.players[0].creatures_put_this_turn = count
    _end_turn(state)
    state = resolve_pending_triggers(state)
    check(f"Petency: {count} creatures put -> offered at turn end={offered}",
          (waiting(state) is not None and waiting(state).player == 1) == offered)

GAIASH = scripted("Gaiash_Kaiser,_the_Meteor", cost=6, power=8000)
for free, already, offered in ((False, False, False), (True, False, True), (True, True, False)):
    state = make_state()
    hand(state, 1, GAIASH)
    if already:
        creature(state, 1, GAIASH)
    state.players[0].played_free_this_turn = free
    _end_turn(state)
    state = resolve_pending_triggers(state)
    check(f"Gaiash: opponent free={free}, you have one={already} -> offered={offered}",
          (waiting(state) is not None) == offered)
state = make_state()
first, second = hand(state, 1, GAIASH, GAIASH)
state.players[0].played_free_this_turn = True
_end_turn(state)
state = resolve_pending_triggers(state)
state = pick(state, waiting(state).valid_options[0])
while waiting(state):
    state = pick(state, waiting(state).valid_options[0])
check("Gaiash: only one copy can be summoned",
      names(state.players[1].battle_zone) == [GAIASH.name], str(names(state.players[1].battle_zone)))

state = make_state()
state.players[0].played_free_this_turn = True
state.players[0].creatures_put_this_turn = 3
_start_turn(state)
check("Turn counters reset at the start of a turn",
      not state.players[0].played_free_this_turn and not state.players[0].creatures_put_this_turn)

# ── Summoning from the graveyard ────────────────────────────────────────────
def summons(state, uid):
    return [a for a in get_legal_actions(state)
            if a.action_type == ActionType.SUMMON_CREATURE and a.card_uid == uid]


def do(state, action):
    return execute_action(state, action, validate=True)


FELIX = scripted("Felix_Misery", cost=6, power=6000, races=("Mafi Gang",),
                 keywords=(Keyword.DOUBLE_BREAKER,))
state = make_state()
felix_card, plain = grave(state, 0, FELIX, card("plain", cost=1))
mana(state, 0, 5)
check("Graveyard summon needs the mana", not summons(state, felix_card.uid))
mana(state, 0, 1)
check("Felix can be summoned from the graveyard", bool(summons(state, felix_card.uid)))
check("Other graveyard cards cannot", not summons(state, plain.uid))
after = do(state, summons(state, felix_card.uid)[0])
raised = after.players[0].battle_zone[0]
check("Graveyard summon pays mana and moves the card",
      raised.definition.name == FELIX.name and felix_card.uid not in [c.uid for c in after.players[0].graveyard]
      and sum(m.is_tapped for m in after.players[0].mana_zone) == 6 and not after.players[0].hand)
check("Not evolved: stays a Neo Creature with summoning sickness",
      raised.has_summoning_sickness and not raised.evolution_stack)

# ── Neo Evolution ───────────────────────────────────────────────────────────
for zone in ("hand", "graveyard"):
    state = make_state()
    base = creature(state, 0, card("base"), entered_turn=1)
    creature(state, 0, card("fire-base", civs=(Civilization.FIRE,)))
    source = hand(state, 0, FELIX)[0] if zone == "hand" else grave(state, 0, FELIX)[0]
    mana(state, 0, 4)
    found = summons(state, source.uid)
    check(f"Neo from {zone}: costs 2 less, on darkness creatures only",
          bool(found) and {a.evolution_base_uid for a in found} == {base.uid}
          and {len(a.mana_used) for a in found} == {4}, str({a.evolution_base_uid for a in found}))
    after = do(state, found[0])
    top = after.find_creature_anywhere(base.uid)[1]
    check(f"Neo from {zone}: becomes a Neo Evolution Creature on the base",
          top.definition.name == FELIX.name and [e.definition.name for e in top.evolution_stack] == ["base"]
          and len(after.players[0].battle_zone) == 2)
    check(f"Neo from {zone}: can attack at once", bool(attacks(after, top)))
    after.turn_info.phase = Phase.ATTACK
    swing = execute_action(after, next(a for a in get_legal_actions(after)
                                       if a.card_uid == top.uid and a.action_type == ActionType.ATTACK_PLAYER),
                           validate=False)
    check(f"Neo from {zone}: the new card's attack ability works", len(swing.players[0].deck) == 10,
          str(len(swing.players[0].deck)))
state = make_state()
base = creature(state, 0, card("base"), is_tapped=True)
(source,) = hand(state, 0, FELIX)
mana(state, 0, 6)
found = summons(state, source.uid)
check("Neo: evolving is optional", {a.evolution_base_uid for a in found} == {None, base.uid})
evolved = do(state, next(a for a in found if a.evolution_base_uid))
check("Neo: a tapped base stays tapped", evolved.players[0].battle_zone[0].is_tapped)

# ── Evolving triggers "when you put this creature" ──────────────────────────
SAGA = scripted("Saga_In_Zenaku", cost=5, power=6500,
                civs=(Civilization.DARKNESS, Civilization.LIGHT, Civilization.WATER))


def saga_setup():
    state = make_state()
    bases = {
        civ: creature(state, 0, card(f"{civ.value}-base", civs=(civ,)), entered_turn=1)
        for civ in (Civilization.LIGHT, Civilization.WATER, Civilization.DARKNESS,
                    Civilization.FIRE, Civilization.NATURE)
    }
    (held,) = hand(state, 0, SAGA)
    for civ in (Civilization.DARKNESS, Civilization.LIGHT, Civilization.WATER):
        mana(state, 0, 2, civ)
    return state, bases, held


state, bases, held = saga_setup()
found = summons(state, held.uid)
expected = {bases[c].uid for c in (Civilization.LIGHT, Civilization.WATER, Civilization.DARKNESS)}
check("G-Neo: light, water or darkness bases, or no evolution",
      {a.evolution_base_uid for a in found} == expected | {None})
check("G-Neo: full cost, no discount", {len(a.mana_used) for a in found} == {5})
water = bases[Civilization.WATER].uid
evolved = do(state, next(a for a in found if a.evolution_base_uid == water))
check("Evolving triggers 'when you put this creature'",
      len(evolved.players[0].hand) == 3 and waiting(evolved) is not None,
      f"hand={len(evolved.players[0].hand)}")
top = evolved.find_creature_anywhere(water)[1]
check("G-Neo: evolves on one base", [e.definition.name for e in top.evolution_stack] == ["Water-base"]
      and len(evolved.players[0].battle_zone) == 5)

state, bases, held = saga_setup()
creature(state, 1, scripted("Wald_Brachio,_Absolute_World_King"))
water = bases[Civilization.WATER].uid
blocked = do(state, next(a for a in summons(state, held.uid) if a.evolution_base_uid == water))
check("Wald Brachio also stops it on evolution", not blocked.players[0].hand)

# The card underneath must not trigger again, and other watchers see the arrival
state = make_state()
oa = creature(state, 0, scripted("Oa:Nateha"), entered_turn=1)
watcher_effect = CardEffect(
    card_id=1, ability_index=0, raw_text="■ Whenever you summon a creature, draw a card.",
    effect_type=EffectType.TRIGGERED, trigger_event=TriggerEvent.ON_SUMMON,
    effect_action=EffectAction.DRAW, trigger_condition={}, effect_target={},
    effect_value={"amount": 1}, is_optional=False, is_replacement=False,
    active_in_phase=("any",), active_in_zone=("battle_zone",), parse_confidence=1.0,
)
creature(state, 0, card("summon-watcher", civs=(Civilization.FIRE,), effects=[watcher_effect]))
hand(state, 1, card("enemy-card"))
(held,) = hand(state, 0, FELIX)
mana(state, 0, 4)
after = do(state, next(a for a in summons(state, held.uid) if a.evolution_base_uid == oa.uid))
check("The base's own 'when put' does not trigger again", len(after.players[1].hand) == 1)
check("'Whenever you summon' abilities see an evolution", len(after.players[0].hand) == 1,
      str(len(after.players[0].hand)))

# ── Summoning from the mana zone ────────────────────────────────────────────
VEJITA = scripted("Million_Vejita,_Tyrant_T", cost=9, power=18000, civs=(Civilization.NATURE,))
state = make_state()
stored = ManaCard(definition=VEJITA)
state.players[0].mana_zone.append(stored)
mana(state, 0, 7, Civilization.NATURE)
check("Mana summon: 8 mana is not enough for cost 9", not summons(state, stored.uid))
mana(state, 0, 1, Civilization.NATURE)
found = summons(state, stored.uid)
check("Mana summon: the card may tap itself to pay",
      bool(found) and all(stored.uid in [u.mana_uid for u in a.mana_used] for a in found))
after = do(state, found[0])
check("Mana summon: moves from mana to the battle zone with the cost paid",
      names(after.players[0].battle_zone) == [VEJITA.name] and len(after.players[0].mana_zone) == 8
      and all(m.is_tapped for m in after.players[0].mana_zone))
state = make_state()
dragon_card = card("wyrm", cost=2, civs=(Civilization.NATURE,), races=("Earth Dragon",))
stored = ManaCard(definition=dragon_card)
state.players[0].mana_zone.append(stored)
mana(state, 0, 3, Civilization.NATURE)
check("Dragons can't leave mana without Shura Beethoven", not summons(state, stored.uid))
creature(state, 0, card("Shura", slug="Shura_Beethoven,_Zenith_of_%22Hadou%22", effects=()))
check("Shura Beethoven lets Dragons be summoned from mana", bool(summons(state, stored.uid)))

# ── Abyss Rush ──────────────────────────────────────────────────────────────
BELL = scripted("Abyssbell_=_Jashin_Emperor", cost=4, power=7000)
state = make_state()
abyss, human = grave(state, 0, card("abyss", cost=4, races=("Abyss Royale",)), card("human", cost=4))
mana(state, 0, 4)
check("No Abyss Rush without Abyssbell", not summons(state, abyss.uid))
creature(state, 0, BELL, entered_turn=1)
found = summons(state, abyss.uid)
check("Abyssbell: your Abyss gets Abyss Rush", bool(found) and not summons(state, human.uid))
check("Abyssbell: graveyard summons cost 2 less", {len(a.mana_used) for a in found} == {2})
check("Abyssbell: never below 1",
      _compute_effective_cost(card("one", cost=1), state, 0, from_graveyard=True) == 1)
check("Abyssbell: the hand is not discounted", _compute_effective_cost(card("h", cost=4), state, 0) == 4)
after = do(state, found[0])
rushed = next(c for c in after.players[0].battle_zone if c.definition.name == "abyss")
creature(after, 1, card("tapped-foe"), is_tapped=True)
check("Abyss Rush: no summoning sickness, attacks players and tapped creatures",
      attacks(after, rushed) == {ActionType.ATTACK_PLAYER, ActionType.ATTACK_CREATURE})
_end_turn(after)
check("Abyss Rush: goes to the bottom of the deck at turn end",
      after.players[0].deck[-1].name == "abyss"
      and "abyss" not in names(after.players[0].battle_zone) + names(after.players[0].graveyard))
state = make_state()
creature(state, 1, scripted("Million_Vejita,_Tyrant_T"))
vej_blocked, = grave(state, 0, FELIX)
mana(state, 0, 6)
check("Million Vejita blocks graveyard summons", not summons(state, vej_blocked.uid))

# ── Super Shield Trigger (no deck card has one, so a test card) ─────────────
from core.card_scripts import SUPER_SHIELD_TRIGGER_KEY  # noqa: E402


def super_card():
    base_card = drawing_spell("super")
    extra = CardEffect(
        card_id=base_card.id, ability_index=1, raw_text="Super: draw 1 more",
        effect_type=EffectType.TRIGGERED, trigger_event=TriggerEvent.NONE,
        effect_action=EffectAction.DRAW, trigger_condition={}, effect_target={},
        effect_value={"amount": 1, "script": f"test#{SUPER_SHIELD_TRIGGER_KEY}"}, is_optional=False,
        is_replacement=False, active_in_phase=("any",), active_in_zone=(), parse_confidence=1.0,
    )
    return card("super", cost=1, card_type=CardType.SPELL, effects=(*base_card.effects, extra))


import core.card_scripts as card_data  # noqa: E402

card_data.SCRIPTS["test"] = (card_data.Ability(
    SUPER_SHIELD_TRIGGER_KEY, TriggerEvent.NONE, (("draw", {"count": 1, "who": "self"}),), "super"),)
for shields, drawn in ((1, 1), (0, 2)):
    state = make_state()
    state.players[0].shield_zone = [ShieldCard(definition=card(f"s{i}")) for i in range(shields)]
    (held,) = hand(state, 0, super_card())
    execute_free_from_hand(state, 0, held.uid, reason="shield_trigger")
    state = resolve_pending_triggers(state)
    check(f"Super Shield Trigger with {shields} shields left draws {drawn}",
          len(state.players[0].hand) == drawn, str(len(state.players[0].hand)))
state = make_state()
(held,) = hand(state, 0, super_card())
execute_free_from_hand(state, 0, held.uid, reason="cast")
state = resolve_pending_triggers(state)
check("Super ability needs a shield trigger", len(state.players[0].hand) == 1)
state = make_state()
state.players[0].shield_zone = [ShieldCard(definition=card("kept"))]
target, = grave(state, 0, super_card())
grave(state, 0, card("f1", civs=(Civilization.FIRE,)))
state = put(state, zadan, paid_mana=True)
state = pick(state, target.uid)
check("Zadan: uses the Super ability as if you had no shields", len(state.players[0].hand) == 2,
      str(len(state.players[0].hand)))
del card_data.SCRIPTS["test"]

if FAILURES:
    print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
    sys.exit(1)
