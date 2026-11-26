"""
tests/test_rulebook_flow.py - turn, attack, effect and evolution rules checked
against the comprehensive rulebook (rule numbers in each section).
"""

import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.card_scripts import scripted_effects
from core.cards import CardDefinition, CardEffect
from core.continuous_rules import is_creature_now
from core.enums import (
    ActionType, CardSubtype, CardType, Civilization, EffectAction, EffectType, GameResult,
    Keyword, Phase, TriggerEvent,
)
from core.player_state import PlayerState
from core.state import GameState, PendingTrigger, TurnInfo
from core.zones import Creature, HandCard, ManaCard, PowerModifier, ShieldCard
from db.card_database import _parse_type_and_subtype
from engine.action_executor import execute_action
from engine.action_generator import get_legal_actions
from engine.entry_rules import may_enter
from engine.shield_break_window import execute_free_from_hand
from engine.sba_checker import check_state_based_actions
from engine.trigger_resolver import resolve_pending_triggers
from engine.zone_mover import (
    draw_card, move_battle_to_graveyard, move_battle_to_zone, move_hand_to_battle,
    move_shield_to_standby,
)

FAILURES: list[str] = []
_ids = itertools.count(7000)


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


def card(name, cost=3, power=3000, slug=None, civs=(Civilization.DARKNESS,), races=(),
         card_type=CardType.CREATURE, subtype=CardSubtype.NONE, keywords=(), effects=None):
    cid = next(_ids)
    slug = slug or name
    return CardDefinition(
        id=cid, slug=slug, name=name, cost=cost,
        power=power if card_type == CardType.CREATURE else None, card_type=card_type,
        card_subtype=subtype, civilizations=frozenset(civs), races=frozenset(races),
        keywords=frozenset(keywords),
        effects=tuple(effects) if effects is not None else (scripted_effects(slug, cid) or ()),
        evolution_source_races=frozenset(), evolution_source_types=frozenset(), is_multiface=False,
    )


def scripted(slug, **kwargs):
    return card(slug.replace("_", " "), slug=slug, **kwargs)


def ability(action, event=TriggerEvent.NONE, kind=EffectType.TRIGGERED, value=None,
            text="", index=0):
    return CardEffect(
        card_id=index + 1, ability_index=index, raw_text=text, effect_type=kind,
        trigger_event=event, effect_action=action, trigger_condition={}, effect_target={},
        effect_value=value or {"amount": 1}, is_optional=False, is_replacement=False,
        active_in_phase=("any",), active_in_zone=("battle_zone",), parse_confidence=1.0,
    )


def draw_spell(name):
    return card(name, cost=1, card_type=CardType.SPELL, effects=[
        ability(EffectAction.DRAW, kind=EffectType.SPELL, text="Draw a card.")])


def make_state(phase=Phase.MAIN, deck=12):
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


def mana(state, player, count, civ=Civilization.DARKNESS):
    state.players[player].mana_zone.extend(
        ManaCard(definition=card(f"mana{i}", civs=(civ,))) for i in range(count))


def shields(state, player, *definitions):
    made = [ShieldCard(definition=d) for d in definitions]
    state.players[player].shield_zone.extend(made)
    return made


def waiting(state):
    return state.effect_stack.awaited_choice


def act(state, kind, **match):
    action = next(
        a for a in get_legal_actions(state)
        if a.action_type == kind and all(getattr(a, k) == v for k, v in match.items())
    )
    return execute_action(state, action, validate=False)


def pick(state, option=None):
    if option is None:
        return act(state, ActionType.PASS)
    return act(state, ActionType.SELECT_TARGET, target_uid=option)


def step(state):
    return act(state, ActionType.PASS)


def names(zone):
    return [c.definition.name for c in zone]


def cast_from_hand(state, definition, player=0):
    (held,) = hand(state, player, definition)
    execute_free_from_hand(state, player, held.uid, reason="cast")
    return resolve_pending_triggers(state)


