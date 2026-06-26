# Audit: Parser → Database → Engine Alignment

**Date**: 2026-06-23
**Scope**: `crawler/scripts/effect_parser.py` → `crawler/sql/schema.sql` → `dm_engine/`

---

## Executive Summary

The parser's `SYSTEM_PROMPT` defines a comprehensive schema for LLM output. The database schema (`card_effects` table) stores all parsed fields. The game engine (`dm_engine/`) consumes most but **not all** fields. Several gaps exist where data is parsed/stored but never used by the engine, or where engine needs data the parser doesn't provide.

---

## 1. Field-by-Field Audit: `card_effects` Table

| Column | Parser Output | DB Stored | Engine Used? | Notes |
|--------|---------------|-----------|--------------|-------|
| `card_id` | ✅ implicit | ✅ FK | ✅ | Primary link |
| `face_index` | ✅ | ✅ | ⚠️ | Used for multiface cards (Psychic/Dragheart) |
| `face_name` | ✅ | ✅ | ⚠️ | Used for multiface cards |
| `ability_index` | ✅ | ✅ | ✅ | Order of ■ on card |
| `raw_text` | ✅ | ✅ | ✅ | Debug/display |
| `effect_type` | ✅ | ✅ | ✅ | **Critical** - routes to static/triggered/activated/spell/keyword/cost_mod/replacement |
| `trigger_event` | ✅ | ✅ | ✅ | **Now fully used** via TriggerRegistry (21 events) |
| `trigger_condition` | ✅ JSONB | ✅ | ✅ | Used in `trigger_resolver._eval_condition()` |
| `effect_action` | ✅ | ✅ | ✅ | **Critical** - 70+ actions dispatched in `effect_executor.py` |
| `effect_target` | ✅ JSONB | ✅ | ✅ | Targeting info (zone, scope, filters) |
| `effect_value` | ✅ JSONB | ✅ | ✅ | Parameters (amount, count, keyword, etc.) |
| `is_optional` | ✅ | ✅ | ✅ | Generates choice actions (yes/no) |
| `is_replacement` | ✅ | ✅ | ❌ | **NOT USED** - engine uses `effect_type == REPLACEMENT` instead |
| `active_in_phase` | ✅ | ✅ TEXT[] | ⚠️ | **Partial** - loaded, filtered in TriggerRegistry, but static effects only checked at entry |
| `active_in_zone` | ✅ | ✅ TEXT[] | ✅ | Used in action_generator for target zone validation |
| `parse_confidence` | ✅ | ✅ | ❌ | **NOT USED** - could gate RAG fallback |
| `parsed_at` | auto | ✅ | - | Audit trail |

---

## 2. TriggerEvent Coverage (21 values)

| TriggerEvent | Rule Ref | Parser Prompted? | Engine Fires? | Notes |
|--------------|----------|------------------|---------------|-------|
| `ON_ENTER_BATTLE_ZONE` | 301.2, 701.16 | ✅ | ✅ `zone_mover.py` | Creature enters BZ |
| `ON_ATTACK` | 506.3 | ✅ | ✅ `action_executor.py` | Creature declares attack |
| `ON_BREAK_SHIELD` | 509 | ✅ | ✅ `zone_mover.py` | Shield broken (after replacement) |
| `ON_DESTROY` | 115.3, 701.10 | ✅ | ✅ `zone_mover.py` | Creature destroyed |
| `ON_LEAVE_BATTLE_ZONE` | 805.4, 807.4 | ✅ | ✅ `zone_mover.py` | Creature leaves BZ |
| `START_OF_TURN` | 500.4, 501 | ✅ | ✅ `phase_controller.py` | Turn start |
| `END_OF_TURN` | 500, 512 | ✅ | ✅ `phase_controller.py` | Turn end |
| `ON_SUMMON` | 504 | ✅ | ✅ `zone_mover.py` | Creature summoned from hand |
| `ON_CAST` | 601 | ✅ | ✅ `action_executor.py` | Spell cast |
| `ON_SHIELD_TRIGGER` | 509.5, 112.3a | ✅ | ⚠️ | Handled specially in `shield_break_window.py` |
| `ON_DRAW` | 114.5, 502 | ✅ | ✅ `zone_mover.py` | Card drawn |
| `ON_MANA_CHARGE` | 503 | ✅ | ✅ `zone_mover.py` | Mana charged |
| `ON_BLOCK` | 507.3a | ✅ | ✅ `action_executor.py` | Blocker declared |
| `ON_BATTLE` | 115.2, 508 | ✅ | ✅ `battle_resolver.py` | Battle begins |
| `ON_WIN_BATTLE` | 115.3d | ✅ | ✅ `battle_resolver.py` | Creature wins battle |
| `ON_DIRECT_ATTACK` | 509, 104.2a | ✅ | ✅ `shield_resolver.py` | Direct attack (0 shields) |
| `BEFORE_BREAK` | 509.3 | ✅ | ✅ `zone_mover.py` | Before each shield break |
| `NONE` | - | ✅ | N/A | Static/activated/keyword/replacement |

