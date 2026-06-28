"""core/actions/constructors.py — Action constructor functions.

One constructor function per action type, enforcing correct field population.
Always use these, never Action() directly.

Implements rules: 101.2, 112.2a, 112.3, 301.5, 405.1, 503.1, 504.1, 506.1,
506.3, 507.2, 509.1, 509.2, 112.3c.
"""

from __future__ import annotations

from ..enums import ActionType, Civilization, ManaUsage

from .base import Action


# ─────────────────────────────────────────────────────────────────────────────
# Constructor functions — one per action type
# Enforces correct field population. Always use these, never Action() directly.
# ─────────────────────────────────────────────────────────────────────────────


# ── Mana Charge Step (rule 503) ───────────────────────────────────────────────

def charge_mana(player: int, card_uid: str, card_id: int) -> Action:
    """
    Rule 503.1: Place one card from hand into mana zone face-down.
    Only one card may be charged per turn.
    Multi-colored cards enter TAPPED (rule 405.1) — handled by executor.
    """
    return Action(
        player=player,
        action_type=ActionType.CHARGE_MANA,
        card_uid=card_uid,
        card_id=card_id,
    )


def pass_charge(player: int) -> Action:
    """Rule 503.1: Player chooses not to charge mana this turn."""
    return Action(
        player=player,
        action_type=ActionType.PASS,
        extra=(("step", "mana_charge"),),
    )


# ── Main Step — Summon Creature (rule 301, 701.3) ─────────────────────────────

def summon_creature(
    player:             int,
    card_uid:           str,
    card_id:            int,
    mana_used:          list[ManaUsage],
    evolution_base_uid: Optional[str] = None,
    twinpact_face:      int = 0,
) -> Action:
    """
    Rule 301.1: Pay cost by tapping mana, move creature from hand to battle zone.
    Rule 112.2a: mana_used carries which civilization each tapped card provides.
    Rule 301.5: Creature enters with summoning sickness (unless Speed Attacker).

    evolution_base_uid: set for Evolution creatures — the creature being evolved onto.
    (rule 801: evolution sits on top of a valid base creature)

    twinpact_face: Rule 810.3 — which face of a Twinpact card is being used.
    """
    return Action(
        player=player,
        action_type=ActionType.SUMMON_CREATURE,
        card_uid=card_uid,
        card_id=card_id,
        mana_used=tuple(mana_used),
        evolution_base_uid=evolution_base_uid,
        twinpact_face=twinpact_face,
    )


# ── Main Step — Cast Spell (rule 302, 701.4) ──────────────────────────────────

def cast_spell(
    player:    int,
    card_uid:  str,
    card_id:   int,
    mana_used: list[ManaUsage],
) -> Action:
    """
    Rule 302.1: Pay cost, resolve spell effect, move to graveyard.
    Rule 112.2a: mana_used specifies civilization used from each tapped card.
    """
    return Action(
        player=player,
        action_type=ActionType.CAST_SPELL,
        card_uid=card_uid,
        card_id=card_id,
        mana_used=tuple(mana_used),
    )


# ── Activated Ability (rule 110.3c) ──────────────────────────────────────────

def activate_ability(
    player:       int,
    source_uid:   str,
    source_card_id: int,
    ability_index: int,
    mana_used:    list[ManaUsage],
    tap_source:   bool = False,
    discard_uid:  Optional[str] = None,
    is_forbidden_release: bool = False,
    is_neo_evolve: bool = False,
) -> Action:
    """
    Rule 110.3c: Activate an ability on a card in play by paying its cost.
    Costs may include: tapping mana cards, tapping the source card, and/or
    discarding a card from hand.

    source_uid:            uid of the card whose ability is being activated
    source_card_id:        card_id of the source card
    ability_index:         which ■ ability on the card (0-based)
    mana_used:             tuple of ManaUsage for mana cost payment
    tap_source:            True if the source card must tap (e.g. creature abilities)
    discard_uid:           uid of card from hand to discard (if discard cost)
    is_forbidden_release:  True if this is a Forbidden Release activation (rule 809)
    is_neo_evolve:         True if this is a NEO Evolution activation (rule 802)
    """
    extra = [
        ("ability_index", ability_index),
        ("tap_source", tap_source),
        ("discard_uid", discard_uid),
    ]
    if is_forbidden_release:
        extra.append(("is_forbidden_release", True))
    if is_neo_evolve:
        extra.append(("is_neo_evolve", True))

    return Action(
        player=player,
        action_type=ActionType.ACTIVATE_ABILITY,
        card_uid=source_uid,
        card_id=source_card_id,
        mana_used=tuple(mana_used),
        extra=tuple(extra),
    )


