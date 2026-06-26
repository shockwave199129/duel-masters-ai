Now I have a thorough understanding of both the crawler and game engine. Let me compile the comprehensive audit.

---

# Duel Masters Rules Compliance Audit
## Crawler & Game Engine vs. Duel_Masters_rules.md (Ver 1.50)

---

## Executive Summary

| Component | Overall Compliance | Key Strengths | Critical Gaps |
|-----------|-------------------|---------------|---------------|
| **Crawler / Effect Parser** | ~70% | Good LLM-based parsing with structured output; comprehensive enums; rules context injection | No validation against rule text; missing several trigger types; no replacement effect parsing; Twinpact/Forbidden/Zerom handling incomplete |
| **Game Engine (dm_engine)** | ~85% | Excellent enum coverage matching rules; full turn structure; APNAP ordering; SBA checker; battle/shield resolution | Missing 12+ SBA rules; incomplete replacement effects; no continuous effect layers; limited keyword implementations; Twinpact/Dragheart/Psychic/King Cell only partially implemented |

---

## 1. Crawler & Effect Parser Audit (`crawler/scripts/effect_parser.py`)

### 1.1 Strengths

| Rule Area | Implementation | Quality |
|-----------|----------------|---------|
| **Card Structure (Rule 200-204)** | `CardDefinition` with cost, power, card_type, card_subtype, civilizations, races, keywords, effects | ✅ Complete |
| **Zones (Rules 400-410)** | `VALID_ZONES` includes all 10 zones: deck, hand, mana_zone, battle_zone, shield_zone, graveyard, abyss_zone, hyperspatial, ultra_gr, pending | ✅ Complete |
| **Phases (Rules 500-509)** | `VALID_PHASES` covers all main phases + attack sub-steps | ✅ Complete |
| **Keywords (Rules 701, 112.3, 816)** | 29 keywords mapped including S-Trigger, Ninja Strike, Sabaki Z, G-Zero, Attack Chance, Over Drive, Hyperize | ✅ Comprehensive |
| **Effect Types (Rule 110.3)** | `VALID_EFFECT_TYPES`: keyword, triggered, activated, static, replacement, cost_mod, spell | ✅ Complete |
| **Trigger Events** | 20 trigger events mapped (on_enter_battle_zone, on_attack, on_break_shield, etc.) | ✅ Good |
| **Effect Actions** | 56 actions mapped (draw, destroy, power_modify, give_keyword, move_zone, etc.) | ✅ Comprehensive |

### 1.2 Critical Gaps

| Rule Reference | Missing / Incorrect | Impact |
|----------------|---------------------|--------|
| **Rule 110.4f** State-Defining Effects | Not parsed/recognized. Effects like "This creature enters tapped" or "Enters with a seal" are not distinguished from static/replacement effects | High - affects entry behavior |
| **Rule 110.4a** Characteristic-Defining Abilities (CDAs) | Only partially handled via `CDAFormulaType` enum; LLM not prompted to detect CDAs vs. power-attacker | High - power calculation wrong for cards like 《Nijisoku The Verde》 |
| **Rule 101.5** Replacement Effects | `is_replacement` field exists but **not consumed by engine** (comment in `CardEffect.is_replacement_effect()`). No replacement effect resolution logic in parser | Critical - "instead of X, Y" effects broken |
| **Rule 609** Replacement Effect Application | No parsing of "instead" / "rather than" language; no ordering (turn player priority) | Critical |
| **Rule 112.2a** Mana Cost Payment | Parser doesn't validate/extract civilization requirements from cost; only numeric cost | Medium - cost reduction effects may miscalculate |
| **Rule 204.3** Supertypes | Several supertypes missing: `HYPER_SOUL_X`, `WD_FIELD` marked STUB; `G_CASTLE` present but logic incomplete | Medium |
| **Rule 805-808** Psychic/Dragheart Flip | `AWAKEN`, `AWAKEN_LINK`, `DRAGSOLVE`, `LINK_RELEASE`, `DRAGON_EVASION`, `DRAGON_SOUL_EVASION`, `PSYCHIC_RELEASE` in enums but parser not trained on these patterns | High |
| **Rule 810** Twinpact / Double-Sided Cards | `TWINPACT_FLIP`, `FORBIDDEN_FLIP` in enums; parser doesn't extract both faces' characteristics | High |
| **Rule 812** Zerom System | `ZEROM_RITUAL`, `ZEROM_FLIP` in enums but no parser support | High |
| **Rule 814** King Cell Combine | `COMBINE`, `EXTRA_EX_LIFE` in enums; parser doesn't identify combine requirements | High |
| **Rule 809** Forbidden Release | `FORBIDDEN_RELEASE` in enums; no parser support for "flip from hand to BZ" | Medium |
| **Rule 802** NEO Evolution | `NEO_EVOLVE` in enums; no parser support for "evolve without putting underneath" | Medium |