# ── Card types stored as one string in the database (204.2, 204.3) ──────────
for raw, expected in (
    ("G-Neo Creature", (CardType.CREATURE, CardSubtype.G_NEO)),
    ("Neo Creature", (CardType.CREATURE, CardSubtype.NEO)),
    ("Star Max Evolution Creature", (CardType.CREATURE, CardSubtype.STAR_MAX)),
    ("D2 Field", (CardType.FIELD, CardSubtype.D2)),
    ("Galaxy Castle", (CardType.CASTLE, CardSubtype.G_CASTLE)),
    ("Tamaseed", (CardType.TAMASEED, CardSubtype.NONE)),
    ("Spell", (CardType.SPELL, CardSubtype.NONE)),
):
    got = _parse_type_and_subtype(raw, None)
    check(f"204: '{raw}' loads with its real type", got == expected, str(got))

state = make_state()
field = creature(state, 0, card("a-field", card_type=CardType.FIELD))
check("505.3: a field is not a creature", not is_creature_now(state, field))

# ── S-MAX (815.1a) and Dream elements (817.2) ───────────────────────────────
state = make_state()
for n in range(2):
    creature(state, 0, card(f"smax{n}", subtype=CardSubtype.STAR_MAX))
    creature(state, 0, card(f"star{n}", subtype=CardSubtype.EVOLUTION))
after = check_state_based_actions(state)
check("815.1a: the second S-MAX returns to hand",
      names(after.players[0].hand) == ["smax1"] and len(after.players[0].battle_zone) == 3
      and not after.players[0].graveyard)

state = make_state()
(held,) = hand(state, 0, card("solo-smax", subtype=CardSubtype.STAR_MAX))
check("801.3: an S-MAX without a source can attack at once",
      not move_hand_to_battle(state, 0, held.uid).has_summoning_sickness)

dream = card("dreamer", subtype=CardSubtype.DREAM)
state = make_state()
creature(state, 0, dream)
check("817.2: no second Dream element of the same name",
      not may_enter(state, 0, from_hand=True, played=True, definition=dream))

# ── G Castle (822) ──────────────────────────────────────────────────────────
CASTLE = card("g-castle", cost=1, card_type=CardType.CASTLE, subtype=CardSubtype.G_CASTLE)
state = make_state()
hand(state, 0, CASTLE)
mana(state, 0, 1)
used = act(state, ActionType.FORTIFY_CASTLE)
check("822.2a: a G Castle becomes a face-up shield",
      names(used.players[0].shield_zone) == ["g-castle"] and used.players[0].shield_zone[0].is_face_up)

state = make_state(phase=Phase.DIRECT_ATTACK)
attacker = creature(state, 0, card("breaker"))
shields(state, 1, CASTLE)
from core.state import AttackContext
state.attack_context = AttackContext(attacker.uid, 0, "player", None)
broken = act(state, ActionType.SELECT_ATTACK_ORDER)
while broken.effect_stack.shield_break_window is not None:
    broken = step(broken)
check("822.3: a broken G Castle goes to the graveyard, not the hand",
      names(broken.players[1].graveyard) == ["g-castle"] and not broken.players[1].hand)

# ── An attack ends when the attacker leaves (505.6, 509.1) ──────────────────
state = make_state(phase=Phase.ATTACK)
doomed = creature(state, 0, card("doomed", effects=[ability(
    EffectAction.NONE, TriggerEvent.ON_ATTACK, text="When this attacks, nothing.")]))
declared = act(state, ActionType.ATTACK_PLAYER)
move_battle_to_graveyard(declared, 0, doomed.uid, reason="effect")
declared = step(declared)
check("505.6: the attack ends without its attacker",
      declared.current_phase == Phase.END_OF_ATTACK and declared.result == GameResult.IN_PROGRESS,
      f"{declared.current_phase} {declared.result}")

state = make_state(phase=Phase.DIRECT_ATTACK)
state.attack_context = AttackContext("gone", 0, "player", None)
after = step(state)
check("509.1: a creature that has left cannot win by direct attack",
      after.result == GameResult.IN_PROGRESS)

# ── "When this battles" resolves before power is compared (701.13a) ─────────
state = make_state(phase=Phase.BATTLE)
mine = creature(state, 0, card("thinker", power=5000, effects=[ability(
    EffectAction.DRAW, TriggerEvent.ON_BATTLE, text="When this creature battles, draw a card.")]))
