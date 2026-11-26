"""
core/card_scripts.py — hand-written abilities for cards the parsed format cannot express.

A scripted card's abilities replace its parsed effects. Each ability is a list of
steps; a step is (operation, parameters) and is run by engine/card_script_runner.py.
Steps are plain data, so a game paused on a player choice can still be copied.

Abilities that are not implemented yet are listed in UNSUPPORTED. They do nothing,
which is safer than running a wrong parsed effect. Cards are keyed by database slug.

This module holds data only and must not import the engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.cards import CardEffect
from core.enums import (
    INFINITY, Civilization, EffectAction, EffectType, Keyword, TriggerEvent,
)

PUT = TriggerEvent.ON_ENTER_BATTLE_ZONE
ATTACK = TriggerEvent.ON_ATTACK
DESTROYED = TriggerEvent.ON_DESTROY
CAST = TriggerEvent.ON_CAST
SUMMON = TriggerEvent.ON_SUMMON
TURN_END = TriggerEvent.END_OF_TURN
# Not a game event the trigger registry knows: the engine queues it by its key
EX_LIFE_SHIELD_LEFT = TriggerEvent.NONE
EX_LIFE_SHIELD_LEFT_KEY = "ex_life_shield_left"


@dataclass(frozen=True)
class Ability:
    key: str
    event: TriggerEvent
    steps: tuple[tuple[str, dict], ...]
    text: str
    # Whose event runs it: "self" (this card), "own_abyss" (any of your Abyss
    # creatures), "own_dragon_summon", "own_turn_end", "own_cast" (any spell you cast while this is in the battle zone) or
    # "own_turn_end_after_cast" (the end of your turn, if you cast a spell that turn)
    watch: str = "self"


def _draw(count, who="self"):
    return ("draw", {"count": count, "who": who})


def _mill(count):
    return ("mill", {"count": count})


def _reanimate(max_cost, kind="creature", optional=False):
    return ("reanimate", {"max_cost": max_cost, "kind": kind, "optional": optional})


def _to_mana(count=1, tapped=False):
    return ("deck_to_mana", {"count": count, "tapped": tapped})


def _debuff(amount):
    return ("debuff", {"amount": amount})


_MAY_MILL_2 = (("may", {"then": [_mill(2)], "prompt": "Put the top 2 cards into your graveyard?"}),)

ATER_MODES = {
    "mode:weaken": [_debuff(-4000)],
    "mode:mill": [_mill(4)],
    "mode:reanimate": [_reanimate(4)],
}

TRIPLE_THREE_MODES = {
    "mode:tap": [("tap_opposing", {"left": 3})],
    "mode:draw": [_draw(3)],
    "mode:discount": [("next_creature_discount", {"amount": 3})],
}


PETENCY_MODES = {
    "mode:draw_discard": [_draw(2), ("discard_own", {"count": 1})],
    "mode:shield_trigger": [("play_from_graveyard",
                             {"max_cost": None, "kind": "shield_trigger", "optional": False})],
    "mode:shieldify": [("shieldify_creature", {})],
}

_ENDLESS_STEPS = (
    ("may", {"then": [_draw(1)], "prompt": "Draw a card?"}),
    ("put_under_from_hand", {}),
)

# Abilities the engine queues itself, found by key
QUEUED = TriggerEvent.NONE
FREE_PLAY_KEY = "free_play"
HAND_TURN_END_KEY = "from_hand_at_turn_end"
# A card with a Super Shield Trigger has an ability with this key
SUPER_SHIELD_TRIGGER_KEY = "super_shield_trigger"

_ZADAN_PLAY = (("play_from_graveyard", {
    "max_cost": "graveyard", "kind": "darkness", "optional": True,
    # "use its Super Shield Trigger ability as if you had no shields"
    "super_as_if_no_shields": True,
}),)

SCRIPTS: dict[str, tuple[Ability, ...]] = {
    # Not cards: effects the rules themselves create
    "_rules": (
        Ability("finish_spell", QUEUED, (("finish_spell", {}),),
                "The spell finishes resolving."),
        Ability("last_burst", QUEUED, (("last_burst", {}),),
                "You may cast this card's spell side for no cost."),
        Ability("decide_loss", QUEUED, (("decide_loss", {}),),
                "You would lose the game. Replace it?"),
        Ability("g_strike", QUEUED, (("g_strike", {}),),
                "G-Strike: choose an opposing creature. It can't attack this turn."),
        Ability("decide_leave", QUEUED, (("decide_leave", {}),),
                "Choose how this creature leaves."),
    ),
    # ── Deck 1 ──
    "Astral_Reef": (
        Ability("put", PUT, (("may", {"then": [_draw(3)], "prompt": "Draw 3 cards?"}),),
                "When you put this creature, you may draw 3 cards."),
    ),
    "Dragon%27s_Sign": (
        Ability("cast", CAST, (("put_dragon_from_hand", {"max_cost": 7}),),
                "Put a non-evolution Light Dragon costing 7 or less from your hand. It gets "
                "blocker until the start of your next turn."),
    ),
    "Holy_Awe": (
        Ability("cast", CAST, (("tap_all_opposing", {}),),
                "Tap all your opponent's creatures."),
    ),
    "Mariflo-1,_Viral": (),
    "T_Anago": (),
    # ── Deck 2 ──
    "Aegis_Boost,_Matchless_Dragon_Saint": (
        Ability("put", PUT, (_to_mana(),),
                "When you put this creature, put the top card of your deck into your mana zone."),
    ),
    "Bolbalzak_Ex": (
        Ability("put", PUT, (("untap_mana", {}),),
                "When you put this creature, untap all cards in your mana zone."),
    ),
    "Bolmeteus_Sapphire_Dragon": (),
    "Codeking_Mozart": (
        Ability("put", PUT, (("destroy_all_without_race", {"race": "Dragon"}),),
                "When you put this creature, destroy all non-Dragons."),
    ),
    "Mendelssohn": (
        Ability("cast", CAST, (("reveal_dragons_to_mana", {"count": 2}),),
                "Reveal your top 2 cards: Dragons into your mana zone tapped, the rest into your "
                "graveyard."),
    ),
    "Meteorite_Ryusei_the_Flash": (
        Ability("put", PUT, (("tap_all_opposing", {}),),
                "When you put this creature, tap all your opponent's creatures."),
    ),
    "Mystery_Cube": (
        Ability("cast", CAST, (("mystery_cube", {}),),
                "Shuffle, reveal the top card: you may put it if it is a creature, else it goes "
                "into your mana zone."),
    ),
    "Ryusei_Kaiser,_the_Eternal": (
        Ability("instead_of_discard", QUEUED, (("put_instead_of_discard", {}),),
                "When an opponent's effect would discard this, you may put it instead."),
    ),
    "Seventh_Tower": (
        Ability("cast", CAST, (("metamorph_mana", {"needed": 7, "normal": 1, "morphed": 3}),),
                "Put the top card of your deck into your mana zone; with 7 or more mana, the top 3."),
    ),
    "Shura_Beethoven,_Zenith_of_%22Hadou%22": (
        Ability("dragon_summon", SUMMON,
                (("may", {"then": [_to_mana(2)], "prompt": "Put your top 2 cards into mana?"}),),
                "Whenever you summon a Dragon, you may put the top 2 cards of your deck into your "
                "mana zone.", watch="own_dragon_summon"),
    ),
    "Teranesque,_Hard_Battling_Ancient_Dragon": (
        Ability("put", PUT, (("reveal_creatures_to_hand", {"left": 3}),),
                "Reveal your top 3: any creatures into your hand, the rest into mana tapped."),
    ),
    "Top_of_Romanesque": (
        Ability("put", PUT, (("may", {"then": [_to_mana(2, tapped=True)],
                                      "prompt": "Put your top 2 cards into mana tapped?"}),),
                "When you put this creature, you may put your top 2 cards into mana tapped."),
    ),
    "VAN_Beethoven,_Zenith_of_%22Shura%22": (
        Ability("summoned", SUMMON, (("bounce_all_opposing", {}),),
                "When you summon this creature, return all your opponent's creatures."),
    ),
    "Memento_Guardian_Shrine,_Prison_Court_of_D": (
        Ability("denjara_switch", QUEUED, (("may", {
                    "prompt": "Turn this D2 Field upside down and tap all opposing creatures?",
                    "then": [("denjara_switch", {})]}),),
                "When a player draws their first card on their turn, you may turn this upside "
                "down for the rest of the game; if you do, tap all opposing creatures."),
    ),
    # ── Deck 4 additions ──
    "Hustle_Castle,_Garde_Seventh_Floor": (
        Ability("face_up_self", QUEUED, (_to_mana(1, tapped=True),),
                "When this is put face up into your shields, put your top card into mana tapped."),
    ),
    "Endless_Justice_Romanov_Bible": (
        Ability("put", PUT, _ENDLESS_STEPS,
                "When you put this, you may draw; then you may put a card from hand under it."),
        Ability("turn_end", TURN_END, _ENDLESS_STEPS,
                "At the end of your turn, you may draw; then you may put a card from hand under it.",
                watch="own_turn_end"),
        Ability("left_under", QUEUED, (("modes", {"table": "endless", "count": 1, "chosen": []}),),
                "When a card leaves from under this: shieldify your top card, or destroy an "
                "opposing creature."),
    ),
    "Kuuzen,_Sinister_General_of_Thunderbolt_/_Dark_Life": (
        Ability("twin_spell", QUEUED,
                (("look_top", {"look": 2, "places": ["mana", "graveyard"]}),),
                "Dark Life: look at your top 2; one into mana, the other into your graveyard."),
    ),
    "Perfect_Petency": (
        Ability("cast", CAST, (("modes", {"table": "petency", "count": 2, "chosen": []}),),
                "Choose 2, the same one twice if you like: draw 2 and discard 1; play a shield "
                "trigger card from your graveyard; shieldify a creature."),
        Ability(HAND_TURN_END_KEY, QUEUED, (("may_play_this", {}),),
                "At the end of your opponent's turn, if 3 or more creatures were put, you may cast "
                "this for free."),
    ),
    "Romanov_the_4th,_Demonic_Eye_Gaia": (
        Ability("face_up_shield", QUEUED,
                (("destroy", {"max_cost": None, "optional": True}),),
                "When you put a card face up into your shields, you may destroy an opposing "
                "creature."),
        Ability("put", PUT, (("mill_up_to", {"count": 3}),
                             ("play_from_graveyard", {"max_cost": 4, "kind": "non_creature",
                                                      "optional": True})),
                "Put up to 3 top cards into your graveyard, then you may play a non-creature card "
                "costing 4 or less from it."),
    ),
    "Kaiser_Victory,_Secret_Extreme_Triumph": (
        Ability("cast", CAST, (("deck_to_mana", {}), ("mana_to_hand", {})),
                "Put the top card of your deck into your mana zone, then you may return a card from "
                "your mana zone to your hand."),
    ),
    "Just_In_You": (
        Ability("cast", CAST, (_mill(4), _reanimate(4, "abyss")),
                "Put the top 4 cards into your graveyard, then put an Abyss costing 4 or less from it."),
    ),
    "Seven%27s_Gate_Zadan,_Demon_King": (
        Ability("put", PUT, _ZADAN_PLAY,
                "When you put this, you may play a darkness card from your graveyard costing no more "
                "than the number of cards there."),
        Ability("attack", ATTACK, _ZADAN_PLAY,
                "When this attacks, you may play a darkness card from your graveyard costing no more "
                "than the number of cards there."),
    ),
    "Abyssal_Counterattack": (
        Ability("cast", CAST, (_debuff(-INFINITY),),
                "An opposing creature loses infinite power until the end of the turn."),
    ),
    "Magnum,_Allshot_Puppet": (
        Ability(FREE_PLAY_KEY, QUEUED, (("destroy_own", {}),),
                "Whoever puts a creature or cross gear, or casts a spell, without tapping mana "
                "destroys one of their creatures."),
    ),
    # Continuous abilities only: see the tables below
    "Spell_Del_Fin,_Light_Divine_Dragon": (),
    "Million_Vejita,_Tyrant_T": (),
    "Abyssbell_=_Jashin_Emperor": (),
    "The_Triple_Three": (
        Ability("cast", CAST, (("modes", {"table": "triple_three", "count": 1, "chosen": []}),),
                "Choose one: tap 3 opposing creatures; draw 3; your next creature this turn costs "
                "up to 3 less (not below 1) and gets speed attacker."),
    ),
    "Galingmar,_Hybrid_Starve_Mobile": (
        Ability("spell_cast", CAST, (("untap_self", {}),),
                "Whenever you cast a spell, untap this creature.", watch="own_cast"),
    ),
    "Bau_Mad_Romaion,_Concatenated_True_Wicked": (
        Ability("put", PUT, (("return_spells", {"left": 2}),),
                "When you put this creature, return up to 2 spells from your graveyard to your hand."),
        Ability("attack", ATTACK, (("cast_from_hand", {"max_cost": 8}),),
                "Whenever this attacks, you may cast a spell costing 8 or less from your hand for free."),
        Ability(EX_LIFE_SHIELD_LEFT_KEY, EX_LIFE_SHIELD_LEFT, (("cast_from_hand", {"max_cost": 8}),),
                "When this creature's EX Life shield leaves your shield zone, you may cast a spell "
                "costing 8 or less from your hand for free."),
    ),
    "Zardiclica,_Hybrid_Winds_Dragon": (
        Ability("put", PUT, (("cast_then_bottom", {"max_cost": 7}),),
                "When you put this creature, you may cast a spell costing 7 or less from your hand "
                "or graveyard for free, then put it on the bottom of your deck."),
        Ability("turn_end", TURN_END,
                (("destroy", {"max_cost": None, "max_power": 5500, "optional": True,
                              "then_if_destroyed": [_draw(1)]}),),
                "At the end of your turn, if you cast a spell that turn, you may destroy an opposing "
                "creature with power 5500 or less, then draw a card.",
                watch="own_turn_end_after_cast"),
    ),
    "Digorpion,_Ground_Digging_Snakekind": (
        Ability("put", PUT, (("mana_or_leave", {"race": "Dragon"}),),
                "If you have another Dragon, put the top card of your deck into your mana zone. "
                "Otherwise, put this creature into your mana zone."),
    ),
    "Onimaru_%22Head%22,_Victory_Rush": (
        Ability("attack", ATTACK, (("gachinko_extra_turn", {}),),
                "When this attacks, play Gachinko Judge; if you win, take an extra turn after this one."),
    ),
    "Mitsurugi_Boost,_Matchless_Dragon_Demon": (
        Ability("put", PUT, (("may", {
                    "prompt": "Put this creature into your mana zone?",
                    "then": [("self_to_mana", {"then": [
                        ("destroy", {"max_cost": None, "max_power": 6000, "optional": False})]})],
                }),),
                "You may put this into your mana zone. If you do, destroy an opposing creature with "
                "power 6000 or less."),
    ),
    "Emotional_Hardcore,_Sacred_Dragon": (
        Ability("put", PUT, (("name_card", {"except_name": "Emotional Hardcore, Sacred Dragon"}),),
                "Choose a creature card name; abilities of creatures with that name are ignored."),
    ),
    # Their only ability is continuous: see PUT_TRIGGER_SUPPRESSORS
    "Wald_Brachio,_Absolute_World_King": (),
    "Brachiodrange": (),
    "Oa:Nateha": (
        Ability("put", PUT, (("random_discard", {"count": 1}),),
                "When you put this creature, discard a card at random from your opponent's hand."),
        Ability("destroyed", DESTROYED, (_draw(1, "opponent"),),
                "When this creature is destroyed, your opponent draws a card."),
    ),
    "Kawagutsu_=_Aguus": (
        Ability("grave_left", QUEUED, (("may", {
                    "prompt": "Put the top 3 cards of your deck into your graveyard?",
                    "then": [_mill(3)]}),),
                "Each turn, when a card first leaves your graveyard, you may mill 3."),
        Ability("put", PUT, (_debuff(-2000),),
                "When you put this creature, an opposing creature gets -2000 power this turn."),
    ),
    "Cobra,_Snake_Ninja_Holy_Chaos": (
        Ability("put", PUT, (_mill(2), _reanimate(5), ("destroy_self_unless_shields_broken", {"count": 2})),
                "Mill 2, put a creature costing 5 or less from your graveyard. Put on the opponent's "
                "turn, destroy this unless 2 or more of your shields were broken this turn."),
    ),
    "Ater_Gorghini": (
        Ability("put", PUT, (("modes", {"table": "ater", "count": 2, "chosen": []}),),
                "Choose 2: -4000 power; mill 4; put a creature costing 4 or less from your graveyard."),
    ),
    "Disaster_Tune": (
        Ability("cast", CAST, (("look_top", {"look": 4, "places": ["hand", "mana", "graveyard"]}),),
                "Look at your top 4: one each to hand, mana, graveyard and the bottom of your deck."),
    ),
    "Broom_=_Proof": (
        Ability("put", PUT, (_mill(1),), "When you put this creature, put the top card into your graveyard."),
    ),
    "Kernel,_Blue_Stagnation_Dragon_Elemental_/_Silent_Jealousy": (
        Ability("twin_spell", QUEUED,
                (("random_discard", {"count": 1}), ("shield_to_hand", {})),
                "Silent Jealousy: your opponent discards a card at random; add one of your "
                "shields to your hand without using its Shield Trigger."),
        Ability("put", PUT, (("lock", {}),),
                "An opposing creature can't attack or block until the start of your next turn."),
    ),
    "Gaiash_Kaiser,_the_Meteor": (
        Ability("put", PUT, (_draw(2),), "When you put this creature, draw 2 cards."),
        Ability(HAND_TURN_END_KEY, QUEUED, (("may_play_this", {}),),
                "At the end of your opponent's turn, if they played something without tapping mana "
                "and you have no copy of this, you may summon it for free."),
    ),
    "Jashin_Emperor,_Abyssal_Darkness": (
        Ability("put", PUT, (_mill(1), ("destroy", {"max_cost": "graveyard", "optional": False, "tamaseeds": True})),
                "Mill 1, then destroy an opposing card costing no more than your graveyard size."),
        Ability("attack", ATTACK, (_reanimate(6, "non_evolution", optional=True),),
                "When this attacks, you may put a non-evolution creature costing 6 or less from your graveyard."),
    ),
    "Gotsunma_=_Dhamma,_Soul_Abyss": (
        Ability("attack", ATTACK, (("opponent_discards", {"count": 2}),),
                "When this attacks, your opponent chooses and discards 2 cards.",
                watch="self_hyper"),
    ),
    "Gigandydanos,_Big_Tree_King": (
        Ability("put", PUT, (("opponent_hand_to_mana", {}),),
                "When you put this creature, your opponent puts their hand into their mana zone."),
    ),
    "Jablood,_Wicked_Dragon": (
        Ability("put", PUT, _MAY_MILL_2, "When you put this, you may mill 2."),
        Ability("abyss_attack", ATTACK, _MAY_MILL_2, "When your Abyss attacks, you may mill 2.",
                watch="own_abyss"),
    ),
    "Jabibirublood,_Wicked_Soul_Dragon": (
        Ability("first_abyss_attack", ATTACK,
                (("once_per_turn", {"flag": "first_abyss_attack"}), _mill(2),
                 _reanimate(3, "abyss", optional=True)),
                "The first time your Abyss attacks each turn: mill 2, then you may put an Abyss "
                "costing 3 or less from your graveyard.", watch="own_abyss_hyper"),
    ),
    "Felix_Misery": (
        Ability("attack", ATTACK, (_mill(2), _reanimate(6, "non_evolution_darkness", optional=True)),
                "When this attacks, mill 2, then you may put a non-evolution darkness creature "
                "costing 6 or less from your graveyard."),
    ),
    "Babelginus,_Demonic_Dragon": (
        Ability("put", PUT, (("babelginus", {"except_name": "Babelginus, Demonic Dragon"}),),
                "You may destroy a creature; if you do, its owner's graveyard returns a "
                "non-evolution creature to the battle zone."),
    ),
    "CRYMAX_Jaouga": (
        Ability("put", PUT, (("keep_shields", {"keep": 3, "seat": "self"}),
                             ("keep_shields", {"keep": 3, "seat": "opponent"})),
                "Each player keeps 3 shields; the rest go to the graveyard."),
        Ability("attack", ATTACK, (("destroy", {"max_cost": None, "optional": False}),
                                   ("random_discard", {"count": 2})),
                "When this attacks, destroy an opposing creature, then they discard 2 at random."),
    ),
    "Saga_In_Zenaku": (
        Ability("opponent_discarded_spell", QUEUED, (("cast_discarded", {}),),
                "When your opponent discards a spell, you may cast it for free."),
        Ability("put", PUT, (_draw(3), ("discard_then_random", {"left": 2, "discarded": 0})),
                "Draw 3, discard up to 2; the opponent discards that many at random."),
    ),
}

ENDLESS_MODES = {
    "mode:shieldify_top": [("deck_to_shield", {})],
    "mode:destroy": [("destroy", {"max_cost": None, "optional": False})],
}


def _mode_tables():
    return {"ater": ATER_MODES, "triple_three": TRIPLE_THREE_MODES, "petency": PETENCY_MODES,
            "endless": ENDLESS_MODES}


MODE_TABLES = _mode_tables()

# Continuous abilities, checked by the engine where the rule applies
OPPONENT_HAND_REVEALED = frozenset({"Spell_Del_Fin,_Light_Divine_Dragon"})
OPPONENT_CANT_CAST = frozenset({"Spell_Del_Fin,_Light_Divine_Dragon"})
POWER_PER_OPPONENT_SPELL = {"Spell_Del_Fin,_Light_Divine_Dragon": 2000}
BREAKER_COUNTS = {"Brachiodrange": 4, "Million_Vejita,_Tyrant_T": 4}
# "You can't be attacked by creatures with less power than this creature"
WEAKER_CANT_ATTACK_YOU = frozenset({"Gigandydanos,_Big_Tree_King"})
# "Your opponent's creatures can't attack you the turn they are put"
NEW_CREATURES_CANT_ATTACK_YOU = frozenset({"Gaiash_Kaiser,_the_Meteor"})
# Your creatures with this cost or more cost less to summon: (minimum cost, reduction)
BIG_SUMMON_DISCOUNT = {"Gaiash_Kaiser,_the_Meteor": (10, 4)}
# This card costs less while your graveyard is large: (cards needed, reduction)
GRAVEYARD_SELF_DISCOUNT = {"Seven%27s_Gate_Zadan,_Demon_King": (7, 7)}
OPPONENT_MANA_ENTERS_TAPPED = frozenset({"Gotsunma_=_Dhamma,_Soul_Abyss"})
# Your creatures put from the graveyard have no summoning sickness that turn
GRAVEYARD_CREATURES_RUSH = frozenset({"Jabibirublood,_Wicked_Soul_Dragon"})
# Not a creature without 4 darkness creatures and/or tamaseeds
CREATURE_ONLY_WITH_DARKNESS = {
    "Jashin_Emperor,_Abyssal_Darkness": 4,
    "Jablood,_Wicked_Dragon": 4,
}
DESTROYS_UNSUMMONED = frozenset({"Broom_=_Proof"})
ELEMENTS_ONLY_PLAYED_FROM_HAND = frozenset({"Million_Vejita,_Tyrant_T"})
# Playable from hand as the opponent's turn ends
HAND_AT_OPPONENT_TURN_END = {
    "Perfect_Petency": {"kind": "creatures_put", "count": 3, "ability": HAND_TURN_END_KEY},
    "Gaiash_Kaiser,_the_Meteor": {"kind": "opponent_played_free", "ability": HAND_TURN_END_KEY},
}

# ── Ways to summon ────────────────────────────────────────────────────────
# Summoning from these zones costs mana as usual
SUMMON_FROM_GRAVEYARD = frozenset({"Felix_Misery", "Seven%27s_Gate_Zadan,_Demon_King"})
SUMMON_FROM_MANA = frozenset({"Million_Vejita,_Tyrant_T"})
# While this is in the battle zone, your creatures of the race may be summoned from mana
SUMMON_FROM_MANA_GRANTS = {"Shura_Beethoven,_Zenith_of_%22Hadou%22": "Dragon"}
# Abyss Rush: summon from the graveyard, no summoning sickness, and put on the
# bottom of the deck at the end of the turn. Granted to your creatures of the race.
ABYSS_RUSH_GRANTS = {"Abyssbell_=_Jashin_Emperor": "Abyss"}
# Your creatures cost this much less to summon from the graveyard (not below 1)
GRAVEYARD_SUMMON_DISCOUNT = {"Abyssbell_=_Jashin_Emperor": 2}
# Neo and G-Neo creatures may be summoned as they are, or on one of your creatures
# sharing a listed civilization, which makes them Evolution Creatures. G-Neo
# differs by allowing more kinds of base.
_G_NEO_BASES = frozenset({Civilization.LIGHT, Civilization.WATER, Civilization.DARKNESS})
NEO_EVOLUTION = {
    "Felix_Misery": {"civilizations": frozenset({Civilization.DARKNESS}), "discount": 2},
    "Saga_In_Zenaku": {"civilizations": _G_NEO_BASES, "discount": 0},
    "Endless_Justice_Romanov_Bible": {"civilizations": _G_NEO_BASES, "discount": 0},
}

# More continuous abilities
PUT_TRIGGER_SUPPRESSORS = frozenset({"Wald_Brachio,_Absolute_World_King", "Brachiodrange"})
ENTERS_TAPPED = frozenset({"Galingmar,_Hybrid_Starve_Mobile"})
# EX Life: "As you put this creature, shieldify the top card of your deck. When this
# creature would leave, put that shield into your graveyard instead."
EX_LIFE = frozenset({
    "Galingmar,_Hybrid_Starve_Mobile",
    "Bau_Mad_Romaion,_Concatenated_True_Wicked",
    "Zardiclica,_Hybrid_Winds_Dragon",
})
# "While attacking, this creature gets +N power"
POWER_ATTACKERS = {
    "Onimaru_%22Head%22,_Victory_Rush": 5000,
    "Bolmeteus_Sapphire_Dragon": 3000,
}
# Creatures of the name these chose have their abilities ignored
NAMES_TO_IGNORE = frozenset({"Emotional_Hardcore,_Sacred_Dragon"})
# Opponents can't choose these while their controller has another creature of the race
CHOICE_PROTECTION = {"Emotional_Hardcore,_Sacred_Dragon": "Oracle"}
DOES_NOT_UNTAP = frozenset({"Galingmar,_Hybrid_Starve_Mobile"})

# Printed abilities these scripts do not cover yet
UNSUPPORTED: dict[str, tuple[str, ...]] = {
}

# Keywords given while the source is in the battle zone:
# slug -> ((whose creatures: "own" | "opponent" | "self", race or None, keywords), ...)
GRANTS = {
    "Memento_Guardian_Shrine,_Prison_Court_of_D": (("own", None, (Keyword.BLOCKER,)),),
    "Meteorite_Ryusei_the_Flash": (("own", None, (Keyword.SPEED_ATTACKER,)),),
    "Ryusei_Kaiser,_the_Eternal": (("own", None, (Keyword.SPEED_ATTACKER,)),),
    "Shura_Beethoven,_Zenith_of_%22Hadou%22": (
        ("own", "Dragon", (Keyword.SPEED_ATTACKER, Keyword.SLAYER)),),
    "Codeking_Mozart": (("opponent", "Dragon", (Keyword.CANNOT_ATTACK,)),),
    "Mariflo-1,_Viral": (("self", None, (Keyword.CANNOT_ATTACK,)),),
    "T_Anago": (("self", None, (Keyword.CANNOT_ATTACK,)),),
}
# "Your opponent's creatures are put tapped"
OPPONENT_CREATURES_ENTER_TAPPED = frozenset({"Ryusei_Kaiser,_the_Eternal"})
# "Your opponent would put a command or dragon creature: into their graveyard instead"
OPPONENT_RACES_TO_GRAVEYARD = {"VAN_Beethoven,_Zenith_of_%22Shura%22": ("Command", "Dragon")}
# Face up in the shields: opposing creatures not summoned go to the mana zone instead
UNSUMMONED_TO_MANA = frozenset({"Hustle_Castle,_Garde_Seventh_Floor"})
# Sasagale N: while summoning a creature of the race, you may destroy this to
# reduce its cost by N (not to 0 or less)
SASAGALE = {"Mariflo-1,_Viral": (1, "Dispector")}
# Super Soul X (rule 818): the ability also belongs to a creature this is under
SUPER_SOUL = {"Kawagutsu_=_Aguus": "grave_left"}
# Evolution cards that must go on a creature of one race
EVOLUTION_BASE_RACE = {"Astral_Reef": "Cyber Virus"}

# ── Ways to use a card ──
# Attack Chance: free when a matching creature of yours attacks
ATTACK_CHANCE = {"Kaiser_Victory,_Secret_Extreme_Triumph": (Civilization.NATURE, 4000)}
# Recycle: cast from the graveyard for this cost, then to the bottom of the deck
RECYCLE = {"Kaiser_Victory,_Secret_Extreme_Triumph": 4}
# Counterattack: free when attacked; pay this at your next turn or lose
COUNTERATTACK = {"Abyssal_Counterattack": (3, Civilization.DARKNESS)}
# Civil Count: gets Shield Trigger with this many creatures/Tamaseeds of a civilization
CIVIL_COUNT_SHIELD_TRIGGER = {"Just_In_You": (Civilization.DARKNESS, 2)}
# Fushigiverse: tap one of your creatures into mana, summon from the graveyard for
# (base - that creature's cost), not below the minimum
_DARK_NATURE = frozenset({Civilization.DARKNESS, Civilization.NATURE})
FUSHIGIVERSE = {
    "Brachiodrange": (13, 1, frozenset({Civilization.NATURE})),
    "Gigandydanos,_Big_Tree_King": (14, 2, _DARK_NATURE),
    "Kuuzen,_Sinister_General_of_Thunderbolt_/_Dark_Life": (8, 2, _DARK_NATURE),
}
# The spell side of a Twinpact card: (cost, civilizations)
TWINPACT_SPELLS = {
    "Kuuzen,_Sinister_General_of_Thunderbolt_/_Dark_Life": (2, _DARK_NATURE),
    "Kernel,_Blue_Stagnation_Dragon_Elemental_/_Silent_Jealousy": (
        2, frozenset({Civilization.DARKNESS})),
}

# Optional "instead" abilities. "scope" is "self", or "own" for any of your
# creatures; "race" limits which ones. See engine/leave_rules.py.
LEAVE_REPLACEMENTS = {
    "Ater_Gorghini": {"kind": "destroy_other", "scope": "self"},
    "CRYMAX_Jaouga": {"kind": "destroy_face_up", "count": 3, "scope": "self"},
    "Abyssbell_=_Jashin_Emperor": {"kind": "discard", "count": 2, "scope": "self"},
    "Million_Vejita,_Tyrant_T": {"kind": "mana", "count": 3, "scope": "own"},
    "Jablood,_Wicked_Dragon": {"kind": "shuffle_grave", "count": 4, "scope": "own", "race": "Abyss"},
    "T_Anago": {"kind": "saver", "scope": "own", "civilization": Civilization.WATER},
}
LOSS_REPLACEMENTS = {"CRYMAX_Jaouga": 3}
# Eternal Omega: "When this creature would leave, return it to your hand instead."
ETERNAL_OMEGA = frozenset({"VAN_Beethoven,_Zenith_of_%22Shura%22"})

# Hyper Mode (rule 816). "Hyperify" is paid by tapping other creatures; while
# released, the creature has the hyper power, breaker and abilities.
HYPER_MODE = {
    "Gotsunma_=_Dhamma,_Soul_Abyss": {"power": 6000, "breaker": 2},
    "Jabibirublood,_Wicked_Soul_Dragon": {"power": 10000, "breaker": 2},
}
# Keywords the stored card data is missing
EXTRA_KEYWORDS = {
    "Jabibirublood,_Wicked_Soul_Dragon": (Keyword.BLOCKER,),
    "Kuuzen,_Sinister_General_of_Thunderbolt_/_Dark_Life": (
        Keyword.MACH_FIGHTER, Keyword.SLAYER, Keyword.DOUBLE_BREAKER, Keyword.LAST_BURST),
    "Brachiodrange": (),
}
# "Whenever this would break a shield, your opponent puts it into their graveyard instead"
BREAKS_TO_GRAVEYARD = frozenset({"Bolmeteus_Sapphire_Dragon"})

# Printed Evolution cards whose card type the loader does not recognize as Evolution
EVOLUTION_SLUGS = frozenset({"CRYMAX_Jaouga"})


def scripted_effects(slug: str, card_id: int) -> tuple[CardEffect, ...] | None:
    """Effects that replace the parsed ones for this card, or None if it is not scripted."""
    abilities = SCRIPTS.get(slug)
    if abilities is None:
        return None
    effects = []
    for index, ability in enumerate(abilities):
        effects.append(
            CardEffect(
                card_id=card_id,
                ability_index=index,
                raw_text=ability.text,
                # A spell's own text is a SPELL effect; watching other spells is a trigger
                effect_type=(
                    EffectType.SPELL if ability.event == CAST and ability.watch == "self"
                    else EffectType.TRIGGERED
                ),
                trigger_event=ability.event,
                effect_action=EffectAction.NONE,
                trigger_condition={},
                effect_target={},
                effect_value={"script": f"{slug}#{ability.key}", "watch": ability.watch},
                is_optional=False,
                is_replacement=False,
                active_in_phase=("any",),
                active_in_zone=("battle_zone",),
                parse_confidence=1.0,
            )
        )
    return tuple(effects)


def script_steps(script_id: str) -> list[tuple[str, dict]]:
    slug, key = script_id.rsplit("#", 1)
    return list(next(a for a in SCRIPTS[slug] if a.key == key).steps)