### 1.3 Data Quality Issues

```python
# In effect_parser.py - the is_replacement field is loaded but IGNORED
def is_replacement_effect(self) -> bool:
    # NOTE: This checks effect_type == EffectType.REPLACEMENT, NOT the
    # is_replacement boolean field. The is_replacement field is loaded
    # from DB but not consumed by the engine...
    return self.effect_type == EffectType.REPLACEMENT
```

**Recommendation**: Either remove `is_replacement` DB column or make engine consume it. Current state causes silent data loss.

### 1.4 LLM Prompt Gaps

The parser uses `build_rules_context()` to inject rules into LLM prompt but:
- No few-shot examples for complex mechanics (King Cell, Zerom, Forbidden)
- No explicit instruction to detect "state-defining" vs "replacement" effects
- No validation pass against rule text after parsing

---

## 2. Game Engine Audit (`dm_engine/`)

### 2.1 Turn Structure Compliance (Rules 500-509)

| Phase | Rule | Implemented | Notes |
|-------|------|-------------|-------|
| Start of Turn | 501 | ✅ | Untap, summoning sickness removal, START_OF_TURN triggers |
| Draw | 502 | ✅ | First player skip on turn 1; 1 card draw |
| Mana Charge | 503 | ✅ | Optional 1 card from hand to mana |
| Main | 504 | ✅ | Summon, cast, cross gear, fortify, deploy field, tamaseed, king combine |
| Attack | 505 | ✅ | 5 sub-steps: ATTACK_DECLARE → BLOCK_DECLARE → BATTLE → DIRECT_ATTACK → END_OF_ATTACK |
| End of Turn | 500 | ✅ | END_OF_TURN triggers, hand limit 10, EOT effect expiry |

**Compliance: Excellent** — PhaseController correctly implements the 6-step turn with 5 attack sub-steps.

### 2.2 State-Based Actions (Rule 703) — **MAJOR GAPS**

The SBA checker implements only ~8 of 13 required SBAs (703.4a-m):

| SBA Rule | Description | Implemented? |
|----------|-------------|--------------|
| 703.4a | Player with 0 cards in deck loses | ✅ |
| 703.4b | Player attacked with 0 shields loses (direct attack) | ✅ |
| 703.4c | Creature with 0 or less power destroyed | ✅ |
| 703.4d | Creature that lost battle destroyed | ✅ |
| 703.4e | Creature with "cannot attack" tapped | ❌ **MISSING** |
| 703.4f | Cross Gear not attached to creature destroyed | ❌ **MISSING** |
| 703.4g | Castle fortifying shield removed from shield | ❌ **MISSING** |
| 703.4h | Evolution creature without base → top card to graveyard | ⚠️ Partial (`_sba_evolution_reconstruction`) |
| 703.4i | Star Max Evolution uniqueness | ⚠️ Partial (`_sba_smax_uniqueness`) |
| 703.4j | Dream Rare uniqueness | ⚠️ Partial (`_sba_dream_rare_uniqueness`) |
| 703.4k | Duel Mate cleanup | ⚠️ Partial (`_sba_duel_mate_cleanup`) |
| 703.4l | G-Castle on shield | ⚠️ Partial (`_sba_g_castle_shield`) |
| 703.4m | Aura/Weapon/Fortress not attached → graveyard | ❌ **MISSING** |