✅ **All 17 non-NONE trigger events now fire via TriggerRegistry** (previously only 2 were used).

---

## 3. EffectAction Coverage (70+ values)

### Fully Implemented in `effect_executor.py`
- `DRAW`, `DESTROY`, `RETURN_TO_HAND`, `SEARCH_DECK`, `PUT_TO_MANA`
- `SUMMON_FREE`, `PUT_TO_BATTLE_ZONE`, `PUT_TO_SHIELD`, `ADD_TO_HAND`
- `DISCARD`, `TAP`, `UNTAP`, `POWER_MODIFY`, `POWER_FIX`
- `CANNOT_ATTACK`, `CANNOT_BE_BLOCKED`, `CANNOT_BE_DESTROYED`
- `WIN_BATTLE`, `BREAK_SHIELD`, `LOOK_AT_TOP`, `SHUFFLE`
- `COST_REDUCE`, `COST_INCREASE`, `GIVE_KEYWORD`, `BANISH_TO_ABYSS`
- `MOVE_ZONE`, `REVEAL`, `GR_SUMMON`, `COPY_EFFECT`
- `ATTACH_SEAL`, `REMOVE_SEAL`, `GACHINKO_JUDGE`, `HYPERIZE`
- `AWAKEN`, `AWAKEN_LINK`, `DRAGSOLVE`, `LINK_RELEASE`
- `DRAGON_EVASION`, `DRAGON_SOUL_EVASION`, `PSYCHIC_RELEASE`
- `COMBINE`, `EXTRA_EX_LIFE`, `ZEROM_RITUAL`, `ZEROM_FLIP`
- `FORBIDDEN_RELEASE`, `NEO_EVOLVE`, `WIN_CONDITION`, `LOSE_CONDITION`
- `EVOLVE`, `CROSS_GEAR`, `GOD_LINK`, `FORTIFY`, `DEPLOY_FIELD`
- `SWAP_ZONES`, `TURN_UPSIDE_DOWN`, `FORBIDDEN_EXPLOSION`

### Parsed/Defined but NOT IMPLEMENTED (TODO stubs in executor)
| EffectAction | Rule | Parser Prompted? | Status |
|--------------|------|------------------|--------|
| `PROTECTION` | 701.xx | ✅ | ❌ TODO |
| `GAIN_CONTROL` | 701.xx | ✅ | ❌ TODO |
| `ZEROM_BIRTH` | 701.31 | ✅ | ❌ TODO |
| `SHIELDIFY` | 701.32 | ✅ | ❌ TODO |
| `MUST_ATTACK` | - | ✅ | ❌ TODO |
| `MUST_BLOCK` | - | ✅ | ❌ TODO |
| `CANNOT_BLOCK` | - | ✅ | ❌ TODO |

### In Engine Enum but NOT in Parser Prompt
None found - parser covers all enum values.