# ── Main Step — Generate Cross Gear (rule 303, 701.16) ────────────────────────

def generate_cross_gear(
    player:    int,
    card_uid:  str,
    card_id:   int,
    mana_used: list[ManaUsage],
) -> Action:
    """
    Rule 303.1: Cross Gear is placed in the battle zone (not equipped yet).
    Generates the gear — crossing it onto a creature is a separate action.
    """
    return Action(
        player=player,
        action_type=ActionType.GENERATE_CROSS_GEAR,
        card_uid=card_uid,
        card_id=card_id,
        mana_used=tuple(mana_used),
    )


def cross_gear(
    player:     int,
    gear_uid:   str,
    gear_id:    int,
    target_uid: str,
    mana_used:  list[ManaUsage],
) -> Action:
    """
    Rule 303.3b: Cross an existing unequipped Cross Gear onto a creature.
    gear_uid  = uid of the Cross Gear in battle zone.
    target_uid = uid of the Creature to equip.
    """
    return Action(
        player=player,
        action_type=ActionType.CROSS_GEAR,
        card_uid=gear_uid,
        card_id=gear_id,
        target_uid=target_uid,
        mana_used=tuple(mana_used),
    )


# ── Main Step — Fortify Castle (rule 304, 701.19) ─────────────────────────────

def fortify_castle(
    player:     int,
    card_uid:   str,
    card_id:    int,
    mana_used:  list[ManaUsage],
    target_uid: Optional[str] = None,  # shield position uid if attaching to shield
) -> Action:
    """
    Rule 304.1: Place Castle under a shield.
    target_uid: uid of the ShieldCard to attach under (if required by card).
    """
    return Action(
        player=player,
        action_type=ActionType.FORTIFY_CASTLE,
        card_uid=card_uid,
        card_id=card_id,
        mana_used=tuple(mana_used),
        target_uid=target_uid,
    )


# ── Main Step — Deploy Field (rule 308, 701.27) ───────────────────────────────

def deploy_field(
    player:    int,
    card_uid:  str,
    card_id:   int,
    mana_used: list[ManaUsage],
) -> Action:
    """
    Rule 308.1: Place a Field card (D2 Field, etc.) into the battle zone.
    If a field already exists, it goes to the graveyard (unless D2 Field rule).
    """
    return Action(
        player=player,
        action_type=ActionType.DEPLOY_FIELD,
        card_uid=card_uid,
        card_id=card_id,
        mana_used=tuple(mana_used),
    )


# ── Main Step — Execute Tamaseed ──────────────────────────────────────────────

def execute_tamaseed(
    player:    int,
    card_uid:  str,
    card_id:   int,
    mana_used: list[ManaUsage],
) -> Action:
    """Tamaseed (DMRP-21+): play from hand by paying cost."""
    return Action(
        player=player,
        action_type=ActionType.EXECUTE_TAMASEED,
        card_uid=card_uid,
        card_id=card_id,
        mana_used=tuple(mana_used),
    )


# ── Main Step — Combine King Cells (rule 814.1c) ──────────────────────────────

def combine_king_creature(
    player:        int,
    king_card_id:  int,
    cell_uids:     list[str],
    mana_used:     list[ManaUsage],
) -> Action:
    """
    Rule 814.1c: Pay the combined King Creature's cost using mana (including
    King Cells in the mana zone), then combine cells from hand/mana into BZ.
    """
    return Action(
        player=player,
        action_type=ActionType.COMBINE_KING_CREATURE,
        card_id=king_card_id,
        selected_uids=tuple(cell_uids),
        mana_used=tuple(mana_used),
    )