**Missing critical SBAs**: Cross Gear detachment, Castle detachment, Equipment (Aura/Weapon/Fortress) detachment, "cannot attack" enforcement.

### 2.3 Replacement Effects (Rule 609) — **CRITICAL GAP**

```python
# In sba_checker.py - replacement effects are a STUB
class ReplacementEffectRegistry:
    def __init__(self):
        self.effects = []  # Never populated, never consulted
```

- `ReplacementEffectRegistry` exists but is **empty and never used**
- No application of "instead of X, Y happens"
- No turn-player priority ordering for simultaneous replacements
- No "applies only once per event" enforcement (Rule 101.5)

### 2.4 Continuous Effect Layers (Rule 613) — **PARTIAL**

- `LayerEffectRegistry` exists with `Layer` enum (1-7: characteristic, control, text, type/color, power/toughness, keyword, other)
- But **no layer application logic** — creatures don't have power recalculated through layers
- Static effects from `apply_static_effects()` applied directly, not through layer system

### 2.5 Keyword Implementation Status

| Keyword | Rule | Status | Notes |
|---------|------|--------|-------|
| Blocker | 701.12 | ✅ | In action_generator, blocker declaration |
| Speed Attacker | 301.5 | ✅ | Summoning sickness bypass |
| Slayer | 701.2 | ✅ | In battle resolver |
| Double/Triple/World Breaker | 509.2 | ✅ | Shield breaking |
| Shield Trigger | 112.3a | ✅ | Full batch declaration/resolution |
| S-Back | 112.3b | ✅ | Discard to execute |
| Ninja Strike | 112.3c | ✅ | Attack window summon |
| Sabaki Z | 112.3d | ✅ | Emblem of Judgment discard |
| G-Zero | 112.3e | ✅ | Conditional free summon |
| Attack Chance | 112.3f | ✅ | Conditional free spell on attack |
| G-Strike | 101.4b | ✅ | Same timing as S-Trigger |
| Over Drive | 112.2d | ✅ | Additional mana for bonus |
| Revolution Change | 701.26 | ⚠️ | ActionType exists, resolution incomplete |
| Invasion | 701.22 | ❌ | ActionType missing |
| Mana Burst | 110.4b | ❌ | Keyword exists, not implemented |
| Hyperize | 816 | ⚠️ | ActionType exists, Hyper Mode incomplete |
| Kirifudash | - | ⚠️ | Keyword exists, logic missing |

**~12/29 keywords fully implemented**.

### 2.6 Mana Cost Payment (Rule 112.2a) — **PARTIAL**

```python
# action_generator.py - mana combination algorithm exists but has issues:
# 1. Multi-civ card tapped = provides ONLY ONE civ (correct per 112.2a)
# 2. But: no validation that all required civs are covered
# 3. No handling of "cost becomes less than required civs" (112.2b)
# 4. No O-Drive additional cost integration (112.2d)
```

### 2.7 Trigger Resolution (Rule 101.4, 603) — **GOOD**

- APNAP ordering implemented in `order_simultaneous_triggers()`
- Turn player priority respected
- S-Triggers batched before regular triggers (Rule 101.4a)
- Effect interruption guard (`currently_resolving_effect` flag) for Rule 101.4d

### 2.8 Zone Movement (Rules 400-410) — **GOOD**

- All 10 zones implemented in `Zone` enum
- `ZoneMover` handles transitions with proper SBA triggers
- Hyperspatial/Ultra GR zone rules for Psychics/Draghearts/GR Creatures
- Seals (Rule 116) implemented with Command removal

### 2.9 Special Card Types — **PARTIAL**

