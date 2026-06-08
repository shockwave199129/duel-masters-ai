# Duel Masters AI

Python tooling for Duel Masters card scraping, rules ingestion, game simulation,
bot play, and neural self-play training.

The project is split into three main parts:

- `crawler/` scrapes Duel Masters card data and parses card effects with LLMs.
- `rules_ingest/` parses `Duel_Masters_rules.md` into PostgreSQL and ChromaDB.
- `dm_engine/` runs games, loads decks, executes legal actions, and trains neural bots.

## Repository Layout

```text
.
├── Duel_Masters_rules.md       # Rule source used by rules_ingest and audits
├── crawler/                    # Card crawler and LLM effect parser
├── rules_ingest/               # Rules parser + PostgreSQL/Chroma ingestion
├── dm_engine/                  # Duel Masters simulator and bot training
├── requirements.txt            # Python dependencies
└── data/                       # Generated self-play/reports, ignored by Git
```

Generated files are intentionally ignored:

- `.env` files
- crawler state and logs
- self-play JSONL files under `data/self_play/`
- readable game reports under `data/reports/`
- trained model checkpoints under `dm_engine/models/`

## Setup

Install dependencies:

```bash
python -m pip install -r requirements.txt
playwright install chromium
```

Create `.env` or `crawler/.env` with at least:

```bash
DATABASE_URL=postgresql://user:pass@localhost:5432/dm_db
```

Optional LLM settings for effect parsing:

```bash
OPENROUTER_API_KEY=sk-or-v1-...
OPENAI_API_KEY=sk-...
LLM_PROVIDER=openrouter
```

## Rules Ingestion

Parse `Duel_Masters_rules.md` into PostgreSQL and optionally ChromaDB:

```bash
python -m rules_ingest.main \
  --md Duel_Masters_rules.md \
  --chroma ./dm_chroma_db
```

PostgreSQL only:

```bash
python -m rules_ingest.main --md Duel_Masters_rules.md --no-chroma
```

See `rules_ingest/README.md` for details.

## Card Crawler

Run the full crawler pipeline:

```bash
cd crawler
python main.py run --series both
```

Parse only pending card effects:

```bash
python main.py parse-effects --batch-size 100 --cards-per-call 2
```

Parse only cards used by `dm_engine/decks/prebuilt_game.json`:

```bash
python scripts/parse_prebuilt_decks.py \
  --llm-provider openai \
  --model gpt-5-nano \
  --cards-per-call 1
```

See `crawler/README.md` for provider-specific OpenRouter, OpenAI, and Ollama examples.

## Game Engine

The engine loads two decks, creates a `GameState`, generates legal actions, and
executes one legal action at a time.

Run neural bot vs neural bot using the prebuilt decks:

```bash
python dm_engine/scripts/play_neural_game.py \
  --mode neural-vs-neural \
  --max-steps 1000
```

Save a player-friendly text report:

```bash
python dm_engine/scripts/play_neural_game.py \
  --model-path dm_engine/models/gen1_v2_action_score.pt \
  --mode neural-vs-neural \
  --report-path data/reports/gen1_v2_game.txt
```

See `dm_engine/README.md` for deck JSON format, bot usage, and engine details.

## Neural Self-Play Training

Run self-play and save training decisions:

```bash
python dm_engine/scripts/run_self_play.py \
  --preset quick \
  --output data/self_play/gen0_v2_games.jsonl \
  --overwrite
```

Presets are `quick` (50 games), `standard` (100 games), and `large` (500 games).
The v2 dataset records all legal actions, chosen action index, policy target,
value target, heuristic target, and blended target.

Train generation 1:

```bash
python dm_engine/scripts/train_action_score.py \
  --input data/self_play/gen0_v2_games.jsonl \
  --output dm_engine/models/gen1_v2_action_score.pt \
  --epochs 10
```

Generate generation 2 data from generation 1:

```bash
python dm_engine/scripts/run_self_play.py \
  --preset standard \
  --model-path dm_engine/models/gen1_v2_action_score.pt \
  --output data/self_play/gen1_v2_games.jsonl \
  --overwrite
```

Train generation 2:

```bash
python dm_engine/scripts/train_action_score.py \
  --input data/self_play/gen1_v2_games.jsonl \
  --output dm_engine/models/gen2_v2_action_score.pt \
  --epochs 10
```

Self-play randomizes first player and deck seating by default to reduce bias.
Use `--fixed-seating` only when you want Player 0 to always use the first deck.

## Tests

Engine tests are standalone scripts:

```bash
for f in dm_engine/tests/test_*.py; do python "$f" || exit 1; done
```

Run one crawler smoke test:

```bash
python crawler/test_set_page_crawler.py
```

## GitHub Actions

The repository includes a lightweight CI workflow at `.github/workflows/ci.yml`.
On pushes and pull requests to `main`, it:

- compiles Python sources in `dm_engine`, `rules_ingest`, and `crawler/scripts`
- runs all standalone `dm_engine/tests/test_*.py` scripts

The workflow intentionally avoids DB/API/LLM-dependent crawler jobs because
those require local services and secrets.

## Current Status

This is an experimental Duel Masters AI stack. The engine can run legal-action
bot games and record simple reinforcement-learning data, but card-effect support
and strategic training quality are still evolving. Treat generated models as
early experiments rather than strong Duel Masters agents.
