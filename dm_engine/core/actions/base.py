"""core/actions/base.py — Action frozen dataclass.

Rule 101.2  — "Cannot" overrides "Can". Cards beat rules.
Rule 112.2a — Multi-colored mana provides ONE chosen civilization per tap.
              mana_used carries (uid, chosen_civ) pairs — not just uids.
Rule 112.3  — Free execution abilities (S-Trigger, Ninja Strike, G-Zero etc.)
              are RESPONSES, not main-phase actions. Each gets its own ActionType.
Rule 503.1  — Only ONE card may be charged per turn. No multi-charge.
Rule 509.2  — Shield break order: active player chooses which shield to break.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from ..enums import ActionType, Civilization, ManaUsage


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

    # ── Twinpact face selection (Rule 810.3) ────────────────────────────────
    twinpact_face:      int = 0                     # 0 = default, 1 = other face

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
            ActionType.USE_SABAKI_Z,
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