theirs = creature(state, 1, card("victim", power=1000), is_tapped=True)
state.attack_context = AttackContext(mine.uid, 0, "creature", theirs.uid)
first = step(state)
check("701.13a: the battle ability resolves while both creatures are still there",
      len(first.players[0].hand) == 1 and len(first.players[1].battle_zone) == 1
      and first.current_phase == Phase.BATTLE)
second = step(first)
check("701.13b: then power is compared, and the ability is not repeated",
      not second.players[1].battle_zone and len(second.players[0].hand) == 1)

# ── G-Strike (509.5b) ───────────────────────────────────────────────────────
STRIKE = card("striker", keywords=(Keyword.G_STRIKE,))
state = make_state(phase=Phase.DIRECT_ATTACK)
runners = [creature(state, 0, card(f"runner{i}")) for i in range(2)]
shields(state, 1, STRIKE)
state.attack_context = AttackContext(runners[0].uid, 0, "player", None)
after = act(state, ActionType.SELECT_ATTACK_ORDER)
after = act(after, ActionType.USE_G_STRIKE)      # declare
after = step(after)                              # shields go to hand
after = act(after, ActionType.USE_G_STRIKE)      # resolve
check("509.5b: G-Strike asks the shield's owner for a creature",
      waiting(after) is not None and waiting(after).player == 1)
after = pick(after, runners[1].uid)
stopped = after.find_creature_anywhere(runners[1].uid)[1]
check("509.5b: that creature can't attack this turn", not stopped.can_attack())
after.turn_info.phase = Phase.END_OF_TURN
after.attack_context = None
after = step(after)
check("511.2: 'this turn' ends for both players' creatures",
      "cannot_attack" not in after.find_creature_anywhere(runners[1].uid)[1].temp_flags)

# ── Shields broken during an S-Trigger form a new, earlier batch (101.4g) ───
trigger_card = card("trigger", card_type=CardType.SPELL, keywords=(Keyword.SHIELD_TRIGGER,), effects=[])
state = make_state()
shields(state, 0, trigger_card, card("second"), card("third"))
move_shield_to_standby(state, 0, 0)
outer = state.effect_stack.shield_break_window
outer.phase = "resolve"
outer.pending_resolutions = [("s_trigger", "x", "")]
move_shield_to_standby(state, 0, 0)
inner = state.effect_stack.shield_break_window
check("101.4g: the new batch is declared first and the old one waits",
      inner is not outer and inner.parent is outer and inner.phase == "declare"
      and len(outer.standby_shields) == 1 and len(inner.standby_shields) == 1)
done = step(step(state))
check("101.4g: then the earlier batch resumes",
      done.effect_stack.shield_break_window is not None
      and done.effect_stack.shield_break_window.parent is None
      and names(done.players[0].hand) == ["second"])

# ── A spell reaches the graveyard only after resolving (409.1b, 605.2f) ─────
state = make_state()
creature(state, 1, card("target"))
three = scripted("The_Triple_Three", card_type=CardType.SPELL)
paused = cast_from_hand(state, three)
check("409.1b: while resolving, the spell is not in the graveyard",
      waiting(paused) is not None and not paused.players[0].graveyard
      and len(paused.players[0].resolving) == 1)
finished = pick(paused, "mode:draw")
check("605.2f: afterwards it is in the graveyard",
      names(finished.players[0].graveyard) == [three.name] and not finished.players[0].resolving)

# ── A card being used resolves before waiting abilities (601.1g) ────────────
watcher = card("watcher", effects=[ability(
    EffectAction.DRAW, TriggerEvent.ON_CAST, text="Whenever you cast a spell, draw a card.")])
state = make_state()
creature(state, 0, watcher)
creature(state, 1, card("target"))
paused = cast_from_hand(state, three)
check("601.1g: the spell resolves first, the watcher waits",
      waiting(paused) is not None and not paused.players[0].hand)

# ── A spell cast by an ability resolves inside it (101.4d) ──────────────────
state = make_state()
state.players[0].graveyard.clear()
from core.zones import GraveyardCard
state.players[0].graveyard.append(GraveyardCard(definition=card(
    "inner", cost=1, card_type=CardType.SPELL, keywords=(Keyword.SHIELD_TRIGGER,),
    effects=[ability(EffectAction.DRAW, kind=EffectType.SPELL, text="Draw a card.")])))
