You are an experienced, pragmatic software engineering AI agent. Do not over-engineer a solution when a simple one is possible. Keep edits minimal. If you want an exception to ANY rule, you MUST stop and get permission first.

# AGENTS.md

## Project Overview
Duel Masters AI toolkit: a Python monorepo for scraping OCG card data, ingesting comprehensive rules into PostgreSQL/ChromaDB, simulating games with a full rules engine, and training neural bots via self-play. Three main modules:
- `crawler/` — card scraper pipeline + LLM effect parser (Playwright + OpenRouter/OpenAI)
- `rules_ingest/` — markdown → structured rules → PostgreSQL/ChromaDB pipeline
- `dm_engine/` — game simulator, bots (RandomBot, NeuralBot), self-play training

**Technology stack**: Python 3.10+, PostgreSQL, ChromaDB, Playwright, PyTorch, OpenAI/OpenRouter APIs (optional local-only: Transformers, TRL, PEFT for LLM fine-tuning)

## Reference

### Important Directories
| Path | Purpose |
|------|---------|
| `crawler/` | Card scraping pipeline (4 stages: sets → set pages → cards → LLM parse) |
| `crawler/scripts/` | Pipeline stage implementations |
| `crawler/sql/schema.sql` | PostgreSQL schema for card data |
| `rules_ingest/` | Rules markdown parser + DB ingestion |
| `dm_engine/` | Core game engine, bots, training |
| `dm_engine/core/` | Game state, cards, enums, actions, zones, player state, global effects |
| `dm_engine/core/actions/` | Action dataclass + constructor functions (split from core/actions.py) |
| `dm_engine/core/cards/` | CardDefinition, CardEffect, DeckDefinition + helpers (split from core/cards.py) |
| `dm_engine/core/zones/` | HandCard, ManaCard, ShieldCard, Creature, etc. (split from core/zones.py) |
| `dm_engine/engine/` | Action execution, generation, battle/shield/trigger/zone resolvers, phase controller |
| `dm_engine/engine/turn/` | Phase-specific action generators (split from engine/action_generator.py) |
| `dm_engine/engine/cards/effect_actions/` | Per-EffectAction handlers (split from engine/effect_executor.py) |
| `dm_engine/engine/sba/` | State-based action checker + per-SBA modules (split from engine/sba_checker.py) |
| `dm_engine/engine/special_cards/` | Card-family-specific mechanics (split from engine/zone_mover.py) |
| `dm_engine/bot/` | RandomBot, NeuralBot, NeuralModel, state/action encoders (v2/v3) |
| `dm_engine/decks/` | Prebuilt deck definitions and JSON loader |
| `dm_engine/db/` | Card database interface (PostgreSQL) |
| `dm_engine/training/` | Self-play orchestration, reward shaping, action-score training |
| `dm_engine/models/` | Trained `.pt` checkpoints (git-ignored) |
| `dm_engine/tests/` | 37 standalone test files (see list below) |
| `dm_engine/scripts/` | CLI entry points for play, self-play, training |
| `data/` | Generated self-play data and reports (git-ignored) |
| `state/` | Crawler pipeline state file (git-ignored) |
| `Duel_Masters_rules.md` | Canonical rules source for ingestion and audits |

**Local-only (git-ignored, not in repo):**

| Path | Purpose |
|------|---------|
| `finetune_parser/` | LLM finetuning scripts for Japanese card effect parsing (LFM25-JP) |
| `lfm25-jp-duelmasters/` | Intermediate LoRA checkpoints |
| `lfm25-jp-duelmasters-final/` | Final LoRA adapter + tokenizer |
| `dm_train/` / `dm_val/` | Hugging Face Arrow datasets for LLM finetuning |