# ── Main Step — Pass (end main step) ─────────────────────────────────────────

def pass_main(player: int) -> Action:
    """Player is done playing cards and moves to attack step."""
    return Action(
        player=player,
        action_type=ActionType.PASS,
        extra=(("step", "main"),),
    )


# ── Attack Step — Declare Attacker (rule 506) ─────────────────────────────────

def attack_player(
    player:       int,
    attacker_uid: str,
    attacker_id:  int,
) -> Action:
    """
    Rule 506.1: Declare a creature to attack the opponent player.
    Rule 506.3: Creature must be untapped and not have summoning sickness
                (unless Speed Attacker) and not have cannot_attack flag.
    The attacked 'player' is always the opponent (1 - player).
    target_uid encodes which player is being attacked.
    """
    target_player_uid = f"player_{1 - player}"
    return Action(
        player=player,
        action_type=ActionType.ATTACK_PLAYER,
        card_uid=attacker_uid,
        card_id=attacker_id,
        target_uid=target_player_uid,
    )


def attack_creature(
    player:       int,
    attacker_uid: str,
    attacker_id:  int,
    target_uid:   str,
    target_id:    int,
) -> Action:
    """
    Rule 506.3: Declare a creature to attack a TAPPED opponent creature.
    Rule 701.18 (Mach Fighter): can also attack untapped opponent creatures.
    target_uid = uid of the opponent's creature being attacked.
    """
    return Action(
        player=player,
        action_type=ActionType.ATTACK_CREATURE,
        card_uid=attacker_uid,
        card_id=attacker_id,
        target_uid=target_uid,
        extra=(("target_id", target_id),),
    )


def pass_attack(player: int) -> Action:
    """Player declares no more attackers. Move to end of turn."""
    return Action(
        player=player,
        action_type=ActionType.PASS,
        extra=(("step", "attack"),),
    )


# ── Block Declaration (rule 507) ──────────────────────────────────────────────

def declare_blocker(
    player:      int,
    blocker_uid: str,
    blocker_id:  int,
) -> Action:
    """
    Rule 507.1: Non-turn player may intercept the attack with a Blocker creature.
    Rule 701.12: Blocker must be untapped and not ignored (no seals).
    Only one blocker may be declared per attack (rule 507.1).
    The attack target changes from player to this blocker.
    """
    return Action(
        player=player,
        action_type=ActionType.DECLARE_BLOCKER,
        card_uid=blocker_uid,
        card_id=blocker_id,
    )


def declare_guardman(
    player:       int,
    blocker_uid:  str,
    blocker_id:   int,
) -> Action:
    """
    Guardman: must block an attack on the player if this creature is able.
    Same mechanical effect as DECLARE_BLOCKER but from a "must" trigger.
    """
    return Action(
        player=player,
        action_type=ActionType.DECLARE_GUARDMAN,
        card_uid=blocker_uid,
        card_id=blocker_id,
    )


def pass_block(player: int) -> Action:
    """
    Rule 507.1: Non-turn player chooses not to block (or has no valid blockers).
    Attack proceeds to Direct Attack or Battle depending on target type.
    """
    return Action(
        player=player,
        action_type=ActionType.PASS,
        extra=(("step", "block"),),
    )


# ── Shield Break Order (rule 509.2) ───────────────────────────────────────────

def select_shield_to_break(
    player:       int,
    shield_index: int,
) -> Action:
    """
    Rule 509.2: Active player chooses which shield position to break first.
    Relevant when a creature breaks multiple shields (Double/Triple Breaker).
    shield_index: 0-4, position in the defending player's shield_zone list.
    """
    return Action(
        player=player,
        action_type=ActionType.SELECT_ATTACK_ORDER,
        shield_index=shield_index,
    )


# ── Free Execution Abilities (rule 112.3) ─────────────────────────────────────