### In Rules but NOT in Parser/Engine
| Rule | Concept | Missing |
|------|---------|---------|
| 701.33 | Banish to Abyss | ✅ covered as `BANISH_TO_ABYSS` |
| 701.21 | Gachinko Judge | ✅ covered |
| 701.24/23 | Seal attach/remove | ✅ covered |
| 805.1a | Awaken | ✅ covered |
| 805.1b | Psychic Release | ✅ covered |
| 805.1c | Awaken Link | ✅ covered |
| 807.1a | Dragsolution | ✅ covered |
| 807.1b | Dragon Evasion | ✅ covered |
| 808.1b | Dragon Soul Evasion | ✅ covered |
| 806.1b | Link Release | ✅ covered |
| 814 | King Cell Combine | ✅ covered |
| 809 | Forbidden Release | ✅ covered |
| 802 | NEO Evolution | ✅ covered |
| 816 | Hyperize | ✅ covered |
| 812 | Zerom Ritual/Flip/Birth | ✅ mostly covered (Birth TODO) |
| 810 | Twinpact Flip | ✅ covered |
| 822 | G-Castle / Sabaki Z | ⚠️ Sabaki Z handled separately |

---

## 4. Critical Gaps

### Gap 1: `is_replacement` Column Ignored by Engine
**Location**: `core/cards.py:77-82` (`is_replacement_effect()` method)
**Issue**: Engine checks `effect_type == EffectType.REPLACEMENT` but parser outputs `is_replacement` boolean. These can diverge.
**Fix**: Add cross-validation at load time (see `card_database.py` - already added in recent commit).

### Gap 2: `active_in_phase` Only Partially Consumed
**Location**: `TriggerRegistry.fire_trigger()` filters by phase; `Creature.apply_static_effects()` filters at entry time
**Issue**: Static effects with phase restrictions (e.g., "during main phase only") are only filtered when creature **enters** BZ. If phase changes, they don't re-evaluate.
**Fix**: Full phase-gating would require re-evaluating static effects on phase change (major refactor).

### Gap 3: `parse_confidence` Never Used
**Potential**: Could gate RAG fallback - if confidence < 0.7, fetch ruling from ChromaDB
**Location**: `card_database.py` loads it; `CardEffect.needs_rag_fallback()` exists but unused

### Gap 4: Missing Trigger Events from Rules
| Rule | Trigger Description | Current Mapping |
|------|---------------------|-----------------|
| 110.4g | "When this enters [zone other than BZ]" | `ON_ENTER_BATTLE_ZONE` only covers BZ |
| 114.5 | "When a card is drawn" | `ON_DRAW` ✅ |
| 115.2 | "When a battle begins" | `ON_BATTLE` ✅ |
| 500.4 | "At the beginning of step" | `START_OF_TURN` only |
| 509.3 | "Before each shield break" | `BEFORE_BREAK` ✅ |
| 509.5 | S-Trigger/G-Strike/S-Back/Sabaki Z | Special handling |

### Gap 5: Zone Values in `active_in_zone`
**Parser Prompted Zones**: `battle_zone, shield_zone, mana_zone, graveyard, hand, deck, hyperspatial, ultra_gr, abyss_zone, pending, any`
**Engine Validated**: `VALID_ZONE_VALUES = {z.value for z in Zone}` + `"any"`
**Missing from Zone enum**: `"any"` (handled specially), `"deck"` (not in Zone enum)
**Issue**: Parser may output `"deck"` but engine validates against Zone enum which has no DECK.

---

## 5. Parser SYSTEM_PROMPT vs Rules Compliance

### Rules Concepts Correctly Covered
- ✅ Characteristic-Defining Abilities (110.4a) → `active_in_zone: ["any"]`
- ✅ Zone-specific abilities (110.4b) → `active_in_zone` array
- ✅ Battle Zone default (110.4c) → default `["battle_zone"]`
- ✅ Cost-execution abilities (110.4d) → `active_in_zone: ["mana_zone"]` etc.
- ✅ Hand-zone execution modifiers (110.4e) → `active_in_zone: ["hand"]`
- ✅ State-Defining Effects (110.4f) → `effect_type: "replacement"` + `is_replacement: true`
- ✅ Non-BZ triggers (110.4g) → `trigger_event` from appropriate zone