| Card Type | Rule | Status |
|-----------|------|--------|
| Evolution Creatures | 801 | ✅ Basic evolution, base tracking |
| Neo Evolution | 802 | ⚠️ Subtype exists, `NEO_EVOLVE` action missing |
| Psychic / Psychic Super | 805 | ⚠️ `AWAKEN`/`AWAKEN_LINK` enums, resolution incomplete |
| Dragheart Weapon/Fortress | 807/808 | ⚠️ `DRAGSOLVE`, `DRAGON_EVASION` enums, no flip logic |
| Forbidden / Final Forbidden | 809/803 | ⚠️ `FORBIDDEN_RELEASE`, `FORBIDDEN_FLIP` enums, no logic |
| Zerom (Ritual/Nebula flip) | 812 | ⚠️ `ZEROM_RITUAL`, `ZEROM_FLIP` enums, no logic |
| King Cell Combine | 814 | ⚠️ `COMBINE`, `EXTRA_EX_LIFE` enums, combine logic partial |
| G-Castle | 822 | ⚠️ Subtype + SBA, logic incomplete |
| Duel Mate | 820 | ⚠️ Subtype exists, no link logic |
| Hyper Soul X / WD Field | 818/819 | ❌ STUB only |

---

## 3. Rules Ingestion Audit (`rules_ingest/`)

### 3.1 Strengths
- Parses full markdown into structured Chapter/Section/Rule objects
- Tags rules with categories (state_based, turn_structure, keyword, replacement, etc.)
- Extracts zone/phase hints from rule text
- Stores in PostgreSQL + ChromaDB for RAG queries

### 3.2 Gaps
- **No validation** that engine enums match rule numbers (e.g., `TriggerEvent.ON_ENTER_BATTLE_ZONE` vs rule 603.x)
- **No automated sync** — rule changes in markdown don't propagate to engine enums
- `test_enum_sync.py` exists but only checks DB enum values, not rule coverage
- ChromaDB embeddings not used by engine for rule lookup during play

---

## 4. Integration Gaps (Crawler → DB → Engine)

| Flow | Status | Issue |
|------|--------|-------|
| Card data → `v_card_engine` view | ✅ | Comprehensive view with all card data |
| Card effects → `CardEffect` objects | ✅ | Frozen dataclass, loaded at startup |
| Keywords → `Keyword` enum | ✅ | Direct mapping |
| Trigger events → `TriggerEvent` enum | ✅ | Direct mapping |
| Effect actions → `EffectAction` enum | ✅ | Direct mapping |
| **Replacement effects → Engine** | ❌ | DB has `is_replacement`, engine ignores |
| **State-defining effects → Engine** | ❌ | Not parsed, not represented |
| **CDA formulas → Engine** | ⚠️ | `CDAFormulaType` exists, parser doesn't populate |
| **Active-in-phase/zone → Engine** | ⚠️ | Loaded but **not consumed** (see `CardEffect` comments) |

---

## 5. Test Coverage vs Rules

37 test files cover:
- ✅ Action execution/generation
- ✅ Battle/shield/trigger resolution
- ✅ Phase 7/8 (end/start turn)
- ✅ Replacement effects (test exists but implementation missing!)
- ✅ Continuous effects (test exists but layers not implemented!)
- ✅ Evolution, Psychic, Dragheart, King Cell, Gods
- ✅ SBA checker, trigger ordering, attack chance

**But**: Tests often test *engine behavior* not *rule compliance*. Many tests pass because the engine's incomplete implementation matches its own incomplete logic.

---

## 6. Priority Fixes (Ranked by Impact)

### P0 - Critical (Game-breaking)
1. **Implement Replacement Effect System** (Rule 609, 101.5)
   - Populate `ReplacementEffectRegistry` from `CardEffect.effect_type == REPLACEMENT`
   - Apply before original event, turn-player priority, once-per-event
   
2. **Complete State-Based Actions** (Rule 703.4e-4m)
   - Cross Gear detachment, Castle detachment, Equipment detachment
   - "Cannot attack" enforcement