def use_shield_trigger(
    player:    int,
    card_uid:  str,
    card_id:   int,
    use:       bool = True,
) -> Action:
    """
    Rule 112.3a: When a shield is broken, if it has S-Trigger, the player
    may cast or summon it for free. Timing: BEFORE the broken shield moves
    to hand (rule 113.6). use=False means "add to hand without triggering".

    For spells: cast immediately for free.
    For creatures: summon immediately for free.
    """
    return Action(
        player=player,
        action_type=ActionType.USE_SHIELD_TRIGGER,
        card_uid=card_uid,
        card_id=card_id,
        choice=use,
        mana_used=(),   # always free
    )


def use_s_back(
    player:      int,
    card_uid:    str,      # the card with S-Back ability in hand
    card_id:     int,
    discard_uid: str,      # the card being discarded to pay S-Back cost
    discard_id:  int,
) -> Action:
    """
    Rule 112.3b: S-Back — discard a specified card from hand to execute
    a card with S-Back for free. Both cards must be in hand.
    """
    return Action(
        player=player,
        action_type=ActionType.USE_S_BACK,
        card_uid=card_uid,
        card_id=card_id,
        discard_uid=discard_uid,
        mana_used=(),
        extra=(("discard_id", discard_id),),
    )


def use_ninja_strike(
    player:      int,
    card_uid:    str,      # the Ninja Strike creature in hand
    card_id:     int,
    discard_uid: str | None = None,  # only for card-specific extra costs
    discard_id:  int | None = None,
) -> Action:
    """
    Rule 112.3c: Ninja Strike — summon a creature without paying its cost
    during the specified attack/block processing timing if the mana-zone
    threshold is met.
    """
    return Action(
        player=player,
        action_type=ActionType.USE_NINJA_STRIKE,
        card_uid=card_uid,
        card_id=card_id,
        discard_uid=discard_uid,
        mana_used=(),
        extra=((("discard_id", discard_id),) if discard_id is not None else ()),
    )


def use_g_zero(
    player:   int,
    card_uid: str,
    card_id:  int,
) -> Action:
    """
    Rule 112.3e: G-Zero — if the specified condition is met, the creature
    may be summoned for free (cost becomes 0, no mana tapped).
    Still goes through normal summon resolution (ETB triggers, etc.).
    """
    return Action(
        player=player,
        action_type=ActionType.USE_G_ZERO,
        card_uid=card_uid,
        card_id=card_id,
        mana_used=(),   # free
    )


def use_attack_chance(
    player:   int,
    card_uid: str,
    card_id:  int,
) -> Action:
    """
    Rule 112.3f: Attack Chance — when one of your creatures attacks,
    cast a spell with Attack Chance for free. Timing: during attack declaration.
    """
    return Action(
        player=player,
        action_type=ActionType.USE_ATTACK_CHANCE,
        card_uid=card_uid,
        card_id=card_id,
        mana_used=(),
    )


def use_g_strike(
    player:   int,
    card_uid: str,
    card_id:  int,
    use:      bool = True,
) -> Action:
    """
    Rule 101.4b: G-Strike — same timing window as S-Trigger. When a shield
    with G-Strike is broken, player may use its effect for free.
    use=False means add to hand without triggering.
    """
    return Action(
        player=player,
        action_type=ActionType.USE_G_STRIKE,
        card_uid=card_uid,
        card_id=card_id,
        choice=use,
        mana_used=(),
    )


def use_over_drive(
    player:       int,
    creature_uid: str,
    creature_id:  int,
    mana_used:    list["ManaUsage"],
) -> Action:
    """
    Rule 112.2d: Over Drive — when you summon this creature, you may tap
    another N cards of specified civilization(s) in your mana zone. If you do,
    this creature gets the bonus ability (additional triggered effect).

    The bonus ability is already stored as a triggered effect on the creature.
    This action just pays the additional cost (tapping the mana cards).
    The triggered effect will fire automatically after the summon completes.
    """
    return Action(
        player=player,
        action_type=ActionType.USE_OVER_DRIVE,
        card_uid=creature_uid,
        card_id=creature_id,
        mana_used=tuple(mana_used),
    )


