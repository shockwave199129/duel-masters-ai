# Duel Masters AI — Rules Compliance Audit: Fix Plan

Scope: `dm_engine/` and `crawler/scripts/effect_parser.py` vs `Duel_Masters_rules.md`

---

## Priority Order

| # | Severity | Title | File | Lines |
|---|----------|-------|------|-------|
| 1 | 🔴 Critical | Hand size limit that does not exist in rules | `engine/phase_controller.py` | 93–107 |
| 2 | 🔴 Critical | Standby shields counted in `shield_count` | `core/player_state.py` | 95 |
| 3 | 🔴 Critical | Silent Skill choice ignored by `untap_all()` | `core/player_state.py` | 228–231 |
| 4 | 🔴 Critical | Duel Mate SBA evicts all established Duel Mates | `engine/sba_checker.py` | 927–961 |
| 5 | 🔴 Critical | Invented Star Evolution uniqueness SBA | `engine/sba_checker.py` | 65, 831–878 |
| 6 | 🟠 Moderate | APNAP trigger order not enforced | `core/state.py` | 132–135 |
| 7 | 🟠 Moderate | Sympathy reduction uses all card races | `engine/action_generator.py` | `_compute_effective_cost` |
| 8 | 🟠 Moderate | G-Zero uses a hardcoded heuristic | `engine/action_generator.py` | `_g_zero_condition_met` |
| 9 | 🟠 Moderate | Multi-colored mana card not placed tapped | `engine/zone_mover.py` | `move_hand_to_mana` |
| 10 | 🟠 Moderate | S-Back discard missing "from hand" semantic | `engine/action_executor.py` | `_resolve_s_back` |
| 11 | 🟠 Moderate | Multiple Breaker ability selection unverified | `engine/action_generator.py` | `_generate_direct_attack_actions` |
| 12 | 🟡 Minor | `"GR_summon"` case mismatch in crawler prompt | `crawler/scripts/effect_parser.py` | 338 |
| 13 | 🟡 Minor | Replacement effects missing `effect_type: "replacement"` | `crawler/scripts/effect_parser.py` | 237–248 |
| 14 | 🟡 Minor | S-Trigger classified as `triggered` instead of `keyword` | `crawler/scripts/effect_parser.py` | 347 |
| 15 | 🟡 Minor | No canonical schema for civilization filter in `effect_target` | `crawler/scripts/effect_parser.py` | ~368 |

---

## 🔴 Critical Bugs

---

### Bug 1 — Hand size limit that does not exist in rules

**Rule:** 402.2 — *"There is no maximum hand size limit for either player."*

**File:** `engine/phase_controller.py` · Lines 93–107 (inside `_end_turn`)

**What the code does:**
```python
# ── Hand size limit enforcement (Rule 105) ──────────────────────────────────
MAX_HAND_SIZE = 10
if len(p_state.hand) > MAX_HAND_SIZE:
    num_to_discard = len(p_state.hand) - MAX_HAND_SIZE
    from core.state import AwaitedChoice
    state.effect_stack.set_choice(AwaitedChoice(
        choice_type="discard_down",
        player=player,
        ...
        prompt=f"Discard down to {MAX_HAND_SIZE} cards",
    ))
    return  # Pause turn transition until discard is resolved
```

**Why it's wrong:** Rule 402.2 explicitly says there is no maximum hand size. The comment even cites the wrong rule number (105 vs 402). This erroneously forces players to discard every end of turn.

**Fix:** Delete the entire block. No replacement needed.

```python
# REMOVE lines 93–107 entirely. The block from:
#   MAX_HAND_SIZE = 10
# through:
#   return  # Pause turn transition until discard is resolved
# must be deleted.
```

---

### Bug 2 — Standby shields counted in `shield_count`

**Rule:** 113.6a — *"A shield in a standby state is physically in the Shield Zone, but it is not included in the number of shields in the Shield Zone."*

**File:** `core/player_state.py` · Line 95 (`shield_count` property)

**What the code does:**
```python
@property
def shield_count(self) -> int:
    return len(self.shield_zone)
```

**Why it's wrong:** During a `ShieldBreakWindow`, shields in `standby_shields` are still physically in `shield_zone`. So `shield_count` overcounts while the window is open. The action generator's `d_state.shield_count == 0` check (for direct attack eligibility) and the SBA win-condition check both read this property and will fire at the wrong moment.

**Fix:** Subtract shields currently in `ShieldBreakWindow.standby_shields` from the count.

