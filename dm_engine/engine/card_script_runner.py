"""
engine/card_script_runner.py — run the steps of a scripted card ability.

Each handler changes the state and returns None, or returns Ask to get a choice
from a player. The remaining steps travel with the paused trigger, so the ability
continues where it stopped once the player answers.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, replace

from core.card_scripts import (
    CREATURE_ONLY_WITH_DARKNESS, EVOLUTION_SLUGS, LEAVE_REPLACEMENTS, MODE_TABLES,
    script_steps,
)
from core.continuous_rules import (
    NAMED_FLAG, can_be_chosen, has_race, is_creature_now, spells_locked, sync_ignored_abilities,
)
from core.enums import CardSubtype, CardType, Civilization
from core.state import AwaitedChoice, GameState, PendingTrigger
from engine.entry_rules import after_enter, entry_redirect, may_enter
from engine.sba.checker import run_state_based_actions
from core.zones import Creature, EvolutionStackEntry, GraveyardCard, ManaCard, PowerModifier

_NO_ANSWER = object()


@dataclass
class Ask:
    player: int
    options: list[tuple[str, int | None]]  # (option id, card id when the chooser can see it)
    optional: bool = False
    prompt: str = ""


@dataclass
class Context:
    state: GameState
    trigger: PendingTrigger
    queue: list

    @property
    def me(self) -> int:
        return self.trigger.controller

    @property
    def opponent(self) -> int:
        return 1 - self.trigger.controller

    def then(self, *steps) -> None:
        """Run these steps next, before the rest of the ability."""
        self.queue[1:1] = list(steps)


def run_script(state: GameState, trigger: PendingTrigger) -> bool:
    """
    Run a scripted ability on ``state`` in place.

    Returns False if it paused on a player choice, True once it has finished.
    """
    data = dict(trigger.trigger_data)
    queue = data.pop("steps", None)
    if queue is None:
        queue = copy.deepcopy(script_steps(trigger.effect.effect_value["script"]))
    answer = data.pop("picked", _NO_ANSWER)
    ctx = Context(state, trigger, list(queue))

    while ctx.queue:
        operation, params = ctx.queue[0]
        waiting = {id(t) for t in state.effect_stack.pending_triggers}
        result = _HANDLERS[operation](ctx, params, answer)
        answer = _NO_ANSWER
        if isinstance(result, Ask):
            if len(result.options) == 1 and not result.optional:
                answer = result.options[0][0]  # nothing to decide
                continue
            paused = replace(trigger, trigger_data={**data, "steps": ctx.queue})
            state.effect_stack.set_choice(AwaitedChoice(
                choice_type="select_target",
                player=result.player,
                effect=trigger.effect,
                source_uid=trigger.source_uid,
                valid_options=[option for option, _ in result.options],
                min_choices=0 if result.optional else 1,
                prompt=result.prompt or trigger.effect.raw_text,
                trigger=paused,
                option_cards={option: card_id for option, card_id in result.options if card_id},
            ))
            return False
        ctx.queue.pop(0)
        run_state_based_actions(state)  # they apply even mid-ability (rule 111.2d)
        if state.is_terminal():
            return True
        used = _cards_used_since(state, waiting)
        if used and ctx.queue:
            # A card used by this ability resolves as part of it (rule 101.4d):
            # the rest of the ability continues after that card is done
            rest = replace(trigger, trigger_data={**data, "steps": ctx.queue}, immediate=True)
            state.effect_stack.pending_triggers.insert(used, rest)
            return True
    return True


def _cards_used_since(state: GameState, waiting: set[int]) -> int:
    """How many effects of newly used cards are at the front of the queue."""
    count = 0
    for pending in state.effect_stack.pending_triggers:
        if not pending.immediate or id(pending) in waiting:
            break
        count += 1
    return count


# ── Shared actions ──────────────────────────────────────────────────────────

def _to_graveyard(state: GameState, player: int, definition, uid: str | None, reason: str) -> None:
    kwargs = {"uid": uid} if uid else {}
    state.players[player].graveyard.insert(0, GraveyardCard(
        definition=definition, died_from=reason, died_on_turn=state.turn_number, **kwargs,
    ))


def _put_in_mana(state: GameState, player: int, definition) -> None:
    # Multicolored cards enter the mana zone tapped
    state.players[player].mana_zone.append(
        ManaCard(definition=definition, is_tapped=len(definition.civilizations) > 1)
    )


def _discard(state: GameState, player: int, uid: str, *, by_opponent: bool = False) -> None:
    from engine.zone_mover import move_hand_to_graveyard
    card = state.players[player].find_in_hand(uid)
    if by_opponent and card is not None and may_enter(
        state, player, from_hand=True, played=False, definition=card.definition,
    ):
        from engine.shield_break_window import _scripted_triggers
        # "You may put it instead": the owner decides before anything else happens
        offer = _scripted_triggers(card.definition, "instead_of_discard", uid, player)
        if offer:
            state.effect_stack.add_immediate(offer)
            return
    move_hand_to_graveyard(state, player, uid, reason="effect")


def _destroy(state: GameState, uid: str) -> bool:
    """Destroy a creature by effect. Returns True if it left the battle zone."""
    from engine.zone_mover import move_battle_to_graveyard
    found = state.find_creature_anywhere(uid)
    if found is None or not found[1].can_be_destroyed():
        return False
    move_battle_to_graveyard(state, found[0], uid, reason="effect")
    return state.find_creature_anywhere(uid) is None


def _is_evolution(definition) -> bool:
    return definition.is_evolution() or definition.slug in EVOLUTION_SLUGS


def _needs_evolution_source(definition) -> bool:
    """It cannot enter without a card to go on (rule 801.1a); S-MAX can."""
    return _is_evolution(definition) and definition.card_subtype != CardSubtype.STAR_MAX \
        and definition.slug not in EVOLUTION_SLUGS


def _is_abyss(definition) -> bool:
    return any("Abyss" in race for race in definition.races)


_REANIMATE_FILTERS = {
    "creature": lambda d: True,
    "non_evolution": lambda d: not _is_evolution(d),
    "non_evolution_darkness": lambda d: not _is_evolution(d) and Civilization.DARKNESS in d.civilizations,
    "abyss": _is_abyss,
}


def _put_from_graveyard(state: GameState, player: int, uid: str, *, summoned: bool = False) -> None:
    graveyard = state.players[player].graveyard
    card = next((c for c in graveyard if c.uid == uid), None)
    if card is None or not may_enter(state, player, from_hand=False, played=summoned,
                                     definition=card.definition):
        return
    graveyard.remove(card)
    redirect = entry_redirect(state, player, card.definition, summoned=summoned)
    if redirect is not None:
        from engine.zone_mover import place_in_zone
        place_in_zone(state, player, redirect, "redirected")(card.definition, card.uid)
        return
    creature = Creature(
        definition=card.definition, uid=card.uid, controller=player, owner=player,
        entered_turn=state.turn_number, has_summoning_sickness=True,
    )
    state.players[player].battle_zone.append(creature)
    creature.apply_static_effects(state)
    from engine.zone_mover import _fire_enter_battle_zone_trigger
    _fire_enter_battle_zone_trigger(state, creature, player, summoned=summoned)
    after_enter(state, creature, player, summoned=summoned, paid=False, from_graveyard=True)


def _is_tamaseed(definition) -> bool:
    # "Tamaseed / Creature" cards load as creatures and are listed by name
    return (definition.card_type == CardType.TAMASEED
            or definition.slug in CREATURE_ONLY_WITH_DARKNESS)


def _opposing_targets(
    ctx: Context, max_cost: int | None = None, max_power: int | None = None,
    include_tamaseeds: bool = False,
) -> list[tuple[str, int]]:
    return [
        (c.uid, c.definition.id)
        for c in ctx.state.players[ctx.opponent].battle_zone
        if (max_cost is None or c.definition.cost <= max_cost)
        and (max_power is None or c.compute_power(ctx.state) <= max_power)
        and can_be_chosen(ctx.state, c, ctx.me)
        and (is_creature_now(ctx.state, c) or (include_tamaseeds and _is_tamaseed(c.definition)))
    ]


# ── Step handlers ───────────────────────────────────────────────────────────

def _step_draw(ctx, params, answer):
    from engine.zone_mover import draw_card
    player = ctx.me if params["who"] == "self" else ctx.opponent
    for _ in range(params["count"]):
        draw_card(ctx.state, player)


def _step_mill(ctx, params, answer):
    deck = ctx.state.players[ctx.me].deck
    for _ in range(min(params["count"], len(deck))):
        _to_graveyard(ctx.state, ctx.me, deck.pop(0), None, "effect")
        ctx.state.players[ctx.me].note_deck()


def _step_may(ctx, params, answer):
    if answer is _NO_ANSWER:
        return Ask(ctx.me, [("yes", None)], optional=True, prompt=params.get("prompt", ""))
    if answer == "yes":
        ctx.then(*params["then"])


def _step_random_discard(ctx, params, answer):
    state = ctx.state
    hand = state.players[ctx.opponent].hand
    if params["count"] <= 0 or not hand:
        return
    # Seeded from the game position, so a replayed game discards the same cards
    rng = random.Random(
        f"{state.game_id}:{state.turn_number}:{ctx.trigger.source_uid}:{params['count']}")
    _discard(state, ctx.opponent, hand[rng.randrange(len(hand))].uid, by_opponent=True)
    ctx.then(("random_discard", {"count": params["count"] - 1}))


def _step_opponent_discards(ctx, params, answer):
    hand = ctx.state.players[ctx.opponent].hand
    if answer is _NO_ANSWER:
        if params["count"] <= 0 or not hand:
            return None
        return Ask(ctx.opponent, [(c.uid, c.definition.id) for c in hand], prompt="Discard a card")
    _discard(ctx.state, ctx.opponent, answer, by_opponent=True)
    ctx.then(("opponent_discards", {"count": params["count"] - 1}))


def _step_discard_then_random(ctx, params, answer):
    hand = ctx.state.players[ctx.me].hand
    if answer is _NO_ANSWER and params["left"] > 0 and hand:
        return Ask(ctx.me, [(c.uid, c.definition.id) for c in hand], optional=True, prompt="Discard a card?")
    if answer is not _NO_ANSWER and answer is not None:
        _discard(ctx.state, ctx.me, answer)
        ctx.then(("discard_then_random", {"left": params["left"] - 1, "discarded": params["discarded"] + 1}))
    elif params["discarded"]:
        ctx.then(("random_discard", {"count": params["discarded"]}))


def _step_debuff(ctx, params, answer):
    if answer is _NO_ANSWER:
        targets = _opposing_targets(ctx)
        return Ask(ctx.me, targets, prompt="Choose an opposing creature") if targets else None
    found = ctx.state.find_creature_anywhere(answer or "")
    if found:
        found[1].power_modifiers.append(PowerModifier(
            amount=params["amount"], source_uid=ctx.trigger.source_uid, duration="until_end_of_turn",
        ))


def _step_lock(ctx, params, answer):
    if answer is _NO_ANSWER:
        targets = _opposing_targets(ctx)
        return Ask(ctx.me, targets, prompt="Choose an opposing creature") if targets else None
    found = ctx.state.find_creature_anywhere(answer or "")
    if found:
        # Cleared when this player's next turn starts
        found[1].temp_flags["locked_until_turn_of"] = ctx.me


def _step_destroy(ctx, params, answer):
    if answer is _NO_ANSWER:
        max_cost = params["max_cost"]
        if max_cost == "graveyard":
            max_cost = len(ctx.state.players[ctx.me].graveyard)
        targets = _opposing_targets(
            ctx, max_cost, params.get("max_power"), params.get("tamaseeds", False))
        if not targets:
            return None
        return Ask(ctx.me, targets, optional=params["optional"], prompt="Choose a creature to destroy")
    if answer:
        _destroy(ctx.state, answer)
        # "If you do" is met by attempting it (rule 101.3a)
        ctx.then(*copy.deepcopy(params.get("then_if_destroyed", [])))


def _step_reanimate(ctx, params, answer):
    player = params.get("player", ctx.me)
    if answer is _NO_ANSWER:
        if not may_enter(ctx.state, player, from_hand=False, played=False):
            return None
        allowed = _REANIMATE_FILTERS[params["kind"]]
        options = [
            (c.uid, c.definition.id)
            for c in ctx.state.players[player].graveyard
            if c.definition.card_type == CardType.CREATURE
            and (params["max_cost"] is None or c.definition.cost <= params["max_cost"])
            and c.definition.name != params.get("except_name")
            and not _needs_evolution_source(c.definition)
            and allowed(c.definition)
        ]
        if not options:
            return None
        return Ask(ctx.me, options, optional=params["optional"], prompt="Choose a creature from the graveyard")
    if answer:
        _put_from_graveyard(ctx.state, player, answer)


def _step_modes(ctx, params, answer):
    modes = MODE_TABLES[params["table"]]
    chosen = params["chosen"]
    if answer is not _NO_ANSWER:
        chosen = [*chosen, answer]
        ctx.queue[0] = ("modes", {**params, "chosen": chosen})
    if len(chosen) < params["count"]:
        return Ask(ctx.me, [(mode, None) for mode in modes], prompt="Choose an effect")
    # All choices are made first, then resolved in the order chosen
    ctx.then(*[step for mode in chosen for step in copy.deepcopy(modes[mode])])


def _step_look_top(ctx, params, answer):
    deck = ctx.state.players[ctx.me].deck
    look, places = min(params["look"], len(deck)), params["places"]
    if places and look:
        if answer is _NO_ANSWER:
            options = [(f"deck:{i}", deck[i].id) for i in range(look)]
            return Ask(ctx.me, options, prompt=f"Choose a card for your {places[0]}")
        definition = deck.pop(int(answer.split(":")[1]))
        ctx.state.players[ctx.me].note_deck()
        if places[0] == "hand":
            from engine.zone_mover import _new_uid
            from core.zones import HandCard
            ctx.state.players[ctx.me].hand.append(HandCard(definition=definition, uid=_new_uid()))
        elif places[0] == "mana":
            _put_in_mana(ctx.state, ctx.me, definition)
        else:
            _to_graveyard(ctx.state, ctx.me, definition, None, "effect")
        ctx.then(("look_top", {"look": look - 1, "places": places[1:]}))
        return None
    for _ in range(look):  # whatever is left goes to the bottom
        deck.append(deck.pop(0))


def _step_opponent_hand_to_mana(ctx, params, answer):
    p_state = ctx.state.players[ctx.opponent]
    for card in list(p_state.hand):
        p_state.hand.remove(card)
        _put_in_mana(ctx.state, ctx.opponent, card.definition)


def _step_once_per_turn(ctx, params, answer):
    found = ctx.state.find_creature_anywhere(ctx.trigger.source_uid)
    if found is None:
        del ctx.queue[1:]
        return
    flags = found[1].temp_flags
    if flags.get(params["flag"]) == ctx.state.turn_number:
        del ctx.queue[1:]
    else:
        flags[params["flag"]] = ctx.state.turn_number


def _step_destroy_self_unless_shields_broken(ctx, params, answer):
    if ctx.state.active_player == ctx.me:
        return
    if ctx.state.players[ctx.me].shields_broken_this_turn < params["count"]:
        _destroy(ctx.state, ctx.trigger.source_uid)


def _step_babelginus(ctx, params, answer):
    if answer is _NO_ANSWER:
        # Choosing a creature also chooses its player
        targets = [
            (c.uid, c.definition.id) for p in ctx.state.players for c in p.battle_zone
            if can_be_chosen(ctx.state, c, ctx.me)
        ]
        return Ask(ctx.me, targets, optional=True, prompt="Destroy a creature?") if targets else None
    if not answer:
        return None
    found = ctx.state.find_creature_anywhere(answer)
    if found:
        _destroy(ctx.state, answer)  # attempting it is enough (rule 101.3a)
        ctx.then(("reanimate", {
            "max_cost": None, "kind": "non_evolution", "optional": False,
            "player": found[0], "except_name": params["except_name"],
        }))


def _step_keep_shields(ctx, params, answer):
    player = ctx.me if params["seat"] == "self" else ctx.opponent
    zone = ctx.state.players[player].shield_zone
    if answer is not _NO_ANSWER:
        shield = next((s for s in zone if s.uid == answer), None)
        if shield is not None:
            zone.remove(shield)
            _to_graveyard(ctx.state, player, shield.definition, shield.uid, "effect")
    if len(zone) > params["keep"]:
        if answer is not _NO_ANSWER:
            ctx.then(("keep_shields", dict(params)))
            return None
        # Shields are face down, so no card ids are shown to the chooser
        return Ask(player, [(s.uid, None) for s in zone], prompt="Choose a shield to put into the graveyard")


def _step_tap_opposing(ctx, params, answer):
    if answer is _NO_ANSWER:
        targets = [
            (c.uid, c.definition.id)
            for c in ctx.state.players[ctx.opponent].battle_zone
            if not c.is_tapped and can_be_chosen(ctx.state, c, ctx.me)
        ]
        if params["left"] <= 0 or not targets:
            return None
        return Ask(ctx.me, targets, prompt="Choose a creature to tap")
    found = ctx.state.find_creature_anywhere(answer or "")
    if found:
        found[1].tap()
        ctx.then(("tap_opposing", {"left": params["left"] - 1}))


def _step_next_creature_discount(ctx, params, answer):
    ctx.state.players[ctx.me].next_creature_discount += params["amount"]


def _step_untap_self(ctx, params, answer):
    found = ctx.state.find_creature_anywhere(ctx.trigger.source_uid)
    if found:
        found[1].untap()


def _step_return_spells(ctx, params, answer):
    p_state = ctx.state.players[ctx.me]
    if answer is _NO_ANSWER:
        spells = [(c.uid, c.definition.id) for c in p_state.graveyard if c.definition.is_spell()]
        if params["left"] <= 0 or not spells:
            return None
        return Ask(ctx.me, spells, optional=True, prompt="Return a spell to your hand?")
    card = next((c for c in p_state.graveyard if c.uid == answer), None)
    if card is not None:
        from core.zones import HandCard
        p_state.graveyard.remove(card)
        p_state.hand.append(HandCard(definition=card.definition, uid=card.uid))
        ctx.then(("return_spells", {"left": params["left"] - 1}))


def _step_cast_from_hand(ctx, params, answer):
    if answer is _NO_ANSWER:
        spells = [
            (c.uid, c.definition.id)
            for c in ctx.state.players[ctx.me].hand
            if c.definition.is_spell() and not spells_locked(ctx.state, ctx.me)
            and c.definition.cost <= params["max_cost"]
            # Effects that stop a player casting spells still apply
            and ctx.state.global_effects.can_execute_from_hand(
                ctx.me, c.definition.card_type.value, c.definition.card_subtype.value,
                c.definition.civilizations,
            )[0]
        ]
        if not spells:
            return None
        return Ask(ctx.me, spells, optional=True, prompt="Cast a spell for no cost?")
    if answer:
        from engine.shield_break_window import execute_free_from_hand
        execute_free_from_hand(ctx.state, ctx.me, answer, reason="cast")


def _step_cast_then_bottom(ctx, params, answer):
    p_state = ctx.state.players[ctx.me]
    spells = [
        c for c in [*p_state.hand, *p_state.graveyard]
        if c.definition.is_spell() and c.definition.cost <= params["max_cost"]
        and not spells_locked(ctx.state, ctx.me)
        and ctx.state.global_effects.can_execute_from_hand(
            ctx.me, c.definition.card_type.value, c.definition.card_subtype.value,
            c.definition.civilizations,
        )[0]
    ]
    if answer is _NO_ANSWER:
        if not spells:
            return None
        return Ask(ctx.me, [(c.uid, c.definition.id) for c in spells], optional=True,
                   prompt="Cast a spell for no cost?")
    chosen = next((c for c in spells if c.uid == answer), None)
    if chosen is None:
        return None
    from engine.shield_break_window import queue_spell_effects
    zone = p_state.hand if chosen in p_state.hand else p_state.graveyard
    zone.remove(chosen)
    # It goes to the bottom of the deck instead of the graveyard
    queue_spell_effects(ctx.state, ctx.me, chosen.uid, chosen.definition,
                        destination="deck_bottom")


def _leave_for_mana(state: GameState, uid: str) -> bool:
    from engine.zone_mover import move_battle_to_zone
    found = state.find_creature_anywhere(uid)
    if found is None:
        return False
    player = found[0]
    return move_battle_to_zone(
        state, player, uid, reason="to_mana", to_zone="mana_zone",
        place=lambda definition, _uid: _put_in_mana(state, player, definition),
    )


def _step_self_to_mana(ctx, params, answer):
    if ctx.state.find_creature_anywhere(ctx.trigger.source_uid) is not None:
        _leave_for_mana(ctx.state, ctx.trigger.source_uid)
        # "If you do" is met even if something replaced the move (rule 101.3a)
        ctx.then(*copy.deepcopy(params["then"]))


def _step_mana_or_leave(ctx, params, answer):
    p_state = ctx.state.players[ctx.me]
    has_other = any(
        c.uid != ctx.trigger.source_uid and any(params["race"] in r for r in c.definition.races)
        for c in p_state.battle_zone
    )
    if not has_other:
        _leave_for_mana(ctx.state, ctx.trigger.source_uid)
    elif p_state.deck:
        _put_in_mana(ctx.state, ctx.me, p_state.deck.pop(0))
        p_state.note_deck()


def _step_gachinko_extra_turn(ctx, params, answer):
    mine, theirs = ctx.state.players[ctx.me].deck, ctx.state.players[ctx.opponent].deck
    if not mine or not theirs:
        return
    # Each reveals the top card, then puts it on the bottom
    revealed = [mine.pop(0), theirs.pop(0)]
    mine.append(revealed[0])
    theirs.append(revealed[1])
    if revealed[0].cost >= revealed[1].cost:
        ctx.state.players[ctx.me].extra_turns += 1


def _step_name_card(ctx, params, answer):
    if answer is _NO_ANSWER:
        # Any creature name, from either side, including cards not seen yet. Only
        # public zones are added to the game's list, so no hidden card is revealed.
        names = dict(ctx.state.nameable_creatures)
        for player in ctx.state.players:
            for card in [*player.battle_zone, *player.mana_zone, *player.graveyard]:
                if card.definition.card_type == CardType.CREATURE:
                    names.setdefault(card.definition.name, card.definition.id)
        names.pop(params["except_name"], None)
        if not names:
            return None
        return Ask(ctx.me, [(f"name:{n}", cid) for n, cid in sorted(names.items())],
                   prompt="Choose a creature card name")
    found = ctx.state.find_creature_anywhere(ctx.trigger.source_uid)
    if found and answer:
        found[1].temp_flags[NAMED_FLAG] = answer.split(":", 1)[1]
        sync_ignored_abilities(ctx.state)


def _castable(ctx, definition) -> bool:
    return not spells_locked(ctx.state, ctx.me) and ctx.state.global_effects.can_execute_from_hand(
        ctx.me, definition.card_type.value, definition.card_subtype.value, definition.civilizations,
    )[0]


_PLAY_FILTERS = {
    "non_creature": lambda d: not d.is_creature(),
    "darkness": lambda d: Civilization.DARKNESS in d.civilizations,
    "shield_trigger": lambda d: d.has_shield_trigger(),
}


def _step_play_from_graveyard(ctx, params, answer):
    """"Play" a card from your graveyard for no cost: cast it, or summon it."""
    p_state = ctx.state.players[ctx.me]
    limit = len(p_state.graveyard) if params["max_cost"] == "graveyard" else params["max_cost"]

    def playable(definition) -> bool:
        if definition.is_spell():
            return _castable(ctx, definition)
        # Only creatures and cross gear can be played onto the battle zone here
        return (not _needs_evolution_source(definition) and definition.card_type in (CardType.CREATURE, CardType.CROSS_GEAR)
                and may_enter(ctx.state, ctx.me, from_hand=False, played=True))

    if answer is _NO_ANSWER:
        allowed = _PLAY_FILTERS[params["kind"]]
        options = [
            (c.uid, c.definition.id) for c in p_state.graveyard
            if (limit is None or c.definition.cost <= limit)
            and allowed(c.definition) and playable(c.definition)
        ]
        if not options:
            return None
        return Ask(ctx.me, options, optional=params["optional"], prompt="Play a card from your graveyard")
    card = next((c for c in p_state.graveyard if c.uid == answer), None)
    if card is None:
        return None
    from engine.shield_break_window import offer_super_shield_trigger
    if card.definition.is_spell():
        from engine.shield_break_window import queue_spell_effects
        p_state.graveyard.remove(card)
        # Cast this way, it goes to the bottom of the deck instead of the graveyard
        queue_spell_effects(ctx.state, ctx.me, card.uid, card.definition,
                            destination="deck_bottom",
                            super_ready=bool(params.get("super_as_if_no_shields")))
        return None
    _put_from_graveyard(ctx.state, ctx.me, card.uid, summoned=True)
    if params.get("super_as_if_no_shields"):
        offer_super_shield_trigger(ctx.state, ctx.me, card.uid, card.definition)


def _step_mill_up_to(ctx, params, answer):
    if answer is _NO_ANSWER:
        most = min(params["count"], len(ctx.state.players[ctx.me].deck))
        if not most:
            return None
        return Ask(ctx.me, [(f"count:{n}", None) for n in range(most + 1)], prompt="How many cards?")
    ctx.then(_mill_step(int(answer.split(":")[1])))


def _mill_step(count):
    return ("mill", {"count": count})


def _step_deck_to_mana(ctx, params, answer):
    p_state = ctx.state.players[ctx.me]
    for _ in range(params.get("count", 1)):
        if not p_state.deck:
            return
        _put_in_mana(ctx.state, ctx.me, p_state.deck.pop(0))
        p_state.note_deck()
        if params.get("tapped"):
            p_state.mana_zone[-1].is_tapped = True


def _step_metamorph_mana(ctx, params, answer):
    morphed = len(ctx.state.players[ctx.me].mana_zone) >= params["needed"]
    ctx.then(("deck_to_mana", {"count": params["morphed" if morphed else "normal"]}))


def _step_untap_mana(ctx, params, answer):
    for mana in ctx.state.players[ctx.me].mana_zone:
        mana.untap()


def _step_tap_all_opposing(ctx, params, answer):
    for creature in ctx.state.players[ctx.opponent].battle_zone:
        if is_creature_now(ctx.state, creature):
            creature.tap()


def _step_destroy_all_without_race(ctx, params, answer):
    doomed = [
        c.uid for p in ctx.state.players for c in p.battle_zone
        if is_creature_now(ctx.state, c) and not has_race(c.definition, params["race"])
    ]
    for uid in doomed:
        _destroy(ctx.state, uid)


def _step_bounce_all_opposing(ctx, params, answer):
    from engine.zone_mover import move_battle_to_zone
    uids = [c.uid for c in ctx.state.players[ctx.opponent].battle_zone
            if is_creature_now(ctx.state, c)]
    for uid in uids:
        move_battle_to_zone(ctx.state, ctx.opponent, uid, reason="returned", to_zone="hand")


def _step_put_dragon_from_hand(ctx, params, answer):
    from engine.zone_mover import move_hand_to_battle
    p_state = ctx.state.players[ctx.me]
    if answer is _NO_ANSWER:
        options = [
            (c.uid, c.definition.id) for c in p_state.hand
            if c.definition.is_creature() and not _is_evolution(c.definition)
            and Civilization.LIGHT in c.definition.civilizations
            and has_race(c.definition, "Dragon") and c.definition.cost <= params["max_cost"]
            and may_enter(ctx.state, ctx.me, from_hand=True, played=False, definition=c.definition)
        ]
        # The hand is hidden, so a player need not find one (rule 701.10b)
        return Ask(ctx.me, options, optional=True, prompt="Put a Light Dragon?") if options else None
    if answer:
        creature = move_hand_to_battle(ctx.state, ctx.me, answer, summoned=False)
        creature.temp_flags["blocker_until_turn_of"] = ctx.me
        sync_ignored_abilities(ctx.state)


def _step_reveal_dragons_to_mana(ctx, params, answer):
    p_state = ctx.state.players[ctx.me]
    revealed = [p_state.deck.pop(0) for _ in range(min(params["count"], len(p_state.deck)))]
    p_state.note_deck()
    for definition in revealed:
        if has_race(definition, "Dragon"):
            _put_in_mana(ctx.state, ctx.me, definition)
            p_state.mana_zone[-1].is_tapped = True
        else:
            _to_graveyard(ctx.state, ctx.me, definition, None, "effect")


def _step_reveal_creatures_to_hand(ctx, params, answer):
    from engine.zone_mover import _new_uid
    from core.zones import HandCard
    p_state = ctx.state.players[ctx.me]
    left = min(params["left"], len(p_state.deck))
    if answer is not None and answer is not _NO_ANSWER:
        definition = p_state.deck.pop(int(answer.split(":")[1]))
        p_state.hand.append(HandCard(definition=definition, uid=_new_uid()))
        p_state.note_deck()
        ctx.then(("reveal_creatures_to_hand", {"left": left - 1}))
        return None
    if answer is _NO_ANSWER:
        options = [(f"deck:{i}", p_state.deck[i].id) for i in range(left)
                   if p_state.deck[i].is_creature()]
        if options:
            return Ask(ctx.me, options, optional=True, prompt="Put a revealed creature into hand?")
    for _ in range(left):  # the rest go to the mana zone tapped
        _put_in_mana(ctx.state, ctx.me, p_state.deck.pop(0))
        p_state.mana_zone[-1].is_tapped = True
        p_state.note_deck()


def _step_mystery_cube(ctx, params, answer):
    p_state = ctx.state.players[ctx.me]
    if not p_state.deck:
        return None
    if not params.get("shuffled"):
        rng = random.Random(f"{ctx.state.game_id}:{ctx.state.turn_number}:{ctx.trigger.source_uid}")
        rng.shuffle(p_state.deck)
        ctx.queue[0] = ("mystery_cube", {"shuffled": True})
        params = ctx.queue[0][1]
    top = p_state.deck[0]
    if not top.is_creature():
        _put_in_mana(ctx.state, ctx.me, p_state.deck.pop(0))
        p_state.note_deck()
        return None
    # An evolution creature has nothing to go on, so it stays on top
    if _is_evolution(top) or not may_enter(
        ctx.state, ctx.me, from_hand=False, played=False, definition=top,
    ):
        return None
    if answer is _NO_ANSWER:
        return Ask(ctx.me, [("deck:0", top.id)], optional=True, prompt=f"Put {top.name}?")
    if answer:
        from engine.zone_mover import _new_uid, move_hand_to_battle
        from core.zones import HandCard
        held = HandCard(definition=p_state.deck.pop(0), uid=_new_uid())
        p_state.note_deck()
        p_state.hand.append(held)  # passes through so it enters like any other
        move_hand_to_battle(ctx.state, ctx.me, held.uid, summoned=False)


def _step_put_instead_of_discard(ctx, params, answer):
    card = ctx.state.players[ctx.me].find_in_hand(ctx.trigger.source_uid)
    if card is None:
        return None
    if answer is _NO_ANSWER:
        return Ask(ctx.me, [(card.uid, card.definition.id)], optional=True,
                   prompt=f"Put {card.definition.name} instead of discarding it?")
    if answer:
        from engine.zone_mover import move_hand_to_battle
        move_hand_to_battle(ctx.state, ctx.me, card.uid, summoned=False)
    else:
        _discard(ctx.state, ctx.me, card.uid)


def _step_cast_discarded(ctx, params, answer):
    owner = ctx.trigger.trigger_data["owner"]
    graveyard = ctx.state.players[owner].graveyard
    card = next((c for c in graveyard if c.uid == ctx.trigger.trigger_data["card"]), None)
    if card is None or not _castable(ctx, card.definition):
        return None
    if answer is _NO_ANSWER:
        return Ask(ctx.me, [(card.uid, card.definition.id)], optional=True,
                   prompt=f"Cast {card.definition.name} for no cost?")
    if answer:
        from engine.shield_break_window import queue_spell_effects
        graveyard.remove(card)
        queue_spell_effects(ctx.state, ctx.me, card.uid, card.definition, owner=owner)


def _step_put_under_from_hand(ctx, params, answer):
    found = ctx.state.find_creature_anywhere(ctx.trigger.source_uid)
    p_state = ctx.state.players[ctx.me]
    if found is None:
        return None
    if answer is _NO_ANSWER:
        options = [(c.uid, c.definition.id) for c in p_state.hand]
        return (Ask(ctx.me, options, optional=True, prompt="Put a card under this creature?")
                if options else None)
    card = p_state.find_in_hand(answer or "")
    if card is not None:
        p_state.hand.remove(card)
        # Underneath goes to the bottom of the stack (rule 403.4a)
        found[1].evolution_stack.append(EvolutionStackEntry(
            definition=card.definition, uid=card.uid, owner=ctx.me,
            entered_turn=ctx.state.turn_number))


def _step_deck_to_shield(ctx, params, answer):
    from core.zones import ShieldCard
    p_state = ctx.state.players[ctx.me]
    if p_state.deck:
        p_state.shield_zone.append(ShieldCard(definition=p_state.deck.pop(0)))
        p_state.note_deck()


def _step_mana_to_hand(ctx, params, answer):
    p_state = ctx.state.players[ctx.me]
    if answer is _NO_ANSWER:
        options = [(m.uid, m.definition.id) for m in p_state.mana_zone]
        return Ask(ctx.me, options, optional=True, prompt="Return a card from mana?") if options else None
    mana = next((m for m in p_state.mana_zone if m.uid == answer), None)
    if mana is not None:
        from core.zones import HandCard
        p_state.mana_zone.remove(mana)
        p_state.hand.append(HandCard(definition=mana.definition, uid=mana.uid))


def _step_discard_own(ctx, params, answer):
    hand = ctx.state.players[ctx.me].hand
    if answer is _NO_ANSWER:
        if params["count"] <= 0 or not hand:
            return None
        return Ask(ctx.me, [(c.uid, c.definition.id) for c in hand], prompt="Discard a card")
    _discard(ctx.state, ctx.me, answer)
    ctx.then(("discard_own", {"count": params["count"] - 1}))


def _step_shieldify_creature(ctx, params, answer):
    if answer is _NO_ANSWER:
        options = [
            (c.uid, c.definition.id) for p in ctx.state.players for c in p.battle_zone
            if is_creature_now(ctx.state, c) and can_be_chosen(ctx.state, c, ctx.me)
        ]
        return Ask(ctx.me, options, prompt="Choose a creature to shieldify") if options else None
    found = ctx.state.find_creature_anywhere(answer or "")
    if found:
        from core.zones import ShieldCard
        from engine.zone_mover import move_battle_to_zone
        owner = ctx.state.players[found[0]]
        move_battle_to_zone(
            ctx.state, found[0], answer, reason="shieldified", to_zone="shield_zone",
            place=lambda definition, uid: owner.shield_zone.append(
                ShieldCard(definition=definition, uid=uid)),
        )


def _step_destroy_own(ctx, params, answer):
    """The player named by the trigger destroys one of their own creatures."""
    player = ctx.trigger.trigger_data["player"]
    if answer is _NO_ANSWER:
        options = [
            (c.uid, c.definition.id) for c in ctx.state.players[player].battle_zone
            if is_creature_now(ctx.state, c) and c.can_be_destroyed()
        ]
        return Ask(player, options, prompt="Destroy one of your creatures") if options else None
    if answer:
        _destroy(ctx.state, answer)


def _step_may_play_this(ctx, params, answer):
    """Offer to play the card holding this ability from hand, for free."""
    p_state = ctx.state.players[ctx.me]
    card = p_state.find_in_hand(ctx.trigger.source_uid)
    if card is None:
        return None
    definition = card.definition
    if definition.is_spell():
        legal = _castable(ctx, definition)
    else:
        # One copy only: a second copy offered the same turn must check again
        legal = (may_enter(ctx.state, ctx.me, from_hand=True, played=True)
                 and not any(c.definition.slug == definition.slug for c in p_state.battle_zone))
    if answer is _NO_ANSWER:
        if not legal:
            return None
        return Ask(ctx.me, [(card.uid, definition.id)], optional=True,
                   prompt=f"Play {definition.name} for no cost?")
    if answer and legal:
        from engine.shield_break_window import execute_free_from_hand
        execute_free_from_hand(ctx.state, ctx.me, card.uid, reason="cast")


def _step_finish_spell(ctx, params, answer):
    from engine.shield_break_window import finish_spell
    finish_spell(ctx.state, ctx.me, ctx.trigger.source_uid)


def _step_g_strike(ctx, params, answer):
    if answer is _NO_ANSWER:
        targets = _opposing_targets(ctx)
        return Ask(ctx.me, targets, prompt="Choose a creature that can't attack") if targets else None
    found = ctx.state.find_creature_anywhere(answer or "")
    if found:
        found[1].set_flag("cannot_attack")  # cleared as the turn ends


def _step_decide_leave(ctx, params, answer):
    """The controller picks one replacement, or lets the creature leave (101.5b)."""
    from engine import leave_rules, zone_mover

    state, event = ctx.state, ctx.trigger.trigger_data["leave"]
    player = event["player"]
    creature = state.players[player].find_creature(event["uid"])
    if creature is None:
        return None
    creature.temp_flags.pop(leave_rules.LEAVING_FLAG, None)
    options = leave_rules.leave_options(
        state, player, creature, reason=event["reason"], to_zone=event["to_zone"],
        used=event["used"])
    compulsory = any(option.mandatory for option in options)

    if answer is _NO_ANSWER and options:
        choices = [(o.id, state.players[player].find_creature(o.source_uid).id) for o in options]
        if not compulsory:
            choices.append(("leave", None))
        creature.temp_flags[leave_rules.LEAVING_FLAG] = True
        return Ask(player, choices, prompt=f"{creature.definition.name} would leave. Replace it?")

    chosen = next((o for o in options if o.id == answer), None)
    if chosen is None:
        zone_mover.leave_battle_zone(
            state, player, creature, reason=event["reason"], to_zone=event["to_zone"])
        return None
    if chosen.mandatory:
        leave_rules.apply_simple(state, player, creature, chosen, event)
        return None

    # Each replacement applies only once to the chain of events it starts (609.3)
    used = (*event["used"], chosen.id)
    rule = LEAVE_REPLACEMENTS[state.players[player].find_creature(chosen.source_uid).definition.slug]
    if chosen.kind == "saver":
        ctx.then(("destroy_instead", {"uid": chosen.source_uid, "used": used}))
    elif chosen.kind == "destroy_other":
        ctx.then(("choose_destroy_instead", {"saved": creature.uid, "used": used}))
    elif chosen.kind == "discard":
        ctx.then(("discard_own", {"count": rule["count"]}))
    elif chosen.kind == "mana":
        ctx.then(("mana_to_graveyard", {"count": rule["count"]}))
    elif chosen.kind == "destroy_face_up":
        ctx.then(("destroy_face_up", {
            "count": rule["count"], "chosen": [], "keep": creature.uid, "used": used}))
    elif chosen.kind == "shuffle_grave":
        ctx.then(("shuffle_from_graveyard", {"count": rule["count"], "chosen": []}))


def _destroy_for_replacement(state: GameState, uid: str, used) -> None:
    from engine.zone_mover import move_battle_to_graveyard
    found = state.find_creature_anywhere(uid)
    if found is not None:
        move_battle_to_graveyard(state, found[0], uid, reason="effect", replacements_used=used)


def _step_destroy_instead(ctx, params, answer):
    _destroy_for_replacement(ctx.state, params["uid"], params["used"])


def _step_choose_destroy_instead(ctx, params, answer):
    if answer is _NO_ANSWER:
        options = [
            (c.uid, c.definition.id) for c in ctx.state.players[ctx.me].battle_zone
            if c.uid != params["saved"] and c.definition.card_type == CardType.CREATURE
            and not c.temp_flags.get("leaving") and c.can_be_destroyed()
        ]
        return Ask(ctx.me, options, prompt="Destroy another of your creatures") if options else None
    _destroy_for_replacement(ctx.state, answer, params["used"])


def _step_mana_to_graveyard(ctx, params, answer):
    p_state = ctx.state.players[ctx.me]
    if answer is _NO_ANSWER:
        options = [(m.uid, m.definition.id) for m in p_state.mana_zone]
        if params["count"] <= 0 or not options:
            return None
        return Ask(ctx.me, options, prompt="Put a card from your mana zone into your graveyard")
    mana = next((m for m in p_state.mana_zone if m.uid == answer), None)
    if mana is not None:
        p_state.mana_zone.remove(mana)
        _to_graveyard(ctx.state, ctx.me, mana.definition, mana.uid, "effect")
        ctx.then(("mana_to_graveyard", {"count": params["count"] - 1}))


def _step_shuffle_from_graveyard(ctx, params, answer):
    p_state = ctx.state.players[ctx.me]
    chosen = params["chosen"]
    if answer is not _NO_ANSWER:
        chosen = [*chosen, answer]
        ctx.queue[0] = ("shuffle_from_graveyard", {**params, "chosen": chosen})
    if len(chosen) < params["count"]:
        options = [(c.uid, c.definition.id) for c in p_state.graveyard if c.uid not in chosen]
        if options:
            return Ask(ctx.me, options, prompt="Choose a card to return to your deck")
    cards = [c for c in p_state.graveyard if c.uid in chosen]
    rng = random.Random(f"{ctx.state.game_id}:{ctx.state.turn_number}:{ctx.trigger.source_uid}")
    rng.shuffle(cards)
    for card in cards:
        p_state.graveyard.remove(card)
        p_state.deck.append(card.definition)


def _step_destroy_face_up(ctx, params, answer):
    """Choose all the face-up cards first, then destroy them together."""
    from engine.leave_rules import face_up_cards

    chosen = params["chosen"]
    if answer is not _NO_ANSWER:
        chosen = [*chosen, answer]
        ctx.queue[0] = ("destroy_face_up", {**params, "chosen": chosen})
    if len(chosen) < params["count"]:
        options = [
            (c.uid, c.definition.id)
            for c in face_up_cards(ctx.state, ctx.me, keep=params["keep"]) if c.uid not in chosen
        ]
        if options:
            return Ask(ctx.me, options, prompt="Choose a face-up card to destroy")
    zone = ctx.state.players[ctx.me].shield_zone
    for uid in chosen:
        shield = next((s for s in zone if s.uid == uid), None)
        if shield is not None:
            zone.remove(shield)
            _to_graveyard(ctx.state, ctx.me, shield.definition, shield.uid, "destroyed")
        else:
            _destroy_for_replacement(ctx.state, uid, params["used"])


def _step_decide_loss(ctx, params, answer):
    from engine.leave_rules import declare_loss, face_up_cards

    p_state = ctx.state.players[ctx.me]
    count = ctx.trigger.trigger_data["count"]
    source = p_state.find_creature(ctx.trigger.source_uid)
    possible = source is not None and len(face_up_cards(ctx.state, ctx.me)) >= count
    if answer is _NO_ANSWER and possible:
        return Ask(ctx.me, [("replace", source.id), ("lose", None)],
                   prompt=f"Destroy {count} face-up cards instead of losing?")
    if answer == "replace" and possible:
        # The loss stays dealt with until the cards are gone; then it is checked again
        ctx.then(("destroy_face_up", {"count": count, "chosen": [], "keep": None, "used": ()}),
                 ("loss_replaced", {}))
    else:
        p_state.loss_pending = False
        declare_loss(ctx.state, ctx.me)


def _step_loss_replaced(ctx, params, answer):
    ctx.state.players[ctx.me].loss_pending = False


def _step_last_burst(ctx, params, answer):
    """Cast the spell side of the card, wherever the creature went."""
    from engine.shield_break_window import queue_spell_effects, spell_side

    p_state = ctx.state.players[ctx.me]
    for zone in (p_state.graveyard, p_state.hand, p_state.mana_zone):
        card = next((c for c in zone if c.uid == ctx.trigger.source_uid), None)
        if card is not None:
            break
    else:
        return None
    face = spell_side(card.definition)
    if face is None or not _castable(ctx, face):
        return None
    if answer is _NO_ANSWER:
        return Ask(ctx.me, [(card.uid, card.definition.id)], optional=True,
                   prompt="Cast the spell side for no cost?")
    if answer:
        zone.remove(card)
        queue_spell_effects(ctx.state, ctx.me, card.uid, face, card_definition=card.definition)


def _step_denjara_switch(ctx, params, answer):
    found = ctx.state.find_creature_anywhere(ctx.trigger.source_uid)
    if found is None or found[1].field_orientation != "upright":
        return
    found[1].field_orientation = "upside_down"  # it stays that way (rule 105.5)
    _step_tap_all_opposing(ctx, {}, answer)


def _step_shield_to_hand(ctx, params, answer):
    """Add one of your shields to your hand, without its Shield Trigger."""
    from core.zones import HandCard

    p_state = ctx.state.players[ctx.me]
    if answer is _NO_ANSWER:
        # Face-down shields are not shown to the chooser
        options = [(s.uid, s.definition.id if s.is_face_up else None)
                   for s in p_state.shield_zone]
        return Ask(ctx.me, options, prompt="Add one of your shields to your hand") if options else None
    shield = next((s for s in p_state.shield_zone if s.uid == answer), None)
    if shield is None:
        return
    p_state.shield_zone.remove(shield)
    if shield.is_face_up and shield.definition.card_subtype == CardSubtype.G_CASTLE:
        _to_graveyard(ctx.state, ctx.me, shield.definition, shield.uid, "effect")  # rule 822.3
    else:
        p_state.hand.append(HandCard(definition=shield.definition, uid=shield.uid))


_HANDLERS = {
    "denjara_switch": _step_denjara_switch,
    "shield_to_hand": _step_shield_to_hand,
    "destroy_face_up": _step_destroy_face_up,
    "decide_loss": _step_decide_loss,
    "loss_replaced": _step_loss_replaced,
    "last_burst": _step_last_burst,
    "metamorph_mana": _step_metamorph_mana,
    "untap_mana": _step_untap_mana,
    "tap_all_opposing": _step_tap_all_opposing,
    "destroy_all_without_race": _step_destroy_all_without_race,
    "bounce_all_opposing": _step_bounce_all_opposing,
    "put_dragon_from_hand": _step_put_dragon_from_hand,
    "reveal_dragons_to_mana": _step_reveal_dragons_to_mana,
    "reveal_creatures_to_hand": _step_reveal_creatures_to_hand,
    "mystery_cube": _step_mystery_cube,
    "put_instead_of_discard": _step_put_instead_of_discard,
    "cast_discarded": _step_cast_discarded,
    "put_under_from_hand": _step_put_under_from_hand,
    "deck_to_shield": _step_deck_to_shield,
    "decide_leave": _step_decide_leave,
    "destroy_instead": _step_destroy_instead,
    "choose_destroy_instead": _step_choose_destroy_instead,
    "mana_to_graveyard": _step_mana_to_graveyard,
    "shuffle_from_graveyard": _step_shuffle_from_graveyard,
    "finish_spell": _step_finish_spell,
    "g_strike": _step_g_strike,
    "play_from_graveyard": _step_play_from_graveyard,
    "mill_up_to": _step_mill_up_to,
    "deck_to_mana": _step_deck_to_mana,
    "mana_to_hand": _step_mana_to_hand,
    "discard_own": _step_discard_own,
    "shieldify_creature": _step_shieldify_creature,
    "destroy_own": _step_destroy_own,
    "may_play_this": _step_may_play_this,
    "cast_then_bottom": _step_cast_then_bottom,
    "self_to_mana": _step_self_to_mana,
    "mana_or_leave": _step_mana_or_leave,
    "gachinko_extra_turn": _step_gachinko_extra_turn,
    "name_card": _step_name_card,
    "return_spells": _step_return_spells,
    "cast_from_hand": _step_cast_from_hand,
    "tap_opposing": _step_tap_opposing,
    "next_creature_discount": _step_next_creature_discount,
    "untap_self": _step_untap_self,
    "draw": _step_draw,
    "mill": _step_mill,
    "may": _step_may,
    "random_discard": _step_random_discard,
    "opponent_discards": _step_opponent_discards,
    "discard_then_random": _step_discard_then_random,
    "debuff": _step_debuff,
    "lock": _step_lock,
    "destroy": _step_destroy,
    "reanimate": _step_reanimate,
    "modes": _step_modes,
    "look_top": _step_look_top,
    "opponent_hand_to_mana": _step_opponent_hand_to_mana,
    "once_per_turn": _step_once_per_turn,
    "destroy_self_unless_shields_broken": _step_destroy_self_unless_shields_broken,
    "babelginus": _step_babelginus,
    "keep_shields": _step_keep_shields,
}