def hyperize(
    player:       int,
    creature_uid: str,
    creature_id:  int,
) -> Action:
    """
    Rule 816: Release Hyper Mode on a creature that has the Hyperize ability.
    Can be done during the player's main step.
    """
    return Action(
        player=player,
        action_type=ActionType.HYPERIZE,
        card_uid=creature_uid,
        card_id=creature_id,
    )


def use_sabaki_z(
    player:      int,
    card_uid:    str,      # the Sabaki Z card in hand
    card_id:     int,
    discard_uid: str,      # the Emblem of Judgment shield card to discard
) -> Action:
    """
    Rule 112.3d: Sabaki Z — when a card with 'Emblem of Judgment' is added
    from a shield to your hand, you can immediately execute it without paying
    the cost by discarding that card.
    """
    return Action(
        player=player,
        action_type=ActionType.USE_SABAKI_Z,
        card_uid=card_uid,
        card_id=card_id,
        discard_uid=discard_uid,
        mana_used=(),
    )


# ── Effect Resolution Choices ─────────────────────────────────────────────────

def select_yes_no(
    player: int,
    choice: bool,
    source_uid: str = "",
) -> Action:
    """
    An optional effect asks "do you want to use this?" — player answers.
    choice=True: use the effect. choice=False: skip.
    source_uid: the uid of the card/effect asking.
    """
    return Action(
        player=player,
        action_type=ActionType.SELECT_YES_NO,
        choice=choice,
        extra=(("source_uid", source_uid),),
    )


def select_target(
    player:        int,
    target_uid:    str,
    target_zone:   str = "battle_zone",
    source_uid:    str = "",
) -> Action:
    """
    Choose a single target for an effect.
    target_uid: uid of the chosen target (Creature uid, ShieldCard uid, etc.)
    target_zone: where the target is (for disambiguation).
    source_uid: uid of the card/effect that requires the target.
    """
    return Action(
        player=player,
        action_type=ActionType.SELECT_TARGET,
        target_uid=target_uid,
        target_zone=target_zone,
        extra=(("source_uid", source_uid),),
    )


def select_targets(
    player:        int,
    target_uids:   list[str],
    target_zone:   str = "battle_zone",
    source_uid:    str = "",
) -> Action:
    """
    Choose multiple targets for an effect ("up to 2 creatures", etc.).
    target_uids: list of chosen target uids.
    """
    return Action(
        player=player,
        action_type=ActionType.SELECT_TARGET,
        target_zone=target_zone,
        selected_uids=tuple(target_uids),
        extra=(("source_uid", source_uid),),
    )


def select_mana(
    player:    int,
    mana_used: list[ManaUsage],
    source_uid: str = "",
) -> Action:
    """
    Rule 112.2a: Choose which mana cards to tap and which civilization
    each multi-colored card provides.
    Used when effect asks player to re-select mana (e.g. after cost reduction).
    """
    return Action(
        player=player,
        action_type=ActionType.SELECT_MANA,
        mana_used=tuple(mana_used),
        extra=(("source_uid", source_uid),),
    )


def select_card(
    player:     int,
    card_uid:   str,
    card_id:    int,
    source_uid: str = "",
    zone:       str = "hand",
) -> Action:
    """
    Choose a specific card (from hand, deck search results, graveyard, etc.)
    for an effect that requires a card selection.
    zone: where the card is being chosen from.
    """
    return Action(
        player=player,
        action_type=ActionType.SELECT_CARD,
        card_uid=card_uid,
        card_id=card_id,
        target_zone=zone,
        extra=(("source_uid", source_uid),),
    )


def select_evolution_base(
    player:           int,
    evolution_uid:    str,   # uid of the HandCard being evolved from hand
    evolution_id:     int,
    base_uid:         str,   # uid of the Creature being evolved onto
    mana_used:        list[ManaUsage],
) -> Action:
    """
    Rule 801: Choose which creature in battle zone to place an evolution on top of.
    Used when player has multiple valid evolution bases.
    evolution_uid: the hand card being played as an evolution.
    base_uid: the creature being evolved onto.
    """
    return Action(
        player=player,
        action_type=ActionType.SELECT_EVOLUTION_BASE,
        card_uid=evolution_uid,
        card_id=evolution_id,
        evolution_base_uid=base_uid,
        mana_used=tuple(mana_used),
    )