hand(state, 0, card("keep"))
petency = scripted("Perfect_Petency", card_type=CardType.SPELL)
run = cast_from_hand(state, petency)
run = pick(run, "mode:shield_trigger")
run = pick(run, "mode:draw_discard")
while waiting(run) is not None and waiting(run).prompt != "Discard a card":
    run = pick(run, waiting(run).valid_options[0])
check("101.4d: the nested spell finished before the next part of the ability",
      waiting(run) is not None and "inner" in [d.name for d in run.players[0].deck]
      and len(run.players[0].hand) == 4, f"hand={names(run.players[0].hand)}")

# ── The turn player orders their own effects (605.1, 101.4a) ────────────────
def enter_draw(label, player_amount):
    return card(label, effects=[ability(
        EffectAction.DRAW, TriggerEvent.ON_ATTACK, value={"amount": player_amount},
        text="Whenever one of your creatures attacks, draw.")])


state = make_state()
one = creature(state, 0, enter_draw("one", 1))
two = creature(state, 0, enter_draw("two", 2))
for source in (one, two):
    state.effect_stack.add_trigger(PendingTrigger(
        effect=source.definition.effects[0], source_uid=source.uid,
        source_card_id=source.id, controller=0))
asked = resolve_pending_triggers(state)
check("605.1: two of your effects: you choose which is first",
      waiting(asked) is not None and waiting(asked).choice_type == "order_effects"
      and len(waiting(asked).valid_options) == 2)
chosen = next(o for o, cid in waiting(asked).option_cards.items() if cid == two.id)
resolved = pick(asked, chosen)
check("605.1: both then resolve", len(resolved.players[0].hand) == 3 and waiting(resolved) is None)

state = make_state()
for player, source in ((1, creature(state, 1, enter_draw("theirs", 1))),
                       (0, creature(state, 0, enter_draw("mine", 1)))):
    state.effect_stack.add_trigger(PendingTrigger(
        effect=source.definition.effects[0], source_uid=source.uid,
        source_card_id=source.id, controller=player))
state.effect_stack.sort_pending(0)
check("603.3: the turn player's effects come first",
      [t.controller for t in state.effect_stack.pending_triggers] == [0, 1])

# ── State-based actions apply between the parts of an ability (111.2d) ──────
state = make_state()
weak = creature(state, 1, card("weak", power=2000))
sturdy = creature(state, 1, card("sturdy", power=9000))
ater = scripted("Ater_Gorghini")
(held,) = hand(state, 0, ater)
move_hand_to_battle(state, 0, held.uid)
run = resolve_pending_triggers(state)
run = pick(run, "mode:weaken")
run = pick(run, "mode:weaken")
run = pick(run, weak.uid)
# With the weak one already gone, the second -4000 has only one creature to take
check("111.2d: a creature at 0 power is gone before the next part",
      waiting(run) is None and names(run.players[1].battle_zone) == ["sturdy"]
      and run.find_creature_anywhere(sturdy.uid)[1].compute_power(run) == 5000)

# ── Jashin destroys a creature or Tamaseed, no other kind of card ───────────
state = make_state()
creature(state, 1, card("a-creature", cost=1))
creature(state, 1, card("a-tamaseed", cost=1, card_type=CardType.TAMASEED))
creature(state, 1, scripted("Jablood,_Wicked_Dragon", cost=1))  # not a creature yet
creature(state, 1, card("a-field", cost=1, card_type=CardType.FIELD))
creature(state, 1, card("a-gear", cost=1, card_type=CardType.CROSS_GEAR))
state.players[0].graveyard.extend(
    GraveyardCard(definition=card(f"fuel{n}")) for n in range(3))
(held,) = hand(state, 0, scripted("Jashin_Emperor,_Abyssal_Darkness"))
move_hand_to_battle(state, 0, held.uid)
run = resolve_pending_triggers(state)
offered = sorted(run.find_creature_anywhere(uid)[1].definition.name
                 for uid in waiting(run).valid_options)