### Key Files
- `requirements.txt` — Python dependencies
- `pyrightconfig.json` — Type checking config (extraPaths: dm_engine)
- `.pylintrc` — Lint config (sys.path append for dm_engine)
- `.github/workflows/ci.yml` — CI: compile + run engine tests
- `crawler/main.py` — Crawler CLI (`run`, `discover-sets`, `discover-cards`, `scrape-cards`, `parse-effects`, `single`, `status`, `retry-errors`)
- `rules_ingest/main.py` — Rules ingest CLI (`--md`, `--dsn`, `--chroma`, `--openai-key`, `--no-postgres`, `--no-chroma`)
- `dm_engine/scripts/play_neural_game.py` — Play neural bot games
- `dm_engine/scripts/run_self_play.py` — Self-play data generation
- `dm_engine/scripts/train_action_score.py` — Train action scoring model

### Engine Test Files (`dm_engine/tests/`)
| File | Focus |
|------|-------|
| `test_action_executor.py` | Action execution & state mutation |
| `test_action_generator.py` | Legal action generation |
| `test_actions.py` | GameAction dataclasses & serialization |
| `test_battle_shield_resolvers.py` | Battle zone & shield trigger resolution |
| `test_continuous_effects.py` | Static ability / continuous effect layers |
| `test_effect_executor.py` | Triggered effect resolution |
| `test_phase7_8.py` | Turn phase 7 (end) & 8 (start) logic |
| `test_replacement_effects.py` | Replacement effect application order |
| `test_special_cards.py` | Specific card implementations |
| `test_state_manager.py` | GameState serialization / cloning |
| `test_rule_knowledge.py` | Rules DB query integration |
| `test_crawler_engine_integration.py` | Card DB → engine integration |
| `test_enum_sync.py` | Enum ↔ database sync validation |
| ...and 24 more (`test_ability_types`, `test_apnap_trigger_ordering`, `test_attack_chance`, `test_calculation_order`, `test_cost_modifiers`, `test_effect_interruption`, `test_evolution_rules`, `test_game_loop`, `test_gods_core`, `test_king_cell_rules`, `test_mana_validation`, `test_negative_power`, `test_neural_v3_features`, `test_over_drive`, `test_prebuilt_decks`, `test_psychic_dragheart_rules`, `test_replacement_apnap`, `test_rule_cleanup`, `test_sabaki_z`, `test_sba_checker`, `test_s_trigger_batch`, `test_train_action_score_v3`, `test_training_deck_sampling`, `test_trigger_effect_resolvers`) |

## Essential Commands

### Setup
```bash
# Install Python dependencies
python -m pip install -r requirements.txt

# Install Playwright browser for crawler
playwright install chromium

# Create .env with required variables
# DATABASE_URL=postgresql://user:pass@localhost:5432/dm_db
# Optional LLM: OPENROUTER_API_KEY, OPENAI_API_KEY, LLM_PROVIDER=openrouter
```

### Testing
```bash
# Run all dm_engine tests (standalone scripts)
for f in dm_engine/tests/test_*.py; do python "$f" || exit 1; done

# Run single test file
python dm_engine/tests/test_action_executor.py

# Run with pattern filter (pytest)
python -m pytest dm_engine/tests/ -k "battle" -v

# Crawler smoke test
python crawler/test_set_page_crawler.py

# Crawler OpenRouter test
python crawler/test_openroute.py
```

### Rules Ingestion
```bash
# Parse rules into PostgreSQL + ChromaDB
python -m rules_ingest.main --md Duel_Masters_rules.md --chroma ./dm_chroma_db

# PostgreSQL only
python -m rules_ingest.main --md Duel_Masters_rules.md --no-chroma
```

### Card Crawler
```bash
cd crawler
# Full pipeline
python main.py run --series both

# Parse pending card effects only
python main.py parse-effects --batch-size 100 --cards-per-call 2

# Parse cards used by prebuilt decks
python scripts/parse_prebuilt_decks.py --llm-provider openai --model gpt-5-nano --cards-per-call 1
```

### Game Engine
```bash
# Neural vs neural game
python dm_engine/scripts/play_neural_game.py --mode neural-vs-neural --max-steps 1000

# Save game report
python dm_engine/scripts/play_neural_game.py --model-path dm_engine/models/gen1_v2_action_score.pt --mode neural-vs-neural --report-path data/reports/gen1_v2_game.txt
```