def select_civilization(
    player:     int,
    civ:        Civilization,
    source_uid: str = "",
) -> Action:
    """
    Choose a civilization (e.g. for search effects "search for a Fire card").
    """
    return Action(
        player=player,
        action_type=ActionType.SELECT_CARD,
        selected_civ=civ,
        extra=(("source_uid", source_uid), ("select_type", "civilization")),
    )


def select_cards_from_list(
    player:     int,
    card_uids:  list[str],
    source_uid: str = "",
    zone:       str = "hand",
) -> Action:
    """
    Choose multiple cards from a presented list (e.g. deck search: "pick 1 of 3").
    """
    return Action(
        player=player,
        action_type=ActionType.SELECT_CARD,
        selected_uids=tuple(card_uids),
        target_zone=zone,
        extra=(("source_uid", source_uid),),
    )


# ── Generic Pass ──────────────────────────────────────────────────────────────

def pass_action(player: int, step: str = "") -> Action:
    """
    Generic pass for any step. Use the step-specific versions above when possible.
    step: human-readable label for debugging ("mana_charge", "main", "attack", etc.)
    """
    return Action(
        player=player,
        action_type=ActionType.PASS,
        extra=((("step", step),) if step else ()),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Action equality helpers for MCTS
# ─────────────────────────────────────────────────────────────────────────────

def actions_equal(a: Action, b: Action) -> bool:
    """
    Two actions are equal if all their meaningful fields match.
    Used by MCTS to avoid duplicate children.
    """
    return (
        a.player        == b.player
        and a.action_type == b.action_type
        and a.card_uid    == b.card_uid
        and a.target_uid  == b.target_uid
        and a.mana_used   == b.mana_used
        and a.evolution_base_uid == b.evolution_base_uid
        and a.twinpact_face == b.twinpact_face
        and a.discard_uid == b.discard_uid
        and a.choice      == b.choice
        and a.selected_uids == b.selected_uids
        and a.shield_index == b.shield_index
    )


# ─────────────────────────────────────────────────────────────────────────────
# Action encoding for neural network
# ─────────────────────────────────────────────────────────────────────────────

# Master action-type index used by the policy head.
# Order matters — must be consistent across training runs.
ACTION_TYPE_INDEX: dict[ActionType, int] = {
    ActionType.CHARGE_MANA:           0,
    ActionType.SUMMON_CREATURE:       1,
    ActionType.CAST_SPELL:            2,
    ActionType.GENERATE_CROSS_GEAR:   3,
    ActionType.CROSS_GEAR:            4,
    ActionType.FORTIFY_CASTLE:        5,
    ActionType.DEPLOY_FIELD:          6,
    ActionType.EXECUTE_TAMASEED:      7,
    ActionType.ATTACK_PLAYER:         8,
    ActionType.ATTACK_CREATURE:       9,
    ActionType.DECLARE_BLOCKER:       10,
    ActionType.DECLARE_GUARDMAN:      11,
    ActionType.USE_SHIELD_TRIGGER:    12,
    ActionType.USE_S_BACK:            13,
    ActionType.USE_NINJA_STRIKE:      14,
    ActionType.USE_G_ZERO:            15,
    ActionType.USE_ATTACK_CHANCE:     16,
    ActionType.USE_G_STRIKE:          17,
    ActionType.USE_OVER_DRIVE:        18,
    ActionType.USE_SABAKI_Z:          19,
    ActionType.HYPERIZE:              20,
    ActionType.SELECT_TARGET:         21,
    ActionType.SELECT_MANA:           22,
    ActionType.SELECT_CARD:           23,
    ActionType.SELECT_YES_NO:         24,
    ActionType.SELECT_ATTACK_ORDER:   25,
    ActionType.SELECT_EVOLUTION_BASE: 26,
    ActionType.PASS:                  27,
    ActionType.COMBINE_KING_CREATURE: 28,
    ActionType.ACTIVATE_ABILITY:      29,
}

NUM_ACTION_TYPES = len(ACTION_TYPE_INDEX)