```python
# In core/player_state.py — replace the shield_count property:

@property
def shield_count(self) -> int:
    """
    Rule 113.6a: shields in standby state are NOT counted,
    even though they are still physically in shield_zone.
    """
    # The ShieldBreakWindow lives on EffectStack; PlayerState has no
    # direct reference to it, so pass the standby set in via a helper
    # or expose a method that the engine calls with the window context.
    return len(self.shield_zone)
```

Because `PlayerState` doesn't hold a reference to `EffectStack`, the cleanest fix is a helper method on `GameState` (which owns both):

```python
# In core/state.py — add a helper method to GameState:

def effective_shield_count(self, player: int) -> int:
    """
    Rule 113.6a: do not count standby shields.
    Call this everywhere shield_count is checked for game-logic purposes.
    """
    win = self.effect_stack.shield_break_window
    standby_uids: set[str] = set()
    if win is not None:
        standby_uids = {s.uid for s in win.standby_shields}
    return sum(
        1 for s in self.players[player].shield_zone
        if s.uid not in standby_uids
    )
```

**Then replace every `d_state.shield_count == 0` and `p_state.shield_count` in logic paths** (action_generator, sba_checker) with `state.effective_shield_count(player)`.

**Files also affected:** `engine/action_generator.py` (`_generate_direct_attack_actions`), `engine/sba_checker.py` (`_sba_no_shields`)

---

### Bug 3 — Silent Skill choice ignored by `untap_all()`

**Rule:** 501.1a — *"A player may choose not to untap a creature with the Silent Skill ability."*

**Files:**
- `core/player_state.py` · Lines 228–231 (`untap_all`)
- `engine/phase_controller.py` · Line 64 (calls `untap_all`)
- `engine/action_generator.py` · Line ~223 (generates the yes/no choice)

**What the code does:**
```python
# player_state.py
def untap_all(self) -> None:
    """Untap all mana and creatures at start of turn."""
    for mana in self.mana_zone:
        mana.untap()
    for creature in self.battle_zone:
        creature.untap()   # ← always untaps, ignores any Silent Skill flag
```

**Why it's wrong:** The action generator correctly offers a `select_yes_no` choice for each Silent Skill creature. But `untap_all()` runs unconditionally when `advance_phase` triggers `_start_turn`. The player's "keep tapped" decision is never checked, so the creature is always untapped.

**Fix — Step 1:** When the player selects "keep tapped" (yes to Silent Skill), record it on the creature before untap runs.

```python
# In action_executor.py — handle ActionType.SELECT_YES_NO for Silent Skill:
# When action.card_uid is a creature uid and action.choice is True (keep tapped):

elif action_type == ActionType.SELECT_YES_NO:
    extra = action.get_extra()
    if extra.get("context") == "silent_skill":
        creature = s.players[action.player].find_creature(action.card_uid or "")
        if creature is not None:
            # True = player chose to keep tapped (Silent Skill)
            creature.temp_flags["silent_skill_skip_untap"] = action.choice
```

**Fix — Step 2:** Honour the flag in `untap_all`:

```python
# In core/player_state.py — replace untap_all:

def untap_all(self) -> None:
    """
    Rule 501.1 / 105.2: untap all mana and creatures.
    Rule 501.1a: skip creatures the player chose to keep tapped via Silent Skill.
    """
    for mana in self.mana_zone:
        mana.untap()
    for creature in self.battle_zone:
        if creature.temp_flags.get("silent_skill_skip_untap"):
            creature.temp_flags.pop("silent_skill_skip_untap")  # consume flag
            continue
        creature.untap()
```

**Fix — Step 3:** Ensure the action generator tags the choice with `context: "silent_skill"` so the executor can distinguish it.

```python
# In action_generator.py — when building Silent Skill yes/no actions,
# include the context in extra:
Action(
    action_type=ActionType.SELECT_YES_NO,
    player=player,
    card_uid=creature.uid,
    card_id=creature.id,
    choice=True,           # True = "keep tapped"
    extra={"context": "silent_skill"},
)
```

---

### Bug 4 — Duel Mate SBA evicts all established Duel Mates

**Rule:** 820 — Duel Mates that are **not properly summoned** must be returned to Hyperspatial.

**File:** `engine/sba_checker.py` · Lines 927–961 (`_sba_duel_mate_cleanup`)

