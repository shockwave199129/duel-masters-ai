# Future Card Types — Design Notes

> **Status**: Not blocking for MVP. These card types are defined as stubs (enums, helpers, card definition fields) but their full mechanics are deferred.

---

## Rule 818 — Hyper Soul X

### Summary
Hyper Soul X is a special evolution card subtype. When placed underneath a creature as an evolution card, it grants additional abilities to the creature it's stacked under.

### Card Type Definition
- **CardSubtype**: `HYPER_SOUL_X = "hyper_soul_x"` (added to `core/enums.py`)
- **CardDefinition fields**:
  - `hyper_soul_abilities: list[str]` — stores the abilities granted when underneath a creature
- **Helper**: `is_hyper_soul_x(card_def)` — checks `card_def.card_subtype == CardSubtype.HYPER_SOUL_X`

### Required Engine Changes (Deferred)
1. **Evolution stacking**: When an Hyper Soul X card is placed under a creature, the engine must merge `hyper_soul_abilities` into the creature's active ability list.
2. **Ability resolution**: Granted abilities must be resolved during action generation and effect execution, just like native abilities.
3. **Unstacking**: If the Hyper Soul X card is removed (e.g., by an effect that removes evolution cards), the granted abilities must be revoked.
4. **Interaction with existing systems**:
   - Must work with `ActionGenerator` (granted abilities may add new actions)
   - Must work with `ActionExecutor` (granted triggered/activated/static effects)
   - Must work with `SBAChecker` (some granted abilities may be CDAs affecting power)
   - Must work with `PhaseController` (granted "at end of turn" triggers, etc.)
5. **Deck validation**: Currently rejected by `DeckDefinition.is_valid()` — remove this restriction once implemented.

### Interaction with Existing Systems
- **King Cell (Rule 814)**: Hyper Soul X should NOT interact with King Cell combining — they are separate evolution mechanics.
- **Star Evolution (Rule 813)**: A card could theoretically be both Star Evolution and Hyper Soul X — the engine must handle stacked effects from both.
- **Psychic/Dragheart (Rules 805/807)**: Hyper Soul X cards in the Hyperspatial Zone must be allowed if they have the Psychic or Dragheart subtype.

---

## Rule 819 — WD Field

### Summary
WD Field is a special Field card subtype. WD Field cards are double-sided (like Twinpact/Forbidden) and can be "flipped" between two field faces, each with different effects.

### Card Type Definition
- **CardSubtype**: `WD_FIELD = "wd_field"` (added to `core/enums.py`)
- **CardDefinition fields**:
  - `wd_field_faces: tuple[dict, dict]` — stores the two field faces; each face is a dict of properties (name, effects, etc.)
- **Helper**: `is_wd_field(card_def)` — checks `card_def.card_subtype == CardSubtype.WD_FIELD`

### Required Engine Changes (Deferred)
1. **Double-faced mechanics**: WD Field cards need flip mechanics similar to Twinpact/Forbidden but for Field-type cards in the battle zone (not hand).
2. **Face resolution**: The engine must track which face is currently active and resolve effects from that face only.
3. **Flip action**: A new action type or activated effect to flip the WD Field card to its other face.
4. **Interaction with existing systems**:
   - Must work with `ActionGenerator` (flip as a legal action)
   - Must work with `ActionExecutor` (flip execution, face resolution)
   - Must work with zone management (Field cards go to specific zones, not hand/battle zone like creatures)
   - Must work with `PhaseController` (face-specific "at start of turn" triggers)
5. **Deck validation**: Currently rejected by `DeckDefinition.is_valid()` — remove this restriction once implemented.

### Interaction with Existing Systems
- **D2 Field (Rule 308)**: WD Field is a separate subtype from D2 Field — they should not share mechanics.
- **Twinpact/Forbidden flip**: The flip mechanic for WD Field is conceptually similar but applies to Field cards rather than Creature/Spell cards. The existing `flip_twinpact` / `flip_forbidden` functions in `engine/zone_mover.py` provide a reference pattern but cannot be reused directly.
- **Static effects**: Each face may have different static effects — the engine must re-evaluate static effects after a flip.

---

## Implementation Priority
1. **Hyper Soul X** is higher priority — it's a straightforward evolution mechanic that extends existing stacking logic.
2. **WD Field** is lower priority — it requires new double-faced mechanics for Field cards, which is a more significant architectural change.

## Related Files
- `dm_engine/core/enums.py` — `CardSubtype` enum
- `dm_engine/core/cards.py` — `CardDefinition` dataclass, helper functions, `DeckDefinition.is_valid()`
- `dm_engine/engine/zone_mover.py` — existing flip mechanics (reference pattern)
- `dm_engine/engine/action_executor.py` — action execution (must handle new actions)
- `dm_engine/engine/action_generator.py` — legal action generation (must include new actions)