### Neural Self-Play Training
```bash
# Generate self-play data (presets: quick=50, standard=100, large=500)
python dm_engine/scripts/run_self_play.py --preset quick --output data/self_play/gen0_v2_games.jsonl --overwrite

# Train generation 1
python dm_engine/scripts/train_action_score.py --input data/self_play/gen0_v2_games.jsonl --output dm_engine/models/gen1_v2_action_score.pt --epochs 10

# Generate gen 2 data from gen 1 model
python dm_engine/scripts/run_self_play.py --preset standard --model-path dm_engine/models/gen1_v2_action_score.pt --output data/self_play/gen1_v2_games.jsonl --overwrite

# Train generation 2
python dm_engine/scripts/train_action_score.py --input data/self_play/gen1_v2_games.jsonl --output dm_engine/models/gen2_v2_action_score.pt --epochs 10
```

### Lint / Type Check
```bash
# Type check (pyright)
python -m pyright

# Lint (pylint)
python -m pylint dm_engine rules_ingest crawler/scripts
```

### Clean
```bash
# Remove Python cache
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
find . -name "*.pyc" -delete

# Remove generated data (careful - not tracked by git)
# rm -rf data/self_play/ data/reports/ dm_engine/models/
```

## Patterns

### Module Imports
```python
# Standard library first
import sys
from dataclasses import dataclass
from typing import Optional

# Third-party
import torch
from pydantic import BaseModel

# Local (via sys.path.insert)
sys.path.insert(0, "dm_engine")
from core.enums import Zone, CardType
from core.game_state import GameState
```

### Test Pattern (standalone scripts)
Tests are standalone Python scripts using inline `check()` helper:
```python
def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "✅ PASS" if condition else "❌ FAIL"
    print(f"{status}: {name}" + (f" - {detail}" if detail else ""))
    return condition

# Usage
check("Player 0 has 5 shields", len(state.players[0].shields) == 5)
```

### Enum-Driven Design
Enums in `dm_engine/core/enums.py` are the single source of truth for game concepts (Zone, CardType, Phase, Step, TriggerCondition, etc.). Always import from there rather than defining string constants.

### Action Execution Flow
1. `ActionGenerator.generate_actions(state, player)` → list of legal `GameAction`
2. Bot selects action index
3. `ActionExecutor.execute(state, action)` → mutates state, returns events
4. `PhaseController` advances phases/turns, runs SBA checker

### Self-Play Data Format (v2)
JSONL lines contain: `game_id`, `step`, `state_tensor`, `legal_actions`, `chosen_action_idx`, `policy_target`, `value_target`, `heuristic_target`, `blended_target`.

## Anti-Patterns

| Pattern | Reason | Correct Approach |
|---------|--------|------------------|
| Hardcoding zone/card type strings | Single source of truth is `core/enums.py` | Import `Zone`, `CardType`, etc. from enums |
| Direct DB connections in modules | Connection management centralized | Use `dm_engine/db/card_db.py` or `rules_ingest` helpers |
| Skipping SBA checks | State-based actions must run after every mutation | Call `SBAChecker.run_all(state)` in `PhaseController` |
| Using pytest fixtures for engine tests | Tests are standalone scripts by design | Write self-contained scripts with `check()` helper |
| Committing `.env` or generated files | Secrets and large artifacts | `.gitignore` covers these; never commit |
| Committing LLM fine-tuning artifacts | Large checkpoints/datasets; local workflow only | Keep `finetune_parser/`, `lfm25-jp-*`, `dm_train/`, `dm_val/` git-ignored |
| Importing torch in lightweight modules | Increases startup time, breaks CI | Lazy import or separate heavy module |

## Code Style
- Python 3.10+ (uses `match`/`case`, `frozenset`, `dataclasses`, type hints)
- No external formatter/linter configured — follow existing patterns in each module
- Imports: stdlib first, then third-party, then local (relative via `sys.path.insert`)
- Tests use inline `check(name, condition, detail)` helper with PASS/FAIL emoji output
- Module-level constants use `UPPER_SNAKE_CASE`
- Enums in `dm_engine/core/enums.py` are the single source of truth for game concepts