**What the code does:**
```python
for creature in duel_mates:
    # If this Duel Mate wasn't properly summoned (no summoning sickness
    # but flagged), move to hyperspatial
    if not creature.has_summoning_sickness:   # ← BUG: condition is inverted
        ... move to hyperspatial ...
```

**Why it's wrong:** Creatures that have been in the Battle Zone since a previous turn do **not** have summoning sickness. The condition `not has_summoning_sickness` evicts every established Duel Mate every time SBAs run. A freshly-arrived but improperly summoned Duel Mate **does** have summoning sickness, so it is never caught.

The correct invariant is: a Duel Mate that arrived this turn without being summoned through the proper Duel Mate mechanic (e.g., directly moved by a card effect without linking) should be evicted. The proper signal is a temp flag set at summon time, not the sickness flag.

**Fix:**

```python
# In engine/sba_checker.py — replace the condition in _sba_duel_mate_cleanup:

for creature in duel_mates:
    # A Duel Mate is invalid in the BZ if it was placed here without
    # going through the proper Duel Mate summon path (rule 820.1b).
    # The summon path sets temp_flags["properly_summoned_as_duel_mate"] = True.
    # Anything lacking that flag that arrived this turn is invalid.
    properly_summoned = creature.temp_flags.get("properly_summoned_as_duel_mate", False)
    if not properly_summoned and creature.has_summoning_sickness:
        # Newly arrived but not through proper path — return to Hyperspatial
        creature.remove_static_effects(state)
        state.players[player_idx].battle_zone.remove(creature)
        ...
```

Also set `creature.temp_flags["properly_summoned_as_duel_mate"] = True` in the zone mover that handles the Duel Mate summon action, so valid summons are distinguished from effect-moved ones.

---

### Bug 5 — Invented Star Evolution uniqueness SBA

**Rules:**
- Rule 813.1: Star Evolution card leaving BZ → only topmost card leaves. **No uniqueness constraint.**
- Rule 815.1a: Only 1 S-MAX per player — uniqueness rule exists here.
- Rule 817: Dream Rare uniqueness — exists here.

**File:** `engine/sba_checker.py` · Line 65 (call site) and Lines 831–878 (`_sba_star_evolution_uniqueness`)

**What the code does:** Sends "duplicate" Star Evolution creatures of the same `card_id` to the graveyard, keeping only the most recently entered one.

**Why it's wrong:** No such rule exists for generic Star Evolution creatures. Only S-MAX and Dream Rares have uniqueness SBAs. This function silently destroys legitimately played Star Evolution creatures.

**Fix:** Delete `_sba_star_evolution_uniqueness` and remove its call from the SBA loop.

```python
# In engine/sba_checker.py:

# DELETE line 65:
#   if _sba_star_evolution_uniqueness(s):

# DELETE lines 831–878:
#   def _sba_star_evolution_uniqueness(state: GameState) -> bool:
#       ...

# Keep _sba_smax_uniqueness (line 519) and _sba_dream_rare_uniqueness (line 880).
# These are correct per rules 815.1a and 817.
```

---

## 🟠 Moderate Issues

---

### Issue 6 — APNAP trigger order not enforced

**Rule:** 101.4a — S-Trigger effects resolve first; then among simultaneous standby triggers, turn player chooses resolution order for their own, then non-turn player.

**File:** `core/state.py` · Lines 132–135 (`EffectStack.pop_next_trigger`)

**What the code does:**
```python
def pop_next_trigger(self) -> Optional[PendingTrigger]:
    if self.pending_triggers:
        return self.pending_triggers.pop(0)   # pure FIFO — no priority check
    return None
```

**Note:** `PendingTrigger.priority` exists at line 41 (`priority: int = -1`) but is never read here.

**Fix:**

```python
# In core/state.py — replace pop_next_trigger:

def pop_next_trigger(self) -> Optional[PendingTrigger]:
    """
    Rule 101.4a: APNAP ordering.
      1. S-Triggers (priority == 0) resolve before all others.
      2. Active player's triggers before non-active player's.
      3. Within same player, the player chooses order (already expressed
         by insertion order when they declare).
    """
    if not self.pending_triggers:
        return None
    # Sort: lower priority number = earlier. S-Triggers get priority 0
    # assigned when added via fire_trigger (see trigger_registry.py).
    self.pending_triggers.sort(key=lambda t: (t.priority if t.priority >= 0 else 999))
    return self.pending_triggers.pop(0)
```

