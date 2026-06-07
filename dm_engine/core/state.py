"""
core/state.py — GameState, the master object for the entire engine.

Every piece of game information lives here. The engine is a pure function:
    (GameState, Action) → GameState

GameState is NEVER mutated. Every action returns a new copy.
This is mandatory for MCTS which needs to branch from any state.

Also contains:
  AttackContext   — tracks the current attack in progress
  EffectStack     — pending triggers and awaited choices
  TurnInfo        — current turn metadata
"""

from __future__ import annotations
from dataclasses import dataclass, field
from copy import deepcopy
from typing import Optional, Any

from .enums import Phase, GameResult, Zone, Civilization, ActionType
from .cards import CardDefinition, CardEffect
from .zones import Creature, HandCard, ShieldCard
from .player_state import PlayerState
from .global_effects import GlobalEffectRegistry, GlobalEffect


# ── Pending Trigger ───────────────────────────────────────────────────────────

@dataclass
class PendingTrigger:
    """
    A triggered effect waiting to be resolved.
    Queued when a trigger event fires, resolved in order.
    """
    effect:          CardEffect
    source_uid:      str            # uid of the card that triggered this
    source_card_id:  int            # card id for lookup
    controller:      int            # player who controls the source
    trigger_data:    dict = field(default_factory=dict)  # context (e.g. "broke shield N")

    def __repr__(self) -> str:
        return (
            f"<PendingTrigger: {self.effect.effect_action.value} "
            f"from card{self.source_card_id}[{self.source_uid}] "
            f"by P{self.controller}>"
        )


# ── Awaited Choice ────────────────────────────────────────────────────────────

@dataclass
class AwaitedChoice:
    """
    The engine is waiting for a player to make a choice.
    This pauses resolution until the choice action is submitted.

    choice_type:
      "yes_no"           — optional effect: use it or not?
      "select_target"    — pick one or more targets from valid_options
      "select_card"      — pick a card from a list (hand, deck search)
      "select_mana"      — pick which mana to tap
      "shield_trigger"   — use shield trigger or add to hand?
      "ninja_strike"     — declare ninja strike or let battle proceed?
    """
    choice_type:    str
    player:         int             # who must make the choice
    effect:         Optional[CardEffect]   # the effect requiring the choice
    source_uid:     str             # source of the effect
    valid_options:  list[Any]       # list of valid choices (uids, card_ids, booleans)
    min_choices:    int = 1
    max_choices:    int = 1
    prompt:         str = ""        # human-readable description

    def __repr__(self) -> str:
        return (
            f"<AwaitedChoice:{self.choice_type} "
            f"P{self.player} options={len(self.valid_options)}>"
        )


# ── Effect Stack ──────────────────────────────────────────────────────────────

@dataclass
class EffectStack:
    """
    Manages pending triggers and awaited choices.

    pending_triggers: effects queued but not yet resolved (FIFO)
    awaited_choice:   if set, the engine is paused waiting for player input
    shield_trigger_queue: shields broken but not yet offered as triggers
    """
    pending_triggers:      list[PendingTrigger] = field(default_factory=list)
    awaited_choice:        Optional[AwaitedChoice] = None
    shield_trigger_queue:  list[tuple[int, ShieldCard]] = field(default_factory=list)
    # (player_index, shield_card) — shields broken but trigger not yet resolved

    def has_pending(self) -> bool:
        return bool(self.pending_triggers) or self.awaited_choice is not None

    def is_waiting_for_choice(self) -> bool:
        return self.awaited_choice is not None

    def add_trigger(self, trigger: PendingTrigger) -> None:
        self.pending_triggers.append(trigger)

    def pop_next_trigger(self) -> Optional[PendingTrigger]:
        if self.pending_triggers:
            return self.pending_triggers.pop(0)
        return None

    def set_choice(self, choice: AwaitedChoice) -> None:
        self.awaited_choice = choice

    def clear_choice(self) -> None:
        self.awaited_choice = None

    def add_shield_trigger(self, player: int, shield: ShieldCard) -> None:
        self.shield_trigger_queue.append((player, shield))

    def pop_shield_trigger(self) -> Optional[tuple[int, ShieldCard]]:
        if self.shield_trigger_queue:
            return self.shield_trigger_queue.pop(0)
        return None

    def __repr__(self) -> str:
        parts = []
        if self.pending_triggers:
            parts.append(f"triggers={len(self.pending_triggers)}")
        if self.awaited_choice:
            parts.append(f"waiting={self.awaited_choice.choice_type}")
        if self.shield_trigger_queue:
            parts.append(f"st_queue={len(self.shield_trigger_queue)}")
        return f"<EffectStack: {', '.join(parts) or 'empty'}>"


# ── Attack Context ────────────────────────────────────────────────────────────