### Rules Concepts NOT Explicitly in Prompt
- ⚠️ Rule 101.5: Replacement effects apply once per event
- ⚠️ Rule 101.4: APNAP ordering (handled in trigger_resolver)
- ⚠️ Rule 101.4d: Effect interruption (handled in effect_stack)
- ⚠️ Rule 206.4: "Double power" → parser has `operation: "double"` in `effect_value`
- ⚠️ Rule 509.2: Breaker counts (Double/Triple/World) → `BREAK_SHIELD` with `effect_value: {"count": N}`

---

## 6. Database View `v_card_engine` Completeness

The view aggregates all card data for the engine. **Complete** - includes all `card_effects` fields.

---

## 7. Recommendations Priority

| Priority | Issue | Effort | Impact |
|----------|-------|--------|--------|
| **P0** | Implement TODO effect actions (PROTECTION, GAIN_CONTROL, etc.) | Medium | Unblocks cards using these |
| **P0** | Cross-validate `is_replacement` vs `effect_type` at load | Low (done) | Prevents silent bugs |
| **P1** | Use `parse_confidence` for RAG fallback | Low | Improves low-confidence parses |
| **P1** | Add `ON_SHIELD_TRIGGER` to TriggerRegistry (unify with shield window) | Medium | Consistency |
| **P2** | Full static effect phase-gating (re-eval on phase change) | High | Correctness for phase-limited statics |
| **P2** | Support `active_in_zone: ["deck"]` (add DECK to Zone enum) | Low | Completeness |
| **P3** | Document all `effect_value` schemas per `effect_action` | Low | Parser consistency |

---

## 8. Data Flow Summary

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Card Wiki      │────▶│  effect_parser.py │────▶│  PostgreSQL     │
│  (raw text)     │     │  (LLM + prompt)   │     │  card_effects   │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Game Engine    │◀────│  CardDatabase    │◀────│  v_card_engine  │
│  (dm_engine)    │     │  .load()         │     │  (view)         │
└─────────────────┘     └──────────────────┘     └─────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  TriggerRegistry.fire_trigger()                              │
│    → matches TriggerEvent → filters by phase/condition       │
│    → queues PendingTrigger → trigger_resolver executes       │
│    → effect_executor dispatches on EffectAction              │
└──────────────────────────────────────────────────────────────┘
```

---

## 9. Files Referenced

| File | Purpose |
|------|---------|
| `crawler/scripts/effect_parser.py` | LLM prompt + DB write |
| `crawler/sql/schema.sql` | `card_effects` table + `v_card_engine` view |
| `dm_engine/db/card_database.py` | Loads effects into `CardEffect` objects |
| `dm_engine/core/cards.py` | `CardEffect` dataclass + `CardDefinition` methods |
| `dm_engine/core/enums.py` | `TriggerEvent`, `EffectAction`, `EffectType` enums |
| `dm_engine/engine/trigger_registry.py` | Data-driven trigger dispatch (NEW) |
| `dm_engine/engine/trigger_resolver.py` | Condition evaluation + APNAP ordering |
| `dm_engine/engine/effect_executor.py` | Dispatches on `EffectAction` |
| `dm_engine/engine/zone_mover.py` | Fires zone-change triggers |
| `dm_engine/engine/action_executor.py` | Fires action-based triggers |
| `dm_engine/engine/battle_resolver.py` | Fires battle triggers |
| `dm_engine/engine/shield_resolver.py` | Fires direct attack trigger |
| `dm_engine/engine/phase_controller.py` | Fires turn-boundary triggers |
| `Duel_Masters_rules.md` | Canonical rules source |