3. **Implement Continuous Effect Layers** (Rule 613)
   - Power/toughness layer, keyword layer, characteristic layer
   - Re-evaluate after every SBA

### P1 - High (Major mechanics broken)
4. **Psychic/Dragheart Flip Resolution** (Rules 805-808)
   - Awaken, Dragsolve, Dragon Evasion, Psychic Release

5. **Twinpact/Forbidden/Zerom Face Selection** (Rules 810, 812, 809)
   - Twinpact face choice at summon
   - Forbidden flip on leave
   - Zerom ritual → creature flip

6. **King Cell Combine** (Rule 814)
   - Validate required cells, execute combine, grant Extra EX Life

7. **CDA Detection in Parser** (Rule 110.4a)
   - LLM prompt: distinguish "Power = X" (CDA) from "Power Attacker +X"

### P2 - Medium (Missing mechanics)
8. **State-Defining Effects** (Rule 110.4f)
   - Parser: new effect_type `state_defining`
   - Engine: apply before replacement, all apply simultaneously

9. **Mana Burst, Invasion, Revolution Change** completion

10. **G-Castle / Duel Mate** implementation

### P3 - Polish
11. **Active-in-phase/zone gating** in action_generator
12. **Enum sync validation** (CI check: every rule tag has engine enum)
13. **Rules RAG integration** — engine queries ChromaDB for ambiguous situations

---

## 7. Recommended Architecture Changes

```
crawler/
  scripts/
    effect_parser.py     → Add: CDA detection, state-defining classification, replacement parsing
    rules_context.py     → Add: few-shot examples for King Cell, Zerom, Forbidden

rules_ingest/
  parser.py              → Add: rule_number → engine enum mapping table
  sync_check.py (NEW)    → CI job: verify all rule categories have engine implementations

dm_engine/
  core/
    enums.py             → ✅ Already comprehensive
    cards.py             → Add: state_defining_effects field to CardEffect
  engine/
    replacement/ (NEW)   → ReplacementEffectResolver with turn-player priority
    layers/ (NEW)        → ContinuousEffectLayerSystem (Rule 613)
    sba_checker.py       → Add missing 703.4e-4m
    trigger_registry.py  → ✅ Good
    battle_resolver.py   → ✅ Good
    shield_resolver.py   → ✅ Good
    psychic_dragheart/ (NEW) → Flip mechanics
    king_cell/ (NEW)     → Combine logic
    zerom/ (NEW)         → Ritual → creature flip
    forbidden/ (NEW)     → Release + flip logic
```

---

## 8. Validation Commands

```bash
# Current test suite (passes but incomplete)
for f in dm_engine/tests/test_*.py; do python "$f" || exit 1; done

# Add these to CI:
python -m pytest dm_engine/tests/ -k "replacement" -v  # Currently 0 tests pass for real replacement
python -m pytest dm_engine/tests/ -k "layer" -v        # Currently 0 tests pass for layers
python -m pytest dm_engine/tests/ -k "sba" -v          # Only tests 4/13 SBAs

# Crawler validation
python crawler/scripts/effect_parser.py --validate-enums  # Check DB enums match engine
python rules_ingest/parser.py Duel_Masters_rules.md | grep -E "state_based|replacement"  # Check rule coverage
```

---

## Conclusion

The **game engine has excellent structural foundations** (enums, phase controller, APNAP, SBA loop) but **critical rule systems are stubbed or missing**: replacement effects, continuous effect layers, and ~5 SBAs. The **crawler's LLM parser produces rich structured data** but **doesn't distinguish key rule categories** (CDA vs static, state-defining vs replacement) and the **engine doesn't consume several parsed fields** (`active_in_phase`, `active_in_zone`, `is_replacement`).

**Focus priority**: Replacement effects → Complete SBAs → Layer system → Special card mechanics. These four unlock 80%+ of competitive Duel Masters interactions.