Also ensure `trigger_registry.fire_trigger` assigns:
- `priority = 0` for S-Trigger effects (keyword `SHIELD_TRIGGER`)
- `priority = 1` for active player's standby triggers
- `priority = 2` for non-active player's standby triggers

---

### Issue 7 — Sympathy cost reduction is over-broad

**Rule:** Sympathy reduces cost by 1 for each creature of the **specific race stated in the Sympathy ability text** the player controls in their Battle Zone — not all races of the card.

**File:** `engine/action_generator.py` · `_compute_effective_cost` function

**Problem:** The current implementation iterates over all of the card's own races and counts matching creatures, which double- or triple-counts for multi-race cards with multiple Sympathy abilities.

**Fix:** Read the Sympathy race from the card's `card_effects` entry (stored by the crawler as `effect_value` for the `cost_reduce` effect with `trigger_event: "none"` and a race filter in `effect_target`).

```python
# In engine/action_generator.py — inside _compute_effective_cost:

# REPLACE the current Sympathy block with:
for effect in card_defn.effects:
    if effect.effect_action != EffectAction.COST_REDUCE:
        continue
    if effect.trigger_event is not None:
        continue  # only static cost reductions apply here
    try:
        target = json.loads(effect.effect_target or "{}")
    except (ValueError, TypeError):
        continue
    sympathy_race = target.get("race")
    if sympathy_race is None:
        continue
    count = sum(
        1 for c in player_state.battle_zone
        if sympathy_race.lower() in [r.lower() for r in c.definition.races]
    )
    cost = max(0, cost - count)
```

---

### Issue 8 — G-Zero uses a hardcoded heuristic

**Rule:** 112.3e — G-Zero lets a player summon a creature for free if a condition stated **on that card** is met. Conditions vary widely per card.

**File:** `engine/action_generator.py` · `_g_zero_condition_met` function

**Problem:** Comment says "Simplified: if player has ≥ 1 creature of the same race, condition met." This is incorrect for most G-Zero cards, which have conditions like "opponent has fewer shields than you," "you have 5+ cards in your mana zone," etc.

**Fix:** Read the actual condition from `card_effects`. The crawler should parse G-Zero's condition into `trigger_condition` (a JSON string). Fall back to the heuristic only when `trigger_condition` is null.

```python
# In engine/action_generator.py — replace _g_zero_condition_met:

def _g_zero_condition_met(state: GameState, player: int, card_defn) -> bool:
    """
    Rule 112.3e: evaluate the G-Zero condition stored in card_effects.
    Falls back to False (safe) if no condition is parseable.
    """
    for effect in card_defn.effects:
        if effect.effect_type != EffectType.COST_MOD:
            continue
        raw = effect.trigger_condition
        if not raw:
            continue
        try:
            cond = json.loads(raw)
        except (ValueError, TypeError):
            continue
        return _evaluate_condition(state, player, cond)
    # No parseable condition found — deny (conservative / safe default)
    return False


def _evaluate_condition(state: GameState, player: int, cond: dict) -> bool:
    """Evaluate a structured condition dict from card_effects.trigger_condition."""
    ctype = cond.get("type")
    if ctype == "own_creature_count_gte":
        return len(state.players[player].battle_zone) >= cond.get("value", 1)
    if ctype == "own_shield_count_lte":
        return state.effective_shield_count(player) <= cond.get("value", 0)
    if ctype == "opponent_shield_count_lte":
        opp = 1 - player
        return state.effective_shield_count(opp) <= cond.get("value", 0)
    if ctype == "own_mana_count_gte":
        return state.players[player].mana_count >= cond.get("value", 1)
    if ctype == "own_creature_race":
        race = cond.get("race", "").lower()
        return any(race in [r.lower() for r in c.definition.races]
                   for c in state.players[player].battle_zone)
    # Extend as more condition types are identified during crawler testing
    return False
```

---

### Issue 9 — Multi-colored mana card not placed tapped

**Rule:** 405.1 — *"Multi-colored cards are placed tapped in the mana zone."*

**File:** `engine/zone_mover.py` · `move_hand_to_mana` function

**Fix:** After placing the card in the mana zone, check if it has more than one civilization and tap it immediately.

```python
# In engine/zone_mover.py — inside move_hand_to_mana, after appending mana_card:

if len(mana_card.definition.civilizations) > 1:
    mana_card.tap()   # Rule 405.1: multi-colored cards enter mana tapped
```

Verify `ManaCard` has a `tap()` method (it should alongside `untap()`). If not, add it.

---

