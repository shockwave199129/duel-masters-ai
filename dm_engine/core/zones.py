"""
core/zones.py — Zone objects that hold cards during a game.

These are the STATEFUL wrappers around CardDefinitions.
A CardDefinition never changes. A zone object tracks everything
that changes during play: tapped status, power modifications,
which shields are revealed, etc.

Key design: every zone object is part of GameState and gets
deepcopied when MCTS branches. Keep them lean.

Objects defined here:
  ManaCard      — a card in the mana zone (tracks tapped state)
  ShieldCard    — a card in the shield zone (tracks revealed state)
  Creature      — a card in the battle zone (tracks all in-play state)
  HandCard      — thin wrapper (mostly just the definition + instance_id)
  GraveyardCard — card + info about how it got there
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from uuid import uuid4

from .enums import Civilization, Keyword, CardSubtype, CDAFormulaType, EffectAction
from .cards import CardDefinition


def _new_uid() -> str:
    """Unique instance ID — every card copy on the field has one."""
    return str(uuid4())[:8]


# ── Hand Card ─────────────────────────────────────────────────────────────────

@dataclass
class HandCard:
    """
    A card in a player's hand.
    Very thin — hand cards have no in-game state beyond existing.
    The uid lets the engine uniquely reference this specific copy
    even if the player has 2 copies of the same card.
    """
    definition: CardDefinition
    uid:        str = field(default_factory=_new_uid)

    @property
    def id(self) -> int:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def cost(self) -> int:
        return self.definition.cost

    @property
    def civilizations(self) -> frozenset[Civilization]:
        return self.definition.civilizations

    def __repr__(self) -> str:
        return f"<Hand:{self.definition.name}[{self.uid}]>"


# ── Hyperspatial Card ──────────────────────────────────────────────────────────

@dataclass
class HyperspatialCard:
    """
    A card in the Hyperspatial Zone (Psychic, Psychic Super, Dragheart).

    This can be either:
    - A Psychic or Psychic Super Creature (face 0 or 1, treated as Creature)
    - A Dragheart in Weapon or Fortress face (face 0, NOT a creature despite having stats)

    Face-up and visible to both players (rule 407.2).

    Stores the card definition, face, and uid. When summoned, converted to a Creature.

    Rule 805.4 / 807.4: Psychic/Dragheart cards in hyperspatial zone have
    summoning sickness (has_summoning_sickness=True initially).
    """
    definition: CardDefinition
    face:       int = 0  # 0=lower-cost face, 1=awakened/creature face
    uid:        str = field(default_factory=_new_uid)

    @property
    def id(self) -> int:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def cost(self) -> int:
        return self.definition.cost

    @property
    def civilizations(self) -> frozenset[Civilization]:
        return self.definition.civilizations

    def __repr__(self) -> str:
        return f"<Hyperspatial:{self.definition.name}[face={self.face},uid={self.uid}]>"


# ── Mana Card ─────────────────────────────────────────────────────────────────

@dataclass
class ManaCard:
    """
    A card in the mana zone.
    Tracks whether it's tapped (used this turn).
    The card is face-up — opponent can see civilization and card name.

    Rule 405.1: Multi-colored cards are placed TAPPED when charged to mana.
    Use ManaCard.from_charge(definition) to create with correct initial tap state.
    """
    definition: CardDefinition
    uid:        str  = field(default_factory=_new_uid)
    is_tapped:  bool = False

    @classmethod
    def from_charge(cls, definition: "CardDefinition") -> "ManaCard":
        """
        Create a ManaCard as if just charged from hand.
        Rule 405.1: multi-colored cards enter mana zone tapped.
        """
        is_multi = len(definition.civilizations) > 1
        return cls(definition=definition, is_tapped=is_multi)

    @property
    def id(self) -> int:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def civilizations(self) -> frozenset[Civilization]:
        return self.definition.civilizations

    def tap(self) -> None:
        self.is_tapped = True

    def untap(self) -> None:
        self.is_tapped = False

    def provides_civilization(self, civ: Civilization) -> bool:
        return civ in self.definition.civilizations

    def __repr__(self) -> str:
        tapped = "⟳" if self.is_tapped else "○"
        civs = "/".join(c.value[0] for c in self.definition.civilizations)
        return f"<Mana:{self.definition.name}[{civs}]{tapped}>"


# ── Shield Card ───────────────────────────────────────────────────────────────

@dataclass
class ShieldCard:
    """
    A card in the shield zone.

    VISIBILITY RULES (critical for information hiding):
    - is_revealed = False → neither player knows which card it is
      (though the owner knows it's IN their deck — deck composition is known)
    - is_revealed = True  → both players see it (after being broken)
    - The ENGINE always knows (needed for Shield Trigger detection)
    - The OBSERVATION for the opponent's shields is always hidden
    - The OBSERVATION for own shields is also hidden (you don't see your own shields)
    """
    definition:  CardDefinition
    uid:         str  = field(default_factory=_new_uid)
    is_revealed: bool = False    # True only during the break resolution window
    fortified_castles: list[CardDefinition] = field(default_factory=list)

    @property
    def id(self) -> int:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    def has_shield_trigger(self) -> bool:
        return self.definition.has_shield_trigger()

    def reveal(self) -> None:
        """Called when shield is broken — temporarily visible."""
        self.is_revealed = True

    def conceal(self) -> None:
        """Called if shield is returned face-down (some effects)."""
        self.is_revealed = False

    def __repr__(self) -> str:
        if self.is_revealed:
            return f"<Shield:{self.definition.name}[REVEALED]>"
        return f"<Shield:???[{self.uid}]>"


# ── Power Modifier ────────────────────────────────────────────────────────────

@dataclass
class PowerModifier:
    """
    A single power modification on a creature.
    Tracks source so it can be removed when source leaves play,
    and duration so it expires at the right time.
    """
    source_uid:    str            # uid of the card that granted this
    amount:        int            # positive = buff, negative = debuff
    duration:      str            # "permanent" | "until_end_of_turn" | "while_in_play"
    is_per_card:   bool = False   # True for "Power Attacker +1000 per fire card"
    per_card_zone: Optional[str] = None   # zone to count (e.g. "mana_zone")
    per_card_civ:  Optional[Civilization] = None  # civilization filter

    def __repr__(self) -> str:
        sign = "+" if self.amount >= 0 else ""
        return f"<PowerMod:{sign}{self.amount} [{self.duration}] from:{self.source_uid}>"


# ── Evolution Stack Entry ──────────────────────────────────────────────────────

@dataclass
class EvolutionStackEntry:
    """
    A single card in an evolution stack (rule 801).
    Stores card identity (uid + definition) and context needed for reconstruction.

    When an evolution creature is built:
      - The top card (newest) is stored in Creature.definition + Creature.uid
      - Previous cards are pushed into evolution_base as EvolutionStackEntry
      - Index 0 = card directly underneath, last = bottom of stack

    IMPORTANT (rule 801.2): The Creature object is the same creature, even after evolution.
    The uid remains the same. Only definition changes at the top.

    For reconstruction (rule 801.4), if the top card leaves:
      - Check next entry: if it can exist standalone, promote it to top
      - Apply rule 801.4c: no re-entry, inherit effects, no new summoning sickness
        (unless under-card was placed via NEO ability same turn — rule 802.3)
      - Apply rule 801.4d: orientation matches the leaving creature
    """
    definition:            CardDefinition
    uid:                   str  = field(default_factory=_new_uid)
    owner:                 int  = 0              # usually same as creature owner
    entered_turn:          int  = 0              # turn this card entered evolution stack
    neo_evolution_placed:  bool = False          # True if placed via NEO Evolution ability (rule 802.3)

    def __repr__(self) -> str:
        return f"<EvolutionEntry:{self.definition.name}[{self.uid}]>"


# ── Creature (Battle Zone Card) ───────────────────────────────────────────────

@dataclass
class Creature:
    """
    A creature card in the battle zone.
    This is the most complex zone object — tracks all in-play state.

    IMPORTANT: current_power is NOT stored here. It is always computed
    fresh from base_power + active modifiers. This prevents stale values.
    """
    definition:          CardDefinition
    uid:                 str  = field(default_factory=_new_uid)

    # Tap state
    is_tapped:           bool = False

    # Summoning sickness — can't attack until next turn (unless Speed Attacker)
    has_summoning_sickness: bool = True
    entered_turn:        int  = 0     # which turn it entered play

    # Power modifications active on this creature
    power_modifiers:     list[PowerModifier] = field(default_factory=list)

    # Evolution stack — cards underneath this creature (rule 801)
    # Index 0 = directly underneath, last = bottom of stack
    # Stores EvolutionStackEntry for full card identity + context
    evolution_stack:     list[EvolutionStackEntry] = field(default_factory=list)

    # Attached cards (cross gear, aura effects)
    attached_cards:      list[CardDefinition] = field(default_factory=list)

    # Temporary boolean flags set by effects
    # e.g. "cannot_attack", "cannot_be_blocked", "cannot_be_destroyed"
    temp_flags:          dict[str, bool] = field(default_factory=dict)

    # Tracks whether this creature attacked this turn (for once-per-turn checks)
    has_attacked_this_turn: bool = False

    # For "on_block" tracking
    is_blocking:         bool = False
    blocking_uid:        Optional[str] = None   # uid of creature it's blocking

    # Hyper Mode (rule 816)
    hyper_mode_released: bool = False

    # Sealed state (rule 116.2) — creature with seal is "ignored"
    # ignored = cannot attack/block, no abilities, cannot be chosen, doesn't tap/untap
    seals:               list = field(default_factory=list)  # list[CardDefinition] face-down

    @property
    def is_ignored(self) -> bool:
        """Rule 116.2: a creature with any seal attached is ignored."""
        return len(self.seals) > 0

    # God linking (rule 804) — component cards of a linked God
    linked_cards:        list = field(default_factory=list)  # list[CardDefinition]

    # Psychic / Dragheart double-face state (rules 805, 807)
    # face = 0: lower-cost face (default); face = 1: awakened/creature face
    # Preserved through flips per rule 805.5 / 807 (same creature object, uid unchanged)
    face:                int = 0

    # Psychic Super / Dragheart Super cell tracking (rules 806, 808)
    is_psychic_cell:     bool = False        # True when this is part of a linked Super Creature
    linked_cells:        list["Creature"] = field(default_factory=list)  # component cells for Super Creatures

    # King Cell combine tracking (rule 814)
    is_king_cell:        bool = False        # True when part of a combined King Creature

    # Static ability tracking — which CardEffect refs are currently applied
    static_effects:      list = field(default_factory=list)  # list[CardEffect] currently active

    # Controller — usually the owner but can change with some effects
    controller:          int = 0   # player index (0 or 1)
    owner:               int = 0   # who owns the card (for "return to owner's hand")

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def id(self) -> int:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def base_power(self) -> int:
        return self.definition.power or 0

    @property
    def civilizations(self) -> frozenset[Civilization]:
        # Rule 806.1f / 808.1e: Psychic/Dragheart Cells carry the full Super Creature's civilizations.
        # When part of a Super Creature, return the civilizations of all constituent cells combined.
        if self.is_psychic_cell and self.linked_cells:
            civs = set()
            for cell in self.linked_cells:
                civs.update(cell.definition.civilizations)
            return frozenset(civs)
        return self.definition.civilizations

    @property
    def races(self) -> frozenset[str]:
        """Effective races — includes races from evolution base if relevant."""
        return self.definition.races

    def compute_power(self, game_state_ref=None) -> int:
        """
        Always call this to get current power. Never cache.
        game_state_ref needed for per-card modifiers (Power Attacker) and CDA formulas.
        """
        # ── Power fix (from POWER_FIX effect action) ──────────────────────────────
        # Highest priority — overrides everything including CDA
        fixed_power = self.temp_flags.get("_power_fix")
        if fixed_power is not None:
            return int(fixed_power)

        # ── CDA (Characteristic-Defining Ability) ────────────────────────────────
        cda = self.definition.cda_formula_type
        if cda != CDAFormulaType.NONE:
            cda_base = self._compute_cda_power(game_state_ref)
            # CDA base replaces printed base_power; power_modifiers still apply on top
            total = cda_base
            for mod in self.power_modifiers:
                if mod.is_per_card and game_state_ref is not None:
                    count = game_state_ref.count_cards_in_zone(
                        player=self.controller,
                        zone=mod.per_card_zone,
                        civilization=mod.per_card_civ
                    )
                    total += mod.amount * count
                else:
                    total += mod.amount
            return total

        # ── Standard power computation ────────────────────────────────────────────
        total = self.base_power
        for mod in self.power_modifiers:
            if mod.is_per_card and game_state_ref is not None:
                count = game_state_ref.count_cards_in_zone(
                    player=self.controller,
                    zone=mod.per_card_zone,
                    civilization=mod.per_card_civ
                )
                total += mod.amount * count
            else:
                total += mod.amount
        # Add per-card global power bonuses (static aura effects)
        if game_state_ref is not None:
            total += game_state_ref.global_effects.get_per_card_power_bonus(self)
        return total

    def _compute_cda_power(self, game_state_ref=None) -> int:
        """
        Compute the CDA base power from the card's CDA formula.
        Called by compute_power() when cda_formula_type != NONE.
        """
        cda = self.definition.cda_formula_type

        if cda == CDAFormulaType.FIXED:
            return self.definition.cda_fixed_value

        if cda == CDAFormulaType.HAND_COUNT_MULT:
            if game_state_ref is None:
                return 0
            count = game_state_ref.count_cards_in_zone(
                player=self.controller,
                zone="hand",
                civilization=self.definition.cda_filter_civ
            )
            return count * self.definition.cda_multiplier

        if cda == CDAFormulaType.BATTLE_ZONE_COUNT_MULT:
            if game_state_ref is None:
                return 0
            count = game_state_ref.count_cards_in_zone(
                player=self.controller,
                zone="battle_zone",
                civilization=self.definition.cda_filter_civ
            )
            return count * self.definition.cda_multiplier

        if cda == CDAFormulaType.MANA_COUNT_MULT:
            if game_state_ref is None:
                return 0
            count = game_state_ref.count_cards_in_zone(
                player=self.controller,
                zone="mana_zone",
                civilization=self.definition.cda_filter_civ
            )
            return count * self.definition.cda_multiplier

        if cda == CDAFormulaType.SHIELD_COUNT_MULT:
            if game_state_ref is None:
                return 0
            count = game_state_ref.count_cards_in_zone(
                player=self.controller,
                zone="shield_zone",
                civilization=self.definition.cda_filter_civ
            )
            return count * self.definition.cda_multiplier

        # Fallback (should not reach here if cda != NONE)
        return self.base_power

    # ── Keyword checks (delegate to definition + temp flags) ──────────────────

    def has_keyword(self, kw: Keyword) -> bool:
        return self.definition.has_keyword(kw) or self.temp_flags.get(kw.value, False)

    def can_attack(self) -> bool:
        if self.is_ignored:          # rule 116.2: ignored creatures cannot attack
            return False
        if self.temp_flags.get("cannot_attack", False):
            return False
        if self.is_tapped:
            return False

        # Rule 805.6: Awakened Psychic Creatures have no summoning sickness
        if self.face == 1 and self.definition.card_subtype == CardSubtype.PSYCHIC:
            # face=1 indicates awakened Psychic; no sickness check needed
            return True

        # Rule 808.1a: Dragheart Super Creatures have no summoning sickness
        if (self.definition.card_subtype == CardSubtype.DRAGHEART 
            and self.linked_cells):
            # Dragheart Super Creature (has linked cells); no sickness check needed
            return True

        # Rule 805.6a: Even if awakened Psychic flips back, it has no sickness if in BZ since turn start
        # This is already handled by checking face=1 above, but keep the standard sickness check as fallback
        if self.has_summoning_sickness and not self.has_keyword(Keyword.SPEED_ATTACKER):
            return False
        return True

    def can_attack_players(self) -> bool:
        return not self.temp_flags.get("cannot_attack_players", False)

    def can_be_blocked(self) -> bool:
        return not (
            self.has_keyword(Keyword.CANNOT_BE_BLOCKED)
            or self.temp_flags.get("cannot_be_blocked", False)
        )

    def can_be_destroyed(self) -> bool:
        return not self.temp_flags.get("cannot_be_destroyed", False)

    def is_blocker(self) -> bool:
        """Rule 116.2: ignored creatures cannot block."""
        return self.has_keyword(Keyword.BLOCKER) and not self.is_ignored

    def is_guardman(self) -> bool:
        return self.has_keyword(Keyword.GUARDMAN) and not self.is_ignored

    def shields_broken_on_attack(self) -> int:
        return self.definition.shields_broken()

    def set_flag(self, flag: str, value: bool = True) -> None:
        self.temp_flags[flag] = value

    def clear_flag(self, flag: str) -> None:
        self.temp_flags.pop(flag, None)

    def clear_eot_flags(self) -> None:
        """Clear all temporary flags that expire end of turn."""
        eot_flags = {"cannot_attack", "cannot_be_blocked", "cannot_be_destroyed",
                     "cannot_attack_players", "power_attacker_active"}
        for flag in list(self.temp_flags):
            if flag in eot_flags:
                del self.temp_flags[flag]

    def remove_eot_power_modifiers(self) -> None:
        """Remove power modifiers that expire at end of turn."""
        self.power_modifiers = [
            m for m in self.power_modifiers
            if m.duration != "until_end_of_turn"
        ]

    def tap(self) -> None:
        self.is_tapped = True

    def untap(self) -> None:
        self.is_tapped = False

    def clear_summoning_sickness(self) -> None:
        self.has_summoning_sickness = False

    # ── Evolution stack helpers (rule 801) ─────────────────────────────────────

    def is_evolution_creature(self) -> bool:
        """True if this creature has cards stacked underneath it."""
        return len(self.evolution_stack) > 0

    def get_evolution_base_definitions(self) -> list[CardDefinition]:
        """
        Backward-compatibility helper: return all definitions in the evolution stack.
        Used for rule checks that reference "evolution base card".
        Rule 200.3a: characteristics of under-cards are ignored during normal gameplay.
        """
        return [entry.definition for entry in self.evolution_stack]

    def get_under_card_uids(self) -> list[str]:
        """Return list of UIDs for all cards in evolution stack (top to bottom)."""
        return [entry.uid for entry in self.evolution_stack]

    def push_to_evolution_stack(self, entry: EvolutionStackEntry) -> None:
        """Push a card onto the evolution stack (becomes the new 'directly underneath')."""
        self.evolution_stack.insert(0, entry)

    def pop_from_evolution_stack(self) -> Optional[EvolutionStackEntry]:
        """Remove and return the top card from evolution stack, or None if empty."""
        if self.evolution_stack:
            return self.evolution_stack.pop(0)
        return None

    def peek_under_card(self) -> Optional[EvolutionStackEntry]:
        """Look at the top card in the evolution stack without removing it."""
        if self.evolution_stack:
            return self.evolution_stack[0]
        return None

    def get_all_under_cards(self) -> list[EvolutionStackEntry]:
        """Return copy of entire evolution stack (for iteration, inspection)."""
        return list(self.evolution_stack)

    # ── NEO Evolution state helpers (rule 802) ─────────────────────────────────

    def is_neo_evolution_creature(self) -> bool:
        """
        Rule 802.2: A NEO Creature is treated as a "NEO Evolution Creature" while it
        is in the Battle Zone with a card underneath it via the NEO Evolution ability,
        or while it is attempting to enter the Battle Zone as a NEO Evolution Creature.
        While in other zones, it is treated as a non-evolution creature.
        """
        # Must have the NEO or G-NEO subtype
        if self.definition.card_subtype not in (CardSubtype.NEO, CardSubtype.G_NEO):
            return False
        
        # Must have an underlying card
        return self.is_evolution_creature()

    def check_neo_summoning_sickness_recovery(self, current_turn: int) -> bool:
        """
        Rule 802.3: If a NEO Creature has a card underneath it, it is treated as a
        "NEO Evolution Creature" and does not suffer from summoning sickness. However,
        if the underlying card is removed by some method during the same turn it was
        played, it is no longer a "NEO Evolution Creature" and cannot attack due to
        "summoning sickness."

        This helper checks if a NEO creature that lost its underlying card during the
        same turn it was NEO-evolved should have summoning sickness restored.

        Returns True if summoning sickness should be restored (i.e., the creature was
        NEO-evolved this turn and now has no underlying card).
        """
        # Only applies to NEO creatures
        if self.definition.card_subtype not in (CardSubtype.NEO, CardSubtype.G_NEO):
            return False
        
        # Must have entered this turn (to check same-turn rule)
        if self.entered_turn != current_turn:
            return False
        
        # Now has no underlying card
        if self.is_evolution_creature():
            return False
        
        # Check if there was a NEO-evolution card placed this turn
        # (This is checked via the temp_flags set when the card was evolved)
        if self.temp_flags.get("_neo_evolved_this_turn", False):
            return True
        
        return False

    def get_neo_underlying_entry_info(self) -> Optional[tuple[EvolutionStackEntry, bool]]:
        """
        Helper to get the current underlying card and whether it was placed via
        NEO Evolution ability (rule 802.3).

        Rule 802.3: Whether the underlying card was placed via NEO ability matters
        for determining if summoning sickness applies when it's removed.

        Returns: Tuple of (EvolutionStackEntry, was_placed_via_neo_ability) or None
        """
        under_entry = self.peek_under_card()
        if not under_entry:
            return None
        
        return (under_entry, under_entry.neo_evolution_placed)

    # ── Static ability application ───────────────────────────────────────────────

    def apply_static_effects(self, game_state) -> None:
        """
        Read this creature's static effects from its CardDefinition and apply them
        to the game state. Called when the creature enters the battle zone.

        Handles:
          - Power modifiers to other creatures (via GlobalEffectRegistry)
          - Keyword grants to other creatures (via GlobalEffectRegistry)
          - Registration of per-card global effects
        """
        from core.global_effects import GlobalEffect, GlobalEffectType

        effects = self.definition.get_static_effects()
        for card_effect in effects:
            self.static_effects.append(card_effect)

            action = card_effect.effect_action
            target = card_effect.effect_target
            value = card_effect.effect_value

            if action == EffectAction.POWER_MODIFY:
                # e.g. "your other Fire creatures get +1000 power"
                amount = value.get("amount", 0)
                filter_civ = target.get("civilization")
                filter_race = target.get("race")
                target_scope = target.get("scope", "own")  # "own" | "opponent" | "all"
                exclude_self = target.get("exclude_self", True)

                eff = GlobalEffect(
                    effect_type=GlobalEffectType.PER_CARD_POWER_MOD,
                    applied_by_uid=self.uid,
                    applied_by_card=self.id,
                    controller=self.controller,
                    target_player=None if target_scope == "all" else self.controller,
                    duration="while_in_play",
                    power_mod_amount=amount,
                    power_mod_target=target_scope,
                    per_card_filter_civ=filter_civ,
                    per_card_filter_race=filter_race,
                    per_card_filter_self=exclude_self,
                )
                game_state.global_effects.add(eff)

            elif action == EffectAction.GIVE_KEYWORD:
                # e.g. "your creatures gain Blocker"
                keyword = value.get("keyword")
                if keyword is None:
                    keyword = value.get("granted_keyword")
                if keyword is None:
                    continue
                filter_civ = target.get("civilization")
                filter_race = target.get("race")
                target_scope = target.get("scope", "own")

                eff = GlobalEffect(
                    effect_type=GlobalEffectType.PER_CARD_KEYWORD_GRANT,
                    applied_by_uid=self.uid,
                    applied_by_card=self.id,
                    controller=self.controller,
                    target_player=None if target_scope == "all" else self.controller,
                    duration="while_in_play",
                    grant_keyword=keyword,
                    grant_to_civ=filter_civ,
                    grant_to_race=filter_race,
                    grant_to_controller=self.controller if target_scope == "own" else None,
                )
                game_state.global_effects.add(eff)

            elif action == EffectAction.NONE and card_effect.is_replacement_effect():
                # Replacement effect — register with ReplacementEffectRegistry (rule 609)
                from engine.replacement import ReplacementEffect, EventType

                # Determine which event type this replacement applies to
                event_type = self._resolve_replacement_event_type(card_effect)
                if event_type is not None:
                    applies_to = card_effect.effect_target.get("scope", "self")
                    rep = ReplacementEffect(
                        event_type=event_type,
                        source_uid=self.uid,
                        source_card_id=self.id,
                        controller=self.controller,
                        condition=card_effect.trigger_condition,
                        replacement_action=card_effect.effect_value.get("action", "prevent"),
                        replacement_value=card_effect.effect_value,
                        applies_to=applies_to,
                    )
                    game_state.replacement_effects.register(rep)

    @staticmethod
    def _resolve_replacement_event_type(card_effect: CardEffect) -> Optional[str]:
        """
        Map a replacement CardEffect's trigger_event to an EventType constant.
        Returns None if the event type is not yet supported.
        """
        from engine.replacement import EventType
        from core.enums import TriggerEvent

        te = card_effect.trigger_event
        if te == TriggerEvent.ON_DESTROY:
            return EventType.DESTROY
        elif te == TriggerEvent.ON_LEAVE_BATTLE_ZONE:
            return EventType.LEAVE_BATTLE_ZONE
        # Other trigger events can be mapped as support expands
        return None

    def remove_static_effects(self, game_state) -> None:
        """
        Remove all static effects that this creature has applied to the game state.
        Called when the creature leaves the battle zone.

        Cleans up:
        - GlobalEffectRegistry (power mods, keyword grants, etc.)
        - ReplacementEffectRegistry (rule 609 replacement effects)
        """
        self.static_effects.clear()
        game_state.global_effects.remove_by_source(self.uid)
        game_state.replacement_effects.unregister(self.uid)

    def __repr__(self) -> str:
        state = []
        if self.is_tapped: state.append("tapped")
        if self.has_summoning_sickness: state.append("sick")
        if self.temp_flags: state.append(str(self.temp_flags))
        state_str = f" ({', '.join(state)})" if state else ""
        return f"<Creature:{self.definition.name}[{self.uid}] {self.base_power}{state_str}>"


# ── Graveyard Card ────────────────────────────────────────────────────────────

@dataclass
class GraveyardCard:
    """
    A card in the graveyard.
    Graveyard order matters (newest first = index 0).
    Tracks how it got there — relevant for some trigger conditions.
    """
    definition:      CardDefinition
    uid:             str = field(default_factory=_new_uid)
    died_from:       str = "unknown"   # "battle" | "spell" | "effect" | "discarded"
    died_on_turn:    int = 0

    @property
    def id(self) -> int:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    def __repr__(self) -> str:
        return f"<GY:{self.definition.name}[{self.uid}] via:{self.died_from}>"