@dataclass
class AttackContext:
    """
    Tracks the state of the current attack in progress.
    Cleared after each attack fully resolves.

    An "attack" goes through sub-phases: declare → block_offered → battle/break → resolve.
    This context carries information across those sub-steps.
    """
    attacker_uid:       str             # uid of attacking creature
    attacker_player:    int             # who is attacking (0 or 1)
    target_type:        str             # "player" | "creature"
    target_uid:         Optional[str]   # uid of target creature (if creature attack)

    blocker_uid:        Optional[str] = None   # uid of declared blocker
    blocker_player:     Optional[int] = None

    # Sub-phase tracking
    block_was_offered:  bool = False
    block_was_declared: bool = False
    shields_broken:     int  = 0

    # Ninja Strike was used
    ninja_strike_used:      bool = False
    ninja_strike_card_uid:  Optional[str] = None

    # Set only when the attack actually reaches the defending player while
    # they have no shields. Breaking the last shield is not a direct attack.
    received_direct_attack: bool = False

    @property
    def is_attacking_player(self) -> bool:
        return self.target_type == "player"

    @property
    def is_attacking_creature(self) -> bool:
        return self.target_type == "creature"

    @property
    def defending_player(self) -> int:
        return 1 - self.attacker_player

    def __repr__(self) -> str:
        target = f"P{self.defending_player}" if self.is_attacking_player else f"creature[{self.target_uid}]"
        blocked = f" BLOCKED by [{self.blocker_uid}]" if self.blocker_uid else ""
        return f"<AttackContext: [{self.attacker_uid}]→{target}{blocked}>"


# ── Turn Info ─────────────────────────────────────────────────────────────────

@dataclass
class TurnInfo:
    """Current turn metadata."""
    turn_number:     int   = 1
    active_player:   int   = 0    # 0 or 1
    phase:           Phase = Phase.START_OF_TURN

    # First player doesn't draw on turn 1
    first_player:    int   = 0

    @property
    def inactive_player(self) -> int:
        return 1 - self.active_player

    def is_first_turn(self) -> bool:
        return self.turn_number == 1

    def should_skip_draw(self) -> bool:
        """First player skips draw on turn 1."""
        return self.is_first_turn() and self.active_player == self.first_player

    def __repr__(self) -> str:
        return f"<TurnInfo: T{self.turn_number} P{self.active_player} {self.phase}>"


# ── Action History Entry ──────────────────────────────────────────────────────

@dataclass
class ActionRecord:
    """One recorded action for history / replay / debugging."""
    turn_number:   int
    player:        int
    phase:         Phase
    action_type:   ActionType
    card_id:       Optional[int]
    target_uid:    Optional[str]
    extra:         dict = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"T{self.turn_number}:P{self.player}:{self.action_type.value}"
            f"(card={self.card_id} target={self.target_uid})"
        )


# ── GameState ─────────────────────────────────────────────────────────────────