### Issue 10 — S-Back discard missing "from hand" semantic

**Rule:** 509.5c — *"The card discarded at this time is moved from the shield to the graveyard, but it is treated as a card discarded from the hand."*

**File:** `engine/action_executor.py` · `_resolve_s_back` · Lines ~430–460

**What the code does:** Inserts a `GraveyardCard` with `died_from="s_back_discard"` — the provenance is recorded but there is no field marking it as semantically "discarded from hand."

**Why it matters:** Effects that trigger "when a card is discarded from your hand" (e.g., certain Psy Creatures) must fire when an S-Back discard occurs. If the graveyard entry has no "hand discard" semantic, those triggers will never see it.

**Fix:** Add a `was_discarded_from_hand: bool` field to `GraveyardCard` (or use `temp_flags`), and set it to `True` for S-Back discards. Then ensure `trigger_registry.fire_trigger` for `ON_DISCARD` checks this flag when determining whether hand-discard triggers fire.

```python
# In engine/action_executor.py — _resolve_s_back, the graveyard insert:

from core.zones import GraveyardCard
state.players[action.player].graveyard.insert(
    0,
    GraveyardCard(
        definition=shield.definition,
        uid=shield.uid,
        died_from="s_back_discard",
        died_on_turn=state.turn_number,
        treat_as_hand_discard=True,   # ← Rule 509.5c
    ),
)
```

Add `treat_as_hand_discard: bool = False` to `GraveyardCard` in `core/zones.py`, and in `trigger_registry.py` fire `ON_DISCARD` triggers when this field is `True`.

---

### Issue 11 — Multiple Breaker ability selection unverified

**Rule:** 509.2c — If a creature simultaneously has both T Breaker and W Breaker, the player must choose one; they may not choose to break only 1 shield.

**File:** `engine/action_generator.py` · `_generate_direct_attack_actions`

**Action:** Audit whether the action generator offers both T Breaker and W Breaker as separate choosable actions when a creature has both abilities simultaneously. If both are offered as individual options without the restriction that the player may not choose 1-break, add a check:

```python
# In _generate_direct_attack_actions — when building breaker action options:

has_t_breaker = creature.has_keyword(Keyword.TRIPLE_BREAKER)
has_w_breaker = creature.has_keyword(Keyword.DOUBLE_BREAKER)

if has_t_breaker and has_w_breaker:
    # Rule 509.2c: player must choose T or W; single-break is not allowed
    actions.append(Action(..., extra={"break_count": 3, "breaker_type": "T"}))
    actions.append(Action(..., extra={"break_count": 2, "breaker_type": "W"}))
    # Do NOT add a single-break option
else:
    # normal single / double / triple path
    ...
```

---

## 🟡 Minor — Crawler Gaps

---

### Gap 12 — `"GR_summon"` case mismatch in crawler prompt

**File:** `crawler/scripts/effect_parser.py` · Line 338

**What the code does:**
```python
# Line 338 (system prompt string):
Use "GR_summon" when a card specifically GR Summons from the Ultra GR Zone (rule 701.30).
```

**Why it's wrong:** `VALID_EFFECT_ACTIONS` (line ~54) and `EffectAction` enum both use `"gr_summon"` (lowercase). The LLM will output `"GR_summon"` and the validator will silently replace it with `"none"`.

**Fix:** Change the prompt text at line 338:

```python
# Before:
Use "GR_summon" when a card specifically GR Summons ...

# After:
Use "gr_summon" when a card specifically GR Summons ...
```

---

### Gap 13 — Replacement effects missing explicit `effect_type: "replacement"`

**File:** `crawler/scripts/effect_parser.py` · Lines 237–248 (system prompt, replacement effect examples)

**What the code does:** The prompt tells the LLM to set `is_replacement=true` for effects like `psychic_release`, `dragon_evasion`, and `dragon_soul_evasion`, but doesn't explicitly say to also set `effect_type: "replacement"`.

**Why it's wrong:** The validator at line 1112 falls back to `"triggered"` when `effect_type` is absent or invalid. These effects would be queued as triggered effects, missing the engine's replacement-effect registry.

**Fix:** In the system prompt, for every replacement effect example, add the `effect_type` instruction alongside `is_replacement`:

```python
# In each replacement effect example block, change from:
#   Set is_replacement=true.
# to:
#   Set is_replacement=true AND effect_type: "replacement".

# Example for dragon_evasion (line ~248):
# Before:
#   lower-cost face instead (rule 805.1b). Set is_replacement=true.
# After:
#   lower-cost face instead (rule 805.1b). Set effect_type: "replacement" and is_replacement=true.
```

