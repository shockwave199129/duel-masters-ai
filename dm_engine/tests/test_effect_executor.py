"""
tests/test_effect_executor.py — coverage for effect execution and trigger gating.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.actions import ActionType
from core.cards import CardDefinition, CardEffect
from core.enums import CardSubtype, CardType, Civilization, EffectAction, EffectType, Phase, TriggerEvent
from core.player_state import PlayerState
from core.state import GameState, PendingTrigger, TurnInfo
from core.zones import Creature, HandCard, ShieldCard
from engine.action_generator import get_legal_actions
from engine.trigger_resolver import resolve_pending_triggers

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok, detail))
    print(f"  {(PASS if ok else FAIL)} {name}" + (f" — {detail}" if detail else ""))
    return ok


def card(cid, name, card_type=CardType.CREATURE, civs=(Civilization.FIRE,), power=1000):
    return CardDefinition(
        id=cid,
        slug=name.lower().replace(" ", "_"),
        name=name,
        cost=3,
        power=power if card_type == CardType.CREATURE else None,
        card_type=card_type,
        card_subtype=CardSubtype.NONE,
        civilizations=frozenset(civs),
        races=frozenset({"Human"}) if card_type == CardType.CREATURE else frozenset(),
        keywords=frozenset(),
        effects=tuple(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
    )


def effect(card_id, action, value=None, target=None, condition=None):
    return CardEffect(
        card_id=card_id,
        ability_index=0,
        raw_text=action.value,
        effect_type=EffectType.TRIGGERED,
        trigger_event=TriggerEvent.ON_SUMMON,
        effect_action=action,
        trigger_condition=condition or {},
        effect_target=target or {},
        effect_value=value or {},
        is_optional=False,
        is_replacement=False,
        active_in_phase=tuple(),
        active_in_zone=tuple(),
        parse_confidence=1.0,
    )


print("\n" + "═" * 60)
print("  DM ENGINE — EFFECT EXECUTOR TESTS")
print("═" * 60)

# Base state
hand_card = card(1, "HandCard")
deck_card = card(2, "DeckCard")
attacker_card = card(3, "Attacker")
shield_card = card(4, "ShieldCard")
state = GameState(
    players=(
        PlayerState(player_index=0, player_name="P0", hand=[], deck=[deck_card]),
        PlayerState(player_index=1, player_name="P1", deck=[deck_card], battle_zone=[Creature(definition=attacker_card, controller=1, owner=1)]),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)

# 1) Draw
tr = PendingTrigger(effect(10, EffectAction.DRAW, {"amount": 1}), "src", 10, 0)
state_draw = state.copy()
state_draw.effect_stack.add_trigger(tr)
after = resolve_pending_triggers(state_draw)
check("Draw adds card to hand", after.players[0].hand_count == 1)
check("Draw removes card from deck", after.players[0].deck_size == 0)

# 2) Destroy
state_destroy = GameState(
    players=(
        PlayerState(player_index=0, player_name="P0", deck=[deck_card]),
        PlayerState(player_index=1, player_name="P1", deck=[deck_card], battle_zone=[Creature(definition=attacker_card, controller=1, owner=1)]),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)
attack_uid = state_destroy.players[1].battle_zone[0].uid
state_destroy.effect_stack.add_trigger(
    PendingTrigger(effect(11, EffectAction.DESTROY, {}, {"target_uid": attack_uid}), "src2", 11, 0, {"target_uid": attack_uid})
)
after = resolve_pending_triggers(state_destroy)
check("Destroy moves creature out of battle zone", len(after.players[1].battle_zone) == 0)
check("Destroy sends creature to graveyard", len(after.players[1].graveyard) == 1)

# 3) Put to mana
hand_state = GameState(
    players=(
        PlayerState(player_index=0, player_name="P0", deck=[deck_card], hand=[HandCard(definition=hand_card)]),
        PlayerState(player_index=1, player_name="P1", deck=[deck_card]),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)
hand_uid = hand_state.players[0].hand[0].uid
hand_state.effect_stack.add_trigger(
    PendingTrigger(effect(12, EffectAction.PUT_TO_MANA, {}, {}, {}), "src3", 12, 0, {"card_uid": hand_uid})
)
after = resolve_pending_triggers(hand_state)
check("Put to mana removes card from hand", after.players[0].hand_count == 0)
check("Put to mana adds card to mana", after.players[0].mana_count == 1)

# 4) Power modify / fix
pm_state = GameState(
    players=(
        PlayerState(player_index=0, player_name="P0", deck=[deck_card]),
        PlayerState(player_index=1, player_name="P1", deck=[deck_card], battle_zone=[Creature(definition=attacker_card, controller=1, owner=1)]),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)
pm_uid = pm_state.players[1].battle_zone[0].uid
pm_state.effect_stack.add_trigger(PendingTrigger(effect(13, EffectAction.POWER_MODIFY, {"amount": 2000}, {"target_uid": pm_uid}), "src4", 13, 0, {"target_uid": pm_uid}))
pm_state.effect_stack.add_trigger(PendingTrigger(effect(14, EffectAction.POWER_FIX, {"fixed_value": 5000}, {"target_uid": pm_uid}), "src5", 14, 0, {"target_uid": pm_uid}))
after = resolve_pending_triggers(pm_state)
creature = after.players[1].battle_zone[0]
check("Power modify affects compute_power", creature.compute_power(after) == 5000)
check("Power fix overrides modifiers", creature.temp_flags.get("_power_fix") == 5000)

# 5) Break shield queues standby shield
bs_state = GameState(
    players=(
        PlayerState(player_index=0, player_name="P0", deck=[deck_card]),
        PlayerState(player_index=1, player_name="P1", deck=[deck_card], shield_zone=[ShieldCard(definition=shield_card)]),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)
bs_uid = bs_state.players[1].shield_zone[0].uid
bs_state.effect_stack.add_trigger(PendingTrigger(effect(15, EffectAction.BREAK_SHIELD, {}, {"shield_uid": bs_uid, "target_player": 1}), "src6", 15, 0, {"shield_uid": bs_uid, "target_player": 1}))
after = resolve_pending_triggers(bs_state)
check("Break shield removes shield from zone", after.players[1].shield_count == 0)
check("Break shield queues standby shield", len(after.effect_stack.shield_trigger_queue) == 1)

# 6) Trigger condition gate
cond_state = GameState(
    players=(
        PlayerState(player_index=0, player_name="P0", deck=[deck_card]),
        PlayerState(player_index=1, player_name="P1", deck=[deck_card]),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)
cond_state.effect_stack.add_trigger(PendingTrigger(effect(16, EffectAction.DRAW, {"amount": 1}, condition={"controller": 1}), "src7", 16, 0))
after = resolve_pending_triggers(cond_state)
check("Mismatched controller prevents trigger from firing", after.players[0].hand_count == 0)
check("Mismatched controller leaves deck unchanged", after.players[0].deck_size == 1)

# ────────────────────────────────────────────────────────────────────────────
# 7) Optional effect pauses resolution — awaited_choice is set
# ────────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print("  CHOICE PATHWAY TESTS")
print("─" * 60)

# Helper to create an optional CardEffect
def optional_effect(card_id, action, value=None, target=None):
    return CardEffect(
        card_id=card_id,
        ability_index=0,
        raw_text=f"(optional) {action.value}",
        effect_type=EffectType.TRIGGERED,
        trigger_event=TriggerEvent.ON_SUMMON,
        effect_action=action,
        trigger_condition={},
        effect_target=target or {},
        effect_value=value or {},
        is_optional=True,
        is_replacement=False,
        active_in_phase=tuple(),
        active_in_zone=tuple(),
        parse_confidence=1.0,
    )

# State where P0 controls a card in battle zone, P1 has a creature to target
opt_state = GameState(
    players=(
        PlayerState(player_index=0, player_name="P0", deck=[deck_card]),
        PlayerState(
            player_index=1, player_name="P1", deck=[deck_card],
            battle_zone=[Creature(definition=attacker_card, controller=1, owner=1)],
        ),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)
target_uid = opt_state.players[1].battle_zone[0].uid

# Queue an optional DRAW effect
opt_tr = PendingTrigger(optional_effect(20, EffectAction.DRAW, {"amount": 1}), "src_opt", 20, 0)
opt_state.effect_stack.add_trigger(opt_tr)
opt_after = resolve_pending_triggers(opt_state)

# Test 7a: AwaitedChoice is set for optional effect
choice = opt_after.effect_stack.awaited_choice
check("Optional effect sets awaited_choice", choice is not None)
check("AwaitedChoice has correct type", choice.choice_type == "yes_no" if choice else False)
check("AwaitedChoice targets controller (P0)", choice.player == 0 if choice else False)

# Test 7b: Effect did NOT auto-resolve (deck unchanged)
check("Optional effect pauses resolution — deck unchanged", opt_after.players[0].deck_size == 1)
check("Optional effect pauses resolution — hand empty", opt_after.players[0].hand_count == 0)

# Test 7c: get_legal_actions returns choice actions (yes/no)
legal = get_legal_actions(opt_after)
has_yes = any(a.action_type == ActionType.SELECT_YES_NO and a.choice is True for a in legal)
has_no = any(a.action_type == ActionType.SELECT_YES_NO and a.choice is False for a in legal)
check("legal_actions include SELECT_YES_NO (yes)", has_yes)
check("legal_actions include SELECT_YES_NO (no)", has_no)

# Test 7d: Confirming the choice (yes) resolves the effect
opt_confirm = opt_after.copy()
opt_confirm.effect_stack.clear_choice()
opt_confirm.effect_stack.add_trigger(PendingTrigger(
    optional_effect(20, EffectAction.DRAW, {"amount": 1}), "src_opt", 20, 0
))
# Manually resolve by clearing is_optional to simulate "yes" chosen
opt_confirm.effect_stack.pending_triggers[0].effect = CardEffect(
    card_id=20, ability_index=0, raw_text="draw",
    effect_type=EffectType.TRIGGERED, trigger_event=TriggerEvent.ON_SUMMON,
    effect_action=EffectAction.DRAW, trigger_condition={},
    effect_target={}, effect_value={"amount": 1},
    is_optional=False, is_replacement=False,
    active_in_phase=tuple(), active_in_zone=tuple(), parse_confidence=1.0,
)
opt_resolved = resolve_pending_triggers(opt_confirm)
check("After choosing 'yes', effect resolves — hand has card", opt_resolved.players[0].hand_count == 1)

# ────────────────────────────────────────────────────────────────────────────
# 8) Target-selection effect pauses for select_target choice
# ────────────────────────────────────────────────────────────────────────────
ts_state = GameState(
    players=(
        PlayerState(player_index=0, player_name="P0", deck=[deck_card]),
        PlayerState(
            player_index=1, player_name="P1", deck=[deck_card],
            battle_zone=[Creature(definition=attacker_card, controller=1, owner=1)],
        ),
    ),
    turn_info=TurnInfo(turn_number=2, active_player=0, phase=Phase.MAIN),
)
ts_target_uid = ts_state.players[1].battle_zone[0].uid
# Non-optional effect with effect_target requiring target selection
ts_effect = CardEffect(
    card_id=21, ability_index=0, raw_text="destroy target creature",
    effect_type=EffectType.TRIGGERED, trigger_event=TriggerEvent.ON_SUMMON,
    effect_action=EffectAction.DESTROY, trigger_condition={},
    effect_target={"type": "creature", "zone": "battle_zone"},
    effect_value={},
    is_optional=False, is_replacement=False,
    active_in_phase=tuple(), active_in_zone=tuple(), parse_confidence=1.0,
)
ts_state.effect_stack.add_trigger(PendingTrigger(ts_effect, "src_ts", 21, 0, {"target_uid": ts_target_uid}))
ts_after = resolve_pending_triggers(ts_state)

ts_choice = ts_after.effect_stack.awaited_choice
check("Target-selection effect sets awaited_choice", ts_choice is not None)
check("AwaitedChoice type is select_target", ts_choice.choice_type == "select_target" if ts_choice else False)
check("Creature not destroyed while waiting for choice", len(ts_after.players[1].battle_zone) == 1)

# get_legal_actions should offer select_target actions
ts_legal = get_legal_actions(ts_after)
has_select_target = any(a.action_type == ActionType.SELECT_TARGET for a in ts_legal)
check("legal_actions include SELECT_TARGET for each valid target", has_select_target)
select_target_actions = [a for a in ts_legal if a.action_type == ActionType.SELECT_TARGET]
check("SELECT_TARGET action has correct target_uid",
      any(a.target_uid == ts_target_uid for a in select_target_actions) if select_target_actions else False)

# ────────────────────────────────────────────────────────────────────────────
# Summary
# ────────────────────────────────────────────────────────────────────────────
passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\nRESULTS: {passed}/{len(results)} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
