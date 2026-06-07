"""
core/global_effects.py — Game-wide effects that apply to the entire board.

These are NOT tied to a specific card in a specific zone. They are conditions
on the game itself. Examples:

  "Players can't cast spells other than Light civilization"
  "All creatures in the battle zone get +2000 power"
  "Players can't charge mana"
  "Creatures can't attack"

These are stored in GameState.global_effects and checked by:
  - ActionGenerator (are certain actions currently illegal?)
  - EffectExecutor (does this effect get modified?)
  - StateEncoder (neural net features about board conditions)

Source tracking: every global effect records which card/creature caused it
so it can be removed when that card leaves play or the duration expires.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from .enums import Civilization, GlobalEffectType


@dataclass
class GlobalEffect:
    """
    A single game-wide effect currently active.

    applied_by_uid:  the uid of the zone object (Creature, etc.) that created this.
                     When that creature leaves the battle zone, this effect is removed.
    applied_by_card: the card id — for UI display and debugging.
    controller:      which player controls the source (0 or 1).
                     Matters for effects that say "your opponent can't..."
    target_player:   None = affects both players, 0 = player 0, 1 = player 1.
    duration:        "while_in_play"    — lasts as long as source card is in play
                     "until_end_of_turn" — expires during END phase
                     "permanent"         — lasts until explicitly removed
    """
    effect_type:     GlobalEffectType
    applied_by_uid:  str              # uid of source creature/card
    applied_by_card: int              # card id for display
    controller:      int              # player who controls source (0 or 1)
    target_player:   Optional[int]    # None=both, 0=p0, 1=p1

    duration:        str = "while_in_play"

    # Extra parameters depending on effect_type
    # ── RESTRICT_SPELL_CIVILIZATION ──────────────────────────────────────────
    # "Players can only cast Light spells" → allowed_civilizations = {Civilization.LIGHT}
    allowed_civilizations: frozenset[Civilization] = field(default_factory=frozenset)

    # ── RESTRICT_SUMMON_CIVILIZATION ─────────────────────────────────────────
    # "Players can only summon Fire creatures" → allowed_civilizations = {Civilization.FIRE}
    # (reuses allowed_civilizations field)

    # ── ALL_CREATURES_POWER_MOD ───────────────────────────────────────────────
    # "All your opponent's creatures get -2000 power"
    power_mod_amount:  int = 0
    power_mod_target:  Optional[str] = None   # "own" | "opponent" | None (all)

    # ── CANNOT_ATTACK ─────────────────────────────────────────────────────────
    # Target player's creatures can't attack — no extra params needed

    # ── LOCK_CARD_TYPE ────────────────────────────────────────────────────────
    # "cannot summon Evolution creatures" -> locked_card_subtype = "Evolution"
    locked_card_type:    Optional[str] = None
    locked_card_subtype: Optional[str] = None

    # ── GRANT_KEYWORD_ALL ─────────────────────────────────────────────────────
    # "all your Fire creatures gain Speed Attacker"
    grant_keyword:       Optional[str] = None
    grant_to_race:       Optional[str] = None
    grant_to_civ:        Optional[str] = None
    grant_to_controller: Optional[int] = None

    # ── LOCK_ALL_SPELLS ───────────────────────────────────────────────────────
    # No extra params

    def affects_player(self, player: int) -> bool:
        """Does this effect apply to the given player?"""
        if self.target_player is None:
            return True     # affects both
        return self.target_player == player

    def is_spell_restriction(self) -> bool:
        return self.effect_type in (
            GlobalEffectType.RESTRICT_SPELL_CIVILIZATION,
            GlobalEffectType.LOCK_ALL_SPELLS,
        )

    def is_summon_restriction(self) -> bool:
        return self.effect_type == GlobalEffectType.RESTRICT_SUMMON_CIVILIZATION

    def is_power_modifier(self) -> bool:
        return self.effect_type == GlobalEffectType.ALL_CREATURES_POWER_MOD

    def __repr__(self) -> str:
        target = f"P{self.target_player}" if self.target_player is not None else "BOTH"
        return (
            f"<GlobalEffect:{self.effect_type.value} "
            f"target={target} dur={self.duration} "
            f"from=card{self.applied_by_card}[{self.applied_by_uid}]>"
        )


# ── Global Effect Registry ────────────────────────────────────────────────────

@dataclass
class GlobalEffectRegistry:
    """
    Holds all currently active global effects.
    Part of GameState — gets deepcopied on every MCTS branch.

    Provides query methods used by ActionGenerator and EffectExecutor.
    """
    effects: list[GlobalEffect] = field(default_factory=list)

    def add(self, effect: GlobalEffect) -> None:
        self.effects.append(effect)

    def remove_by_source(self, source_uid: str) -> int:
        """Remove all effects from a specific source card. Returns count removed."""
        before = len(self.effects)
        self.effects = [e for e in self.effects if e.applied_by_uid != source_uid]
        return before - len(self.effects)

    def expire_eot(self) -> None:
        """Remove all effects that last only until end of turn."""
        self.effects = [e for e in self.effects if e.duration != "until_end_of_turn"]

    def is_empty(self) -> bool:
        return len(self.effects) == 0

    # ── Spell restriction queries ──────────────────────────────────────────────

    def can_cast_spell(self, player: int, spell_civs: frozenset[Civilization]) -> bool:
        """
        Returns False if any global effect prevents this player from casting
        this spell based on its civilizations.

        Example: D2 Field effect "players can only cast Light spells"
        """
        for eff in self.effects:
            if not eff.affects_player(player):
                continue
            if eff.effect_type == GlobalEffectType.LOCK_ALL_SPELLS:
                return False
            if eff.effect_type == GlobalEffectType.RESTRICT_SPELL_CIVILIZATION:
                # Spell must share at least one allowed civilization
                if not spell_civs.intersection(eff.allowed_civilizations):
                    return False
        return True

    # ── Summon restriction queries ─────────────────────────────────────────────

    def can_summon_creature(
        self,
        player: int,
        creature_civs: frozenset,
        card_type: str = "Creature",
        card_subtype: str = "None",
    ) -> bool:
        """
        Returns False if any global effect prevents summoning this creature.
        card_type/card_subtype used for LOCK_CARD_TYPE checks.
        """
        for eff in self.effects:
            if not eff.affects_player(player):
                continue
            if eff.effect_type == GlobalEffectType.LOCK_ALL_CREATURES:
                return False
            if eff.effect_type == GlobalEffectType.RESTRICT_SUMMON_CIVILIZATION:
                if not creature_civs.intersection(eff.allowed_civilizations):
                    return False
            if eff.effect_type == GlobalEffectType.LOCK_CARD_TYPE:
                if eff.locked_card_type and card_type == eff.locked_card_type:
                    return False
                if eff.locked_card_subtype and card_subtype == eff.locked_card_subtype:
                    return False
        return True

    # ── Attack restriction queries ─────────────────────────────────────────────

    def can_attack_globally(self, player: int) -> bool:
        """
        Returns False if a global effect prevents this player's creatures from attacking.
        Note: individual creatures may have their own cannot_attack flags.
        """
        for eff in self.effects:
            if not eff.affects_player(player):
                continue
            if eff.effect_type == GlobalEffectType.CANNOT_ATTACK:
                return False
        return True

    # ── Mana restriction queries ───────────────────────────────────────────────

    def can_charge_mana(self, player: int) -> bool:
        for eff in self.effects:
            if not eff.affects_player(player):
                continue
            if eff.effect_type == GlobalEffectType.CANNOT_CHARGE_MANA:
                return False
        return True

    # ── Power modification queries ─────────────────────────────────────────────

    def get_global_power_bonus(self, player: int, controller: int) -> int:
        """
        Returns total power modification from global effects for a creature
        controlled by `controller`, belonging to `player`'s side perspective.

        Used when computing creature's effective power.
        """
        total = 0
        for eff in self.effects:
            if eff.effect_type != GlobalEffectType.ALL_CREATURES_POWER_MOD:
                continue
            if eff.power_mod_target == "own" and eff.controller != controller:
                continue
            if eff.power_mod_target == "opponent" and eff.controller == controller:
                continue
            total += eff.power_mod_amount
        return total

    # ── Full state summary (for debugging / logging) ──────────────────────────

    def active_restrictions_for_player(self, player: int) -> list[str]:
        """Human-readable list of active restrictions for a player."""
        result = []
        for eff in self.effects:
            if not eff.affects_player(player):
                continue
            if eff.effect_type == GlobalEffectType.LOCK_ALL_SPELLS:
                result.append("Cannot cast any spells")
            elif eff.effect_type == GlobalEffectType.RESTRICT_SPELL_CIVILIZATION:
                civs = ", ".join(c.value for c in eff.allowed_civilizations)
                result.append(f"Can only cast {civs} spells")
            elif eff.effect_type == GlobalEffectType.RESTRICT_SUMMON_CIVILIZATION:
                civs = ", ".join(c.value for c in eff.allowed_civilizations)
                result.append(f"Can only summon {civs} creatures")
            elif eff.effect_type == GlobalEffectType.CANNOT_ATTACK:
                result.append("Creatures cannot attack")
            elif eff.effect_type == GlobalEffectType.CANNOT_CHARGE_MANA:
                result.append("Cannot charge mana")
            elif eff.effect_type == GlobalEffectType.ALL_CREATURES_POWER_MOD:
                sign = "+" if eff.power_mod_amount >= 0 else ""
                result.append(f"All creatures: {sign}{eff.power_mod_amount} power")
        return result

    def get_granted_keywords(
        self,
        controller: int,
        race: Optional[str] = None,
        civ: Optional[str] = None,
    ) -> list[str]:
        """
        Return keyword strings granted by GRANT_KEYWORD_ALL effects
        to a creature with the given controller/race/civ.
        """
        granted = []
        for eff in self.effects:
            if eff.effect_type != GlobalEffectType.GRANT_KEYWORD_ALL:
                continue
            if eff.grant_to_controller is not None and eff.grant_to_controller != controller:
                continue
            if eff.grant_to_race and race and eff.grant_to_race != race:
                continue
            if eff.grant_to_civ and civ and eff.grant_to_civ != civ:
                continue
            if eff.grant_keyword:
                granted.append(eff.grant_keyword)
        return granted

    def __repr__(self) -> str:
        if not self.effects:
            return "<GlobalEffects: none>"
        return f"<GlobalEffects: {len(self.effects)} active>"