@dataclass
class GameState:
    """
    Complete state of a Duel Masters game at one instant.

    IMMUTABILITY CONVENTION:
    The engine never mutates this object. Every state-changing operation
    calls state.copy() first, modifies the copy, and returns it.
    The original state is always preserved for MCTS branching.

    INFORMATION MODEL:
    The GameState always has FULL information (the engine needs it for correct
    rule enforcement). What each player CAN OBSERVE is computed separately
    by observation.py — that module is what the neural net and bot receive.

    GLOBAL EFFECTS:
    Some effects apply game-wide (e.g. "players can only cast Light spells").
    These live in global_effects and are checked by the action generator.
    """

    # ── Core state ────────────────────────────────────────────────────────────
    players:        tuple[PlayerState, PlayerState]

    # ── Turn management ───────────────────────────────────────────────────────
    turn_info:      TurnInfo = field(default_factory=TurnInfo)

    # ── Effect resolution ─────────────────────────────────────────────────────
    effect_stack:   EffectStack = field(default_factory=EffectStack)

    # ── Attack tracking ───────────────────────────────────────────────────────
    # None when no attack in progress
    attack_context: Optional[AttackContext] = None

    # ── Game-wide effects ─────────────────────────────────────────────────────
    global_effects: GlobalEffectRegistry = field(default_factory=GlobalEffectRegistry)

    # ── Game result ───────────────────────────────────────────────────────────
    result:         GameResult = GameResult.IN_PROGRESS

    # ── Action history (for replay, debugging, UI) ────────────────────────────
    # Last N actions only — keep small for MCTS performance
    history:        list[ActionRecord] = field(default_factory=list)
    max_history:    int = 20

    # ── Game ID (for logging) ─────────────────────────────────────────────────
    game_id:        str = ""

    # ─────────────────────────────────────────────────────────────────────────
    # Convenience accessors
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def active_player(self) -> int:
        return self.turn_info.active_player

    @property
    def inactive_player(self) -> int:
        return self.turn_info.inactive_player

    @property
    def current_phase(self) -> Phase:
        return self.turn_info.phase

    @property
    def turn_number(self) -> int:
        return self.turn_info.turn_number

    def player(self, idx: int) -> PlayerState:
        return self.players[idx]

    def active(self) -> PlayerState:
        return self.players[self.active_player]

    def inactive(self) -> PlayerState:
        return self.players[self.inactive_player]

    def is_terminal(self) -> bool:
        return self.result != GameResult.IN_PROGRESS

    def winner(self) -> Optional[int]:
        if self.result == GameResult.PLAYER_0_WINS:
            return 0
        if self.result == GameResult.PLAYER_1_WINS:
            return 1
        return None   # no winner or in progress

    def is_in_attack(self) -> bool:
        return self.attack_context is not None

    def is_waiting_for_choice(self) -> bool:
        return self.effect_stack.is_waiting_for_choice()

    # ─────────────────────────────────────────────────────────────────────────
    # Cross-player queries (for effect evaluation)
    # ─────────────────────────────────────────────────────────────────────────

    def count_cards_in_zone(
        self,
        player: int,
        zone: str,
        civilization: Optional[Civilization] = None
    ) -> int:
        """
        Used by Creature.compute_power() for Power Attacker.
        zone: "mana_zone" | "battle_zone" | "hand" | "graveyard" | "shield_zone" | "deck"
        """
        from .enums import Zone as ZoneEnum
        zone_enum = ZoneEnum(zone)
        return self.players[player].count_cards_in_zone(zone_enum, civilization)

    def all_creatures(self) -> list[tuple[int, Creature]]:
        """All creatures on the field: [(player_idx, creature), ...]"""
        result = []
        for i in range(2):
            for c in self.players[i].battle_zone:
                result.append((i, c))
        return result

    def find_creature_anywhere(self, uid: str) -> Optional[tuple[int, Creature]]:
        """Find a creature by uid across both battle zones."""
        for i in range(2):
            c = self.players[i].find_creature(uid)
            if c:
                return (i, c)
        return None

    def get_creature_controller(self, uid: str) -> Optional[int]:
        result = self.find_creature_anywhere(uid)
        return result[0] if result else None

    # ─────────────────────────────────────────────────────────────────────────
    # State copy — mandatory for every state-changing operation
    # ─────────────────────────────────────────────────────────────────────────

    def copy(self) -> "GameState":
        """
        Deep copy of the entire game state.
        Called before every action to preserve immutability.

        Performance note: deepcopy is the safe default. If MCTS becomes
        a bottleneck, optimize with __copy__ using selective shallow copies
        for immutable objects (CardDefinition instances are shared by reference —
        they never change so shallow copy is safe for them).
        """
        return deepcopy(self)

    # ─────────────────────────────────────────────────────────────────────────
    # History management
    # ─────────────────────────────────────────────────────────────────────────

    def record_action(
        self,
        action_type: ActionType,
        player: int,
        card_id: Optional[int] = None,
        target_uid: Optional[str] = None,
        extra: Optional[dict] = None
    ) -> None:
        """Append to history, keeping only the last max_history entries."""
        record = ActionRecord(
            turn_number=self.turn_number,
            player=player,
            phase=self.current_phase,
            action_type=action_type,
            card_id=card_id,
            target_uid=target_uid,
            extra=extra or {},
        )
        self.history.append(record)
        if len(self.history) > self.max_history:
            self.history.pop(0)

    # ─────────────────────────────────────────────────────────────────────────
    # Debug display
    # ─────────────────────────────────────────────────────────────────────────

    def display(self) -> str:
        lines = [
            f"╔══════════════════════════════════════════",
            f"║  Game {self.game_id or 'N/A'} | {self.turn_info}",
            f"║  Result: {self.result.value}",
            f"╠══════════════════════════════════════════",
        ]
        for i in range(2):
            lines.append(self.players[i].display())
            lines.append("║")
        if not self.global_effects.is_empty():
            lines.append("║  GLOBAL EFFECTS:")
            for i in range(2):
                for r in self.global_effects.active_restrictions_for_player(i):
                    lines.append(f"║    P{i}: {r}")
        if self.attack_context:
            lines.append(f"║  ATTACK IN PROGRESS: {self.attack_context}")
        if self.effect_stack.has_pending():
            lines.append(f"║  EFFECT STACK: {self.effect_stack}")
        lines.append("╚══════════════════════════════════════════")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"<GameState T{self.turn_number} P{self.active_player} "
            f"{self.current_phase} result={self.result.value}>"
        )
