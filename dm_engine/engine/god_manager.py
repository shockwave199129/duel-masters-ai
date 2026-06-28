"""engine/god_manager.py — Gods (rule 804) G-Link management."""

from __future__ import annotations

from typing import Optional

from core.cards import CardDefinition
from core.enums import Civilization
from core.state import GameState
from core.zones import Creature, GraveyardCard, HandCard


class GodManager:
    """Manages God creature linking and validation (rule 804)."""

    # ── Validation ───────────────────────────────────────────────────────────

    @staticmethod
    def validate_god_link(
        source_card: CardDefinition,
        linked_card: CardDefinition,
    ) -> bool:
        """
        Validate whether two God cards can link together (rule 804.2).

        Requirements:
        1. Both cards must share the same god_link_group
        2. G-Link slot metadata must reference the partner slug
        """
        if not source_card.god_link_group or not linked_card.god_link_group:
            return False
        if source_card.god_link_group != linked_card.god_link_group:
            return False
        return GodManager._slots_allow_link(source_card, linked_card)

    @staticmethod
    def _slots_allow_link(
        source_card: CardDefinition,
        linked_card: CardDefinition,
    ) -> bool:
        if not source_card.god_glink_slots or not linked_card.god_glink_slots:
            return False
        linked_slug = linked_card.slug
        source_slug = source_card.slug
        if any(partner == linked_slug for _, partner in source_card.god_glink_slots):
            return True
        if any(partner == source_slug for _, partner in linked_card.god_glink_slots):
            return True
        return False

    @staticmethod
    def get_linkable_gods(
        state: GameState,
        player: int,
        source_card: CardDefinition,
    ) -> list[CardDefinition]:
        """Return hand cards that can G-Link with *source_card*."""
        if not source_card.god_link_group:
            return []
        linkable: list[CardDefinition] = []
        for hand_card in state.players[player].hand:
            if GodManager.validate_god_link(source_card, hand_card.definition):
                linkable.append(hand_card.definition)
        return linkable

    @staticmethod
    def is_valid_god_configuration(creatures: list[Creature]) -> bool:
        """
        Validate linked Gods form a legal layout (rule 804.1 / 804.2b).

        All members must share god_link_group; count must match a valid layout.
        """
        if len(creatures) < 2:
            return False

        god_group = creatures[0].definition.god_link_group
        if not god_group:
            return False

        for creature in creatures:
            if creature.definition.god_link_group != god_group:
                return False

        valid_counts = {2, 3, 4, 6, 8, 9, 12, 16}
        return len(creatures) in valid_counts

    # ── Link execution ───────────────────────────────────────────────────────

    @staticmethod
    def is_god_link(creature: Creature) -> bool:
        return bool(creature.temp_flags.get("god_linked")) and len(creature.linked_cells) >= 2

    @staticmethod
    def get_god_members(creature: Creature) -> list[Creature]:
        if GodManager.is_god_link(creature):
            return list(creature.linked_cells)
        return [creature]

    @staticmethod
    def get_aggregated_names(creature: Creature) -> frozenset[str]:
        """Rule 804.1b: linked God possesses all component names."""
        return frozenset(m.definition.name for m in GodManager.get_god_members(creature))

    @staticmethod
    def get_aggregated_civilizations(creature: Creature) -> frozenset[Civilization]:
        """Rule 804.1b: linked God possesses all component civilizations."""
        civs: set[Civilization] = set()
        for member in GodManager.get_god_members(creature):
            civs.update(member.definition.civilizations)
        return frozenset(civs)

    @staticmethod
    def link_gods(
        state: GameState,
        player: int,
        primary: Creature,
        hand_card: HandCard,
    ) -> Optional[Creature]:
        """
        Link a God from hand onto an existing God in the battle zone (804.3).

        The primary creature keeps its uid; new member is stored in linked_cells.
        Tap state is preserved on the primary; result is tapped if any member was
        tapped (804.3 / 804.3a).
        """
        if not GodManager.validate_god_link(primary.definition, hand_card.definition):
            return None

        p_state = state.players[player]
        if hand_card not in p_state.hand:
            return None

        new_member = Creature(
            definition=hand_card.definition,
            uid=hand_card.uid,
            controller=player,
            owner=player,
            entered_turn=state.turn_number,
            has_summoning_sickness=False,  # 804.5
        )

        if GodManager.is_god_link(primary):
            members = list(primary.linked_cells)
        else:
            members = [primary]
            primary.temp_flags["god_linked"] = True

        members.append(new_member)
        if not GodManager.is_valid_god_configuration(members):
            return None

        p_state.hand.remove(hand_card)

        for member in members:
            if member is primary:
                continue
            if member in p_state.battle_zone:
                p_state.battle_zone.remove(member)
                state.global_effects.remove_by_source(member.uid)

        if any(m.is_tapped for m in members):
            primary.is_tapped = True

        primary.linked_cells = members
        primary.temp_flags["god_linked"] = True
        primary.has_summoning_sickness = False  # 804.5

        primary.remove_static_effects(state)
        primary.apply_static_effects(state)
        GodManager._apply_member_static_effects(state, primary)

        return primary

    @staticmethod
    def _apply_member_static_effects(state: GameState, primary: Creature) -> None:
        """Apply static abilities from all linked God component definitions (804.1b)."""
        if not GodManager.is_god_link(primary):
            return
        for member in primary.linked_cells:
            if member is primary:
                continue
            saved = primary.definition
            primary.definition = member.definition
            primary.apply_static_effects(state)
            primary.definition = saved

    # ── Detach / leave ───────────────────────────────────────────────────────

    @staticmethod
    def detach_god_link(state: GameState, player: int, primary: Creature) -> None:
        """
        Split an invalid or broken God link into separate battle-zone Gods (804.4).

        Each member inherits effects that were on the linked creature (804.4).
        """
        p_state = state.players[player]
        members = list(primary.linked_cells) if primary.linked_cells else [primary]

        primary.linked_cells.clear()
        primary.temp_flags.pop("god_linked", None)

        if primary not in p_state.battle_zone:
            p_state.battle_zone.append(primary)

        inherited_tapped = primary.is_tapped
        inherited_sickness = primary.has_summoning_sickness
        inherited_flags = dict(primary.temp_flags)

        for member in members:
            if member is primary:
                primary.is_tapped = inherited_tapped
                primary.has_summoning_sickness = inherited_sickness
                primary.apply_static_effects(state)
                continue
            member.linked_cells.clear()
            member.temp_flags.pop("god_linked", None)
            member.is_tapped = inherited_tapped
            member.has_summoning_sickness = inherited_sickness
            member.temp_flags.update(inherited_flags)
            if member not in p_state.battle_zone:
                p_state.battle_zone.append(member)
            member.apply_static_effects(state)

    @staticmethod
    def move_linked_god_to_graveyard(
        state: GameState,
        player: int,
        primary: Creature,
        *,
        reason: str = "destroyed",
    ) -> GraveyardCard:
        """
        Rule 804.7: when a linked God leaves the battle zone, only one card leaves.

        The primary (anchor) creature's card goes to the graveyard; remaining
        members detach as separate Gods in the battle zone (804.4a).
        """
        p_state = state.players[player]
        members = list(primary.linked_cells) if primary.linked_cells else [primary]

        from engine.zone_mover import _fire_leave_battle_zone_triggers
        _fire_leave_battle_zone_triggers(state, primary, player, reason)

        primary.remove_static_effects(state)
        if primary in p_state.battle_zone:
            p_state.battle_zone.remove(primary)

        graveyard_card = GraveyardCard(
            definition=primary.definition,
            uid=primary.uid,
            died_from=reason,
            died_on_turn=state.turn_number,
        )
        p_state.graveyard.insert(0, graveyard_card)
        state.global_effects.remove_by_source(primary.uid)

        inherited_tapped = primary.is_tapped
        inherited_sickness = primary.has_summoning_sickness
        inherited_flags = {
            k: v for k, v in primary.temp_flags.items()
            if k not in ("god_linked",)
        }

        primary.linked_cells.clear()
        primary.temp_flags.pop("god_linked", None)

        for member in members:
            if member is primary:
                continue
            member.linked_cells.clear()
            member.temp_flags.pop("god_linked", None)
            member.is_tapped = inherited_tapped
            member.has_summoning_sickness = inherited_sickness
            member.temp_flags.update(inherited_flags)
            if member not in p_state.battle_zone:
                p_state.battle_zone.append(member)
            member.apply_static_effects(state)

        return graveyard_card
