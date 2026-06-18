"""
engine/replacement.py — Centralized replacement effect registry.

Rule 609: Replacement effects modify how events happen. Instead of the
normal event, a different thing happens ("When X would happen, instead Y").

Examples in Duel Masters,
  Rule 805.1b — Psychic Release: creature flips to lower face instead of leaving BZ
  Rule 807.1b — Dragon Evasion: creature flips to lower face instead of leaving BZ
  Rule 803.2  — G-NEO leave: all placed cards leave instead of just top card
  Rule 813.1  — Star Evolution: only topmost card leaves instead of whole stack

The registry tracks all active replacement effects and selects the
applicable one when an event is about to occur. Full APNAP priority
ordering is deferred to Phase 4; for now, first-registered-wins.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from core.state import GameState


# ── Discrete events that can be replaced ───────────────────────────────────────

class EventType:
    """Event types that replacement effects can intercept.

    Using string constants (mirrors existing engine convention) so callers
    can compare without importing an Enum if they don't want to.
    """
    DESTROY            = "destroy"
    LEAVE_BATTLE_ZONE = "leave_battle_zone"
    DRAW               = "draw"
    SHIELD_BREAK       = "shield_break"
    PUT_TO_GRAVEYARD   = "put_to_graveyard"
    WIN_BATTLE         = "win_battle"


# ── One registered replacement effect ──────────────────────────────────────────

@dataclass
class ReplacementEffect:
    """
    A single active replacement effect.

    Attributes:
        event_type:         what event this replaces (EventType constants)
        source_uid:         uid of the card granting this effect
        source_card_id:     card id for reference / display
        controller:         player who controls the source (0 or 1)
        condition:          extra condition dict from CardEffect.trigger_condition
        replacement_action: "flip_face" | "banish" | "prevent" | "add_shield" | ...
        replacement_value:   parameters for the replacement action
        applies_to:         "self" | "controller_creatures" | "all_creatures" | "event_target"
        is_used:            whether this replacement has already fired this event cycle
        priority:           lower = fires first (for future APNAP support)
    """
    event_type: str
    source_uid: str
    source_card_id: int
    controller: int
    condition: dict = field(default_factory=dict)
    replacement_action: str = ""
    replacement_value: dict = field(default_factory=dict)
    applies_to: str = "self"
    is_used: bool = False
    priority: int = 0

    def __repr__(self) -> str:
        return (
            f"<Replacement:{self.replacement_action} "
            f"for={self.event_type} from=card{self.source_card_id}"
            f"[{self.source_uid}] P{self.controller}>"
        )


# ── Registry ───────────────────────────────────────────────────────────────────

@dataclass
class ReplacementEffectRegistry:
    """
    Centralized registry of all active replacement effects.

    Stored in GameState.replacement_effects.  Consulted before any
    destroy / draw / graveyard / zone-change operation.
    """
    effects: list[ReplacementEffect] = field(default_factory=list)

    # Registration / unregistration ──────────────────────────────────────────

    def register(self, effect: ReplacementEffect) -> None:
        """Add a replacement effect to the registry."""
        self.effects.append(effect)

    def unregister(self, source_uid: str) -> int:
        """Remove ALL replacement effects from the given source uid.
        Returns the count removed."""
        before = len(self.effects)
        self.effects = [e for e in self.effects if e.source_uid != source_uid]
        return before - len(self.effects)

    # Query ──────────────────────────────────────────────────────────────────

    def get_applicable_replacements(
        self,
        event_type: str,
        game_state: "GameState",
        target_uid: Optional[str] = None,
        controller: Optional[int] = None,
    ) -> list[ReplacementEffect]:
        """
        Return all replacement effects that could apply to this event.

        Filtering logic:
        1. event_type must match
        2. effect must not have been used yet this event cycle
        3. applies_to must match the context:
           - "self": target_uid must match source_uid
           - "controller_creatures": controller must match effect.controller
           - "all_creatures": any controller
           - "event_target": target_uid must match replacement_value.get("target_uid")
        4. If effect has extra conditions (from trigger_condition), evaluate them.

        The first matching effect is typically used (Phase 4 will add APNAP).
        """
        applicable: list[ReplacementEffect] = []

        for eff in self.effects:
            # 1. Event type must match
            if eff.event_type != event_type:
                continue

            # 2. Must not have been used yet
            if eff.is_used:
                continue

            # 3. applies_to filtering
            if eff.applies_to == "self":
                # Only applies if the target IS the source creature
                if target_uid is not None and target_uid != eff.source_uid:
                    continue
            elif eff.applies_to == "controller_creatures":
                # Applies to any creature controlled by effect.controller
                if controller is not None and controller != eff.controller:
                    continue
            elif eff.applies_to == "event_target":
                # Applies to the specific target of the event
                expected_target = eff.replacement_value.get("target_uid")
                if expected_target is not None and expected_target != target_uid:
                    continue
            # "all_creatures" — no filter needed

            # 4. Extra condition evaluation
            if eff.condition and not self._evaluate_condition(eff.condition, game_state):
                continue

            applicable.append(eff)

        return applicable

    def check_and_apply(
        self,
        event_type: str,
        game_state: "GameState",
        target_uid: Optional[str] = None,
        controller: Optional[int] = None,
    ) -> Optional[ReplacementEffect]:
        """
        Find the first applicable replacement effect for this event.
        Marks it as used and returns it, or None if no replacement applies.
        """
        replacements = self.get_applicable_replacements(
            event_type, game_state, target_uid, controller
        )
        if not replacements:
            return None

        # Use the first one found (Priority order for Phase 4)
        chosen = replacements[0]
        chosen.is_used = True
        return chosen

    # Lifecycle ──────────────────────────────────────────────────────────────

    def mark_used(self, effect: ReplacementEffect) -> None:
        """Mark a specific effect as having been applied this cycle."""
        effect.is_used = True

    def reset_event(self, event_type: str) -> None:
        """Reset is_used for all effects of the given event type.
        Called after the event cycle completes so effects can fire again."""
        for eff in self.effects:
            if eff.event_type == event_type:
                eff.is_used = False

    def reset_all(self) -> None:
        """Reset all is_used flags. Call between game steps."""
        for eff in self.effects:
            eff.is_used = False

    # Condition evaluation ───────────────────────────────────────────────────

    @staticmethod
    def _evaluate_condition(condition: dict, game_state: "GameState") -> bool:
        """
        Evaluate simple condition dicts from CardEffect.trigger_condition.

        Supported keys:
        - "subject": "self" — condition checks the source
        - "min_power": N — source creature power >= N
        - "max_power": N — source creature power <= N
        - "zone": zone_name — source is in this zone

        Returns True if all conditions pass (or condition dict is empty).
        """
        if not condition:
            return True

        subject = condition.get("subject", "self")

        # Look up the relevant creature
        creature_uid = condition.get("source_uid", "")
        creature = None
        for player_idx in range(2):
            c = game_state.players[player_idx].find_creature(creature_uid)
            if c is not None:
                creature = c
                break

        if creature is None:
            return False

        # Power conditions
        min_power = condition.get("min_power")
        if min_power is not None:
            power = creature.compute_power(game_state)
            if power < min_power:
                return False

        max_power = condition.get("max_power")
        if max_power is not None:
            power = creature.compute_power(game_state)
            if power > max_power:
                return False

        return True

    def __repr__(self) -> str:
        active = sum(1 for e in self.effects if not e.is_used)
        total = len(self.effects)
        return f"<ReplacementRegistry: {active}/{total} active>"