## Module → Rules Chapter Map

| Module | Rules chapter | Key rules |
|--------|--------------|-----------|
| `core/actions/` | Ch 1 (Game Basics) | 101.2, 112.2a, 112.3, 503.1, 506.1, 509.2 |
| `core/cards/` | Ch 2 (Card Anatomy) | 200.3c, 207, 809, 810, 812, 816-822 |
| `core/zones/` | Ch 4 (Zones) | 400-410 |
| `engine/action_generator.py` + `engine/turn/action_gen.py` | Ch 5 (Turn Structure) | 500-512 |
| `engine/effect_executor.py` + `engine/cards/effect_actions/` | Ch 6 (Spells & Abilities) | 601-609 |
| `engine/sba/checker.py` + `engine/sba/actions/` | Ch 7 (State-Based Actions) | 703.4 |
| `engine/zone_mover.py` | Ch 4 (Zones) | 400-410 |
| `engine/special_cards/` | Ch 8 (Special Cards) | 800-822 |

- Type hints on all public functions; `pyrightconfig.json` sets `extraPaths: ["dm_engine"]`
- Pylint init-hook adds `dm_engine` to sys.path

## Commit and Pull Request Guidelines

### Commit Message Convention
Follow conventional commits: `type: message`
- `feat:` — new feature
- `fix:` — bug fix
- `refactor:` — code restructuring without behavior change
- `test:` — adding/updating tests
- `docs:` — documentation changes
- `chore:` — maintenance, deps, CI

Examples from history:
- `Refactor import structure in self-play script`
- `Add training deck management and self-play enhancements`
- `Add GitHub Actions CI workflow`

### Pre-Commit Validation
Before committing, run:
```bash
# 1. Type check
python -m pyright

# 2. Lint
python -m pylint dm_engine rules_ingest crawler/scripts

# 3. Run all engine tests
for f in dm_engine/tests/test_*.py; do python "$f" || exit 1; done

# 4. Compile check (CI does this)
python -m compileall dm_engine rules_ingest crawler/scripts
```

### Pull Request Requirements
- All tests pass (CI must be green)
- No `.env` files or generated artifacts committed
- New features include tests (standalone scripts in `dm_engine/tests/`)
- Update relevant README.md if CLI interface changes
- Keep changes minimal and focused; large refactors should be split

## Development Workflows

### Adding a New Card Effect
1. Add card to PostgreSQL via crawler (`crawler/main.py scrape-cards`)
2. Parse effect with LLM (`crawler/main.py parse-effects`)
3. Implement effect logic in `dm_engine/engine/` resolvers
4. Add test case in `dm_engine/tests/`

### Adding a New Rules Section
1. Edit `Duel_Masters_rules.md`
2. Run `rules_ingest/main.py` to update PostgreSQL/ChromaDB
3. Verify with `dm_engine/tests/test_rule_knowledge.py`

### Training a New Model Generation
1. Generate self-play data: `run_self_play.py --preset standard --model-path <prev_gen> --output data/self_play/gen{N}_v2_games.jsonl`
2. Train: `train_action_score.py --input data/self_play/gen{N}_v2_games.jsonl --output dm_engine/models/gen{N+1}_v2_action_score.pt --epochs 10`
3. Evaluate: `play_neural_game.py --model-path dm_engine/models/gen{N+1}_v2_action_score.pt --mode neural-vs-neural`

### Finetuning Japanese Effect Parser (LFM25-JP, local-only)
Optional workflow; scripts and outputs are git-ignored and must exist locally.
1. Prepare dataset from scraped cards: `python finetune_parser/prepare_dataset.py`
2. Run LoRA finetuning: `python finetune_parser/finetune_lfm25_jp.py`
3. Adapter outputs to `lfm25-jp-duelmasters-final/` (adapter_model.safetensors, tokenizer, config)
4. Use finetuned model for `crawler/main.py parse-effects` via OpenAI-compatible server
