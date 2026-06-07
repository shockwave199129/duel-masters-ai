"""
core/actions.py — Action dataclass and constructor helpers.

Every possible player decision in a Duel Masters game is represented
as an Action object. The engine takes (GameState, Action) → GameState.

Design rules:
  1. Actions are IMMUTABLE (frozen dataclass) — safe to store in MCTS trees.
  2. Actions are HASHABLE — MCTS nodes use them as dict keys.
  3. Actions carry ALL information needed to execute — no ambiguity.
  4. Constructor functions (one per action type) enforce correct shape.

Rule references are cited throughout from DM Comprehensive Rules Ver. 1.50.

─────────────────────────────────────────────────────────────────────────────
KEY RULES THAT SHAPE THIS FILE

Rule 101.2  — "Cannot" overrides "Can". Cards beat rules.
Rule 112.2a — Multi-colored mana provides ONE chosen civilization per tap.
              mana_used carries (uid, chosen_civ) pairs — not just uids.
Rule 112.3  — Free execution abilities (S-Trigger, Ninja Strike, G-Zero etc.)
              are RESPONSES, not main-phase actions. Each gets its own ActionType.
Rule 301.5  — Summoning sickness: creatures cannot attack the turn they enter
              unless they have Speed Attacker.
Rule 405.1  — Multi-colored cards enter mana zone TAPPED.
Rule 503.1  — Only ONE card may be charged per turn. No multi-charge.
Rule 504.1  — During main step, player may execute any number of cards
              (as long as costs are paid). Spells, creatures, cross gear, etc.
Rule 506.1  — Only ONE creature attacks at a time. Attacks happen sequentially.
Rule 506.3  — Creatures can only attack tapped creatures OR the player directly.
              (Exception: Mach Fighter can attack untapped creatures.)
Rule 507.2  — Blocker declaration: defending player may choose ONE creature
              with Blocker that is not tapped and not ignored.
Rule 509.1  — Direct attack: if the attacked creature is the player AND they
              have 0 shields, it's a direct attack → player loses immediately
              after all S-Triggers etc. resolve.
Rule 509.2  — Shield break order: active player chooses which shield to break.
              Double Breaker breaks 2, Triple breaks 3, World Breaker breaks all.
Rule 112.3c — Ninja Strike: summon without paying cost during its attack
              timing if the stated mana-zone threshold is met.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from .enums import ActionType, Civilization, ManaUsage


# ─────────────────────────────────────────────────────────────────────────────
# Core Action dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Action:
    """
    A single player decision, fully self-describing.

    All fields are optional except action_type and player — most actions
    use only a subset. Constructor functions below create correctly shaped
    Actions for each situation.

    FIELD GUIDE:
    ─────────────────────────────────────────────────────────────────────
    player          — 0 or 1, who is taking this action
    action_type     — what kind of action (see ActionType enum)

    card_uid        — uid of the card being played / used as source
                      (HandCard.uid for plays from hand,
                       Creature.uid for attack declarations,
                       ManaCard.uid for charging)

    card_id         — card_id of the card (for reference / encoding)
                      always set alongside card_uid when applicable

    target_uid      — uid of the target creature, shield, or player
                      (Creature.uid for creature targets)
                      ("player_0" / "player_1" for player targets)

    target_zone     — zone string for zone-based targeting
                      (e.g. "battle_zone", "graveyard")

    mana_used       — tuple of ManaUsage objects describing which mana
                      cards are tapped and which civilization each provides.
                      Rule 112.2a: each multi-civ card provides ONE civ,
                      chosen by the player at payment time.
                      Empty tuple for free actions (S-Trigger, etc.)

    evolution_base_uid — uid of the creature being evolved onto
                         (for SUMMON_CREATURE of an evolution card)

    discard_uid     — uid of card in hand to discard
                      (for Ninja Strike, S-Back, Revolution Change)

    choice          — boolean or string for yes/no or selection choices
                      (SELECT_YES_NO, SELECT_CARD, etc.)

    selected_uids   — tuple of uids when multiple selections needed
                      (e.g. "return up to 2 creatures": [uid1, uid2])

    selected_civ    — civilization chosen for a single-civ selection
                      (e.g. when a search effect asks "choose a civilization")

    shield_index    — 0-4, which shield position to break first
                      (rule 509.2: active player chooses break order)

    extra           — dict for rare action-specific data not covered above
                      kept frozen-safe via tuple of tuples

    ─────────────────────────────────────────────────────────────────────
    """

    # ── Required ──────────────────────────────────────────────────────────────
    player:             int
    action_type:        ActionType

    # ── Card being played / used ──────────────────────────────────────────────
    card_uid:           Optional[str]              = None
    card_id:            Optional[int]              = None

    # ── Target ────────────────────────────────────────────────────────────────
    target_uid:         Optional[str]              = None
    target_zone:        Optional[str]              = None

    # ── Mana payment (rule 112.2a) ────────────────────────────────────────────
    # Tuple of ManaUsage — each (uid, chosen_civilization).
    # Empty for free-cost actions.
    mana_used:          tuple                      = field(default=())

    # ── Evolution ─────────────────────────────────────────────────────────────
    evolution_base_uid: Optional[str]              = None

    # ── Discard (Ninja Strike, S-Back, Revolution Change) ─────────────────────
    discard_uid:        Optional[str]              = None

    # ── Selections ────────────────────────────────────────────────────────────
    choice:             Optional[object]           = None   # bool or str
    selected_uids:      tuple                      = field(default=())
    selected_civ:       Optional[Civilization]     = None
    shield_index:       Optional[int]              = None   # rule 509.2

    # ── Overflow ──────────────────────────────────────────────────────────────
    # Frozen-safe: tuple of (key, value) pairs, not a dict.
    extra:              tuple                      = field(default=())

    # ─────────────────────────────────────────────────────────────────────────
    # Convenience properties
    # ─────────────────────────────────────────────────────────────────────────

    def is_pass(self) -> bool:
        return self.action_type == ActionType.PASS

    def is_attack(self) -> bool:
        return self.action_type in (
            ActionType.ATTACK_PLAYER,
            ActionType.ATTACK_CREATURE,
        )

    def is_free_execution(self) -> bool:
        """Rule 112.3 — free execution abilities are responses, not main actions."""
        return self.action_type in (
            ActionType.USE_SHIELD_TRIGGER,
            ActionType.USE_S_BACK,
            ActionType.USE_NINJA_STRIKE,
            ActionType.USE_G_ZERO,
            ActionType.USE_ATTACK_CHANCE,
            ActionType.USE_G_STRIKE,
        )

    def is_play_from_hand(self) -> bool:
        return self.action_type in (
            ActionType.SUMMON_CREATURE,
            ActionType.CAST_SPELL,
            ActionType.GENERATE_CROSS_GEAR,
            ActionType.FORTIFY_CASTLE,
            ActionType.DEPLOY_FIELD,
            ActionType.EXECUTE_TAMASEED,
            ActionType.CHARGE_MANA,
        )

    def costs_mana(self) -> bool:
        """True for actions that require mana payment."""
        return self.action_type in (
            ActionType.SUMMON_CREATURE,
            ActionType.CAST_SPELL,
            ActionType.GENERATE_CROSS_GEAR,
            ActionType.FORTIFY_CASTLE,
            ActionType.DEPLOY_FIELD,
            ActionType.EXECUTE_TAMASEED,
        )

    def get_mana_list(self) -> list[ManaUsage]:
        return list(self.mana_used)

    def get_selected_uids(self) -> list[str]:
        return list(self.selected_uids)

    def get_extra(self) -> dict:
        return dict(self.extra)

    def __repr__(self) -> str:
        parts = [f"P{self.player}:{self.action_type.value}"]
        if self.card_id:
            parts.append(f"card={self.card_id}")
        if self.card_uid:
            parts.append(f"uid={self.card_uid[:6]}")
        if self.target_uid:
            parts.append(f"→{self.target_uid[:10]}")
        if self.mana_used:
            parts.append(f"mana={len(self.mana_used)}")
        if self.evolution_base_uid:
            parts.append(f"evo_base={self.evolution_base_uid[:6]}")
        if self.choice is not None:
            parts.append(f"choice={self.choice}")
        if self.selected_uids:
            parts.append(f"selected={len(self.selected_uids)}")
        return f"<Action {' '.join(parts)}>"


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
) -> Action:
    """
    Rule 301.1: Pay cost by tapping mana, move creature from hand to battle zone.
    Rule 112.2a: mana_used carries which civilization each tapped card provides.
    Rule 301.5: Creature enters with summoning sickness (unless Speed Attacker).

    evolution_base_uid: set for Evolution creatures — the creature being evolved onto.
    (rule 801: evolution sits on top of a valid base creature)
    """
    return Action(
        player=player,
        action_type=ActionType.SUMMON_CREATURE,
        card_uid=card_uid,
        card_id=card_id,
        mana_used=tuple(mana_used),
        evolution_base_uid=evolution_base_uid,
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
    ActionType.HYPERIZE:              18,
    ActionType.SELECT_TARGET:         19,
    ActionType.SELECT_MANA:           20,
    ActionType.SELECT_CARD:           21,
    ActionType.SELECT_YES_NO:         22,
    ActionType.SELECT_ATTACK_ORDER:   23,
    ActionType.SELECT_EVOLUTION_BASE: 24,
    ActionType.PASS:                  25,
}

NUM_ACTION_TYPES = len(ACTION_TYPE_INDEX)