check("Jashin: only creatures and Tamaseeds can be destroyed",
      offered == ["Jablood, Wicked Dragon", "a-creature", "a-tamaseed"], str(offered))

# ── "If you do" needs only the attempt (101.3a) ─────────────────────────────
state = make_state()
tough = creature(state, 1, card("tough"))
tough.set_flag("cannot_be_destroyed")
state.players[1].graveyard.append(GraveyardCard(definition=card("returner")))
(held,) = hand(state, 0, scripted("Babelginus,_Demonic_Dragon"))
move_hand_to_battle(state, 0, held.uid)
run = pick(resolve_pending_triggers(state), tough.uid)
while waiting(run) is not None:
    run = pick(run, waiting(run).valid_options[0])
check("101.3a: the follow-up happens though the creature survived",
      sorted(names(run.players[1].battle_zone)) == ["returner", "tough"])

# ── End of turn (511, 512) ──────────────────────────────────────────────────
state = make_state(phase=Phase.END_OF_TURN)
chooser = creature(state, 0, scripted("Zardiclica,_Hybrid_Winds_Dragon"))
state.players[0].spells_cast_this_turn = 1
prey = creature(state, 1, card("prey", power=7000))
prey.power_modifiers.append(PowerModifier(
    amount=-3000, source_uid="x", duration="until_end_of_turn"))
paused = step(state)
check("511.1: end-of-turn abilities resolve before the turn passes",
      waiting(paused) is not None and paused.active_player == 0
      and paused.current_phase == Phase.END_OF_TURN)
check("511.2: 'until end of turn' still applies while they resolve",
      waiting(paused) is not None and waiting(paused).valid_options == [prey.uid])
after = pick(paused)
if after.active_player == 0:
    after = step(after)
check("512: then the other player's turn starts",
      after.active_player == 1 and not after.turn_info.turn_end_triggered)

# ── An empty deck loses, even for a moment (104.2b) ─────────────────────────
state = make_state(deck=1)
draw_card(state, 0)
state.players[0].deck.append(card("returned"))
check("104.2b: a momentarily empty deck still loses",
      check_state_based_actions(state).result == GameResult.PLAYER_1_WINS)

# ── G-NEO and NEO (802.3, 803.2) ────────────────────────────────────────────
def evolve_gneo(state, base_entered_turn=1):
    base = creature(state, 0, card("source"), entered_turn=base_entered_turn)
    (held,) = hand(state, 0, card("gneo", subtype=CardSubtype.G_NEO))
    return move_hand_to_battle(state, 0, held.uid, evolution_base_uid=base.uid)


state = make_state()
top = evolve_gneo(state)
move_battle_to_graveyard(state, 0, top.uid, reason="effect")
check("803.2: only the card underneath is destroyed",
      names(state.players[0].battle_zone) == ["gneo"]
      and names(state.players[0].graveyard) == ["source"])
check("802.3: it then has summoning sickness on the turn it evolved", top.has_summoning_sickness)
move_battle_to_graveyard(state, 0, top.uid, reason="effect")
check("803.2: with nothing underneath it leaves normally",
      not state.players[0].battle_zone and len(state.players[0].graveyard) == 2)

state = make_state()
top = evolve_gneo(state)
state.turn_info.turn_number += 2
move_battle_to_zone(state, 0, top.uid, reason="bounce", to_zone="hand",
                    place=lambda d, uid: state.players[0].hand.append(HandCard(definition=d, uid=uid)))
check("803.2: bouncing returns the card underneath instead",
      names(state.players[0].hand) == ["source"] and names(state.players[0].battle_zone) == ["gneo"]
      and not top.has_summoning_sickness)

state = make_state()
base = creature(state, 0, card("source"))
(held,) = hand(state, 0, card("plain-neo", subtype=CardSubtype.NEO))
top = move_hand_to_battle(state, 0, held.uid, evolution_base_uid=base.uid)
move_battle_to_graveyard(state, 0, top.uid, reason="effect")
check("802: a plain NEO leaves with everything underneath",
      not state.players[0].battle_zone and len(state.players[0].graveyard) == 2)

print(f"\n{len(FAILURES)} failed")
sys.exit(1 if FAILURES else 0)
