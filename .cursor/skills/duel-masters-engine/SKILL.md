---
name: duel-masters-engine
description: Build, review, and debug the Duel Masters rules engine using Duel_Masters_rules.md as the authoritative source. Use when working on dm_engine, card effect parsing, gameplay simulation, legal actions, state-based actions, turn flow, battle, shields, triggers, or tests.
---

# Duel Masters Engine

## Source Of Truth

Always treat `Duel_Masters_rules.md` as authoritative for gameplay behavior.

Before changing rules logic, read the relevant sections from `Duel_Masters_rules.md` and prefer the rule document over comments, chat notes, assumptions, or memory.

## Workflow

1. Identify the exact gameplay area: initialization, zones, mana payment, turn flow, actions, battle, shields, triggers, state-based actions, or effects.
2. Read the matching rules from `Duel_Masters_rules.md`.
3. Inspect the relevant `dm_engine/` files and tests.
4. If code comments conflict with the rules, call out the mismatch and implement the rule document.
5. Add or update focused tests for the rule behavior before broad refactors.
6. Run the relevant test scripts under `dm_engine/tests/`.

## Current Engine Priorities

- Implement `action_executor.py`, `phase_controller.py`, `trigger_resolver.py`, and battle resolution before relying on self-play data.
- Fix direct attack handling so breaking the last shield is not treated as a direct attack win.
- Model multi-shield S-Trigger, G-Strike, and S-Back declaration timing correctly.
- Correct Ninja Strike timing and cost requirements from rule `112.3c`.
- Process state-based actions as simultaneous events, then repeat checks.
- Model colorless cards as having no civilization, not as a sixth civilization.
- Enforce deck legality before game initialization.

## Review Output

When reviewing code, lead with findings ordered by severity. Include:

- The affected file or symbol.
- The rule number from `Duel_Masters_rules.md`.
- The concrete behavior mismatch.
- A short suggested fix or test case.

## Test Guidance

Prefer small rule-specific tests, especially for:

- Multi-civilization mana payment.
- Direct attack versus shield breaking.
- Double Breaker / Triple Breaker S-Trigger declaration timing.
- Battle power comparison and simultaneous destruction.
- State-based action simultaneity.
- First-player first-turn draw skip.