Apply this change consistently for: `psychic_release`, `dragon_evasion`, `dragon_soul_evasion`, `forbidden_flip`, and any other explicitly replacement effects listed in the prompt.

---

### Gap 14 — S-Trigger classified as `triggered` instead of `keyword`

**File:** `crawler/scripts/effect_parser.py` · Line 347 (system prompt)

**What the code does:**
```python
# Line 347:
- "When a shield is broken" → trigger_event: "on_shield_trigger"
```

This implies `effect_type: "triggered"` for S-Trigger, but S-Trigger (rule 112.3a) is a **keyword** that enables a card to be used for free from hand when a shield it was in is broken — not a triggered ability in the rules sense.

**Why it matters:** The engine's `Keyword.SHIELD_TRIGGER` is handled separately from the trigger queue. Classifying S-Trigger as `effect_type: "triggered"` will flood the trigger queue with spurious S-Trigger "triggers" that are already handled by the shield break window.

**Fix:** Update the system prompt to distinguish S-Trigger (keyword) from abilities that merely *fire* when a shield is broken (legitimate triggered abilities):

```python
# In effect_parser.py system prompt — add a clarification near line 347:

# S-TRIGGER (the keyword that lets a card be used for free from hand):
#   → effect_type: "keyword", effect_action: "none",
#     active_in_zone: ["shield_zone"]
#   Do NOT set trigger_event: "on_shield_trigger" for the S-Trigger keyword itself.

# ABILITIES THAT TRIGGER WHEN A SHIELD IS BROKEN (e.g., "When one of your shields
# is broken, draw a card"):
#   → effect_type: "triggered", trigger_event: "on_break_shield"
```

---

### Gap 15 — No canonical schema for civilization filter in `effect_target`

**File:** `crawler/scripts/effect_parser.py` · ~Line 368 (system prompt, effect_target description)

**What the code does:** `effect_target` is described as "a valid JSON string" with free-form structure. No civilization or race filter schema is specified.

**Why it matters:** Effects like "destroy all Fire creatures" or "your Water spells cost 2 less" require civilization and race filters. Without a canonical schema, every card will produce a different JSON shape for the same concept, making the engine unable to parse targets reliably.

**Fix:** Add a documented canonical schema to the system prompt immediately after the `effect_target` line:

```python
# Add to system prompt after the effect_target field description (~line 370):

"""
effect_target canonical schema (always use this exact shape when applicable):
{
  "scope":          "self" | "opponent" | "both" | "any",   // whose side
  "type":           "creature" | "spell" | "card" | "player",
  "race":           "<race name, e.g. Dragon>" | null,
  "civilization":   "<civ name, e.g. Fire>" | null,
  "zone":           "battle_zone" | "hand" | "mana_zone" | "deck" | "graveyard" | null,
  "power_lte":      <int> | null,   // power ≤ this value
  "power_gte":      <int> | null,   // power ≥ this value
  "count":          <int> | "all" | null,
  "exclude_uid":    null            // reserved; always null in parsed output
}

Omit keys that are not applicable. Do not invent keys outside this schema.
"""
```

---

## Quick Reference — Files Touched

| File | Bugs Fixed |
|------|-----------|
| `engine/phase_controller.py` | Bug 1 (delete hand-size block) |
| `core/player_state.py` | Bug 2 (shield_count), Bug 3 (untap_all) |
| `core/state.py` | Bug 2 (add effective_shield_count), Issue 6 (APNAP sort) |
| `engine/sba_checker.py` | Bug 4 (duel mate), Bug 5 (delete star evo SBA) |
| `engine/action_generator.py` | Issue 7 (sympathy), Issue 8 (g-zero), Issue 11 (multi-breaker) |
| `engine/zone_mover.py` | Issue 9 (multi-colored mana tap) |
| `engine/action_executor.py` | Bug 3 (silent skill flag), Issue 10 (s-back provenance) |
| `core/zones.py` | Issue 10 (GraveyardCard.treat_as_hand_discard field) |
| `engine/trigger_registry.py` | Issue 6 (assign APNAP priority), Issue 10 (ON_DISCARD fire) |
| `crawler/scripts/effect_parser.py` | Gap 12, 13, 14, 15 (prompt fixes) |

---

*Generated from audit of commit `main` · `shockwave199129/duel-masters-ai`*
