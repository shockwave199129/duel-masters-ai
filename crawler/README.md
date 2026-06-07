# dm_scraper

Resumable 3-level Duel Masters card scraper + LLM effect parser.

## Pipeline Architecture

```
Level 1 — sets_crawler.py
  Crawls "List of OCG/TCG Sets" pages
  → Discovers all set URLs (DM-01, DMRP-22, etc.)
  → Stored in: card_sets table + state/crawl_state.json

Level 2 — set_page_crawler.py
  For each set page, extracts the MAIN card list section
  Skips reprint/alt-art/bonus sub-sections
  → Discovers card page URLs
  → Stored in: card_urls table

Level 3 — scraper.py
  For each card URL, fetches the wiki page
  Handles both portable infobox (new) and wikitable (old) formats
  Deduplicates reprints: same card in multiple sets gets one cards row,
  multiple card_printings rows
  → Stored in: cards, card_civilizations, card_races, card_printings, etc.

Level 4 — effect_parser.py
  For each scraped card with ■ abilities, calls an OpenAI-compatible LLM once
  The LLM receives optional Duel Masters rules context from PostgreSQL/ChromaDB
  and parses all abilities into structured JSON
  → Stored in: card_effects
```

## Resume / Fault Tolerance

State is stored in TWO places:
- **PostgreSQL** (`card_sets.scraped_at`, `card_urls.status`) — primary truth
- **`state/crawl_state.json`** — fast checkpoint, read on startup

On restart, the pipeline automatically:
- Skips sets already fully discovered (status='done')
- Skips card URLs already scraped/parsed (status='scraped'|'parsed')
- Retries sets/cards in 'error' status (or use `retry-errors` command)

You can `Ctrl+C` safely — the current card finishes, state is saved, and
the next run continues from exactly where it left off.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium

# Create DB tables
psql -d dm_db -f sql/schema.sql
```

Create `crawler/.env`:

```bash
DATABASE_URL=postgresql://user:pass@localhost:5432/dm_db
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=nvidia/nemotron-3-super-120b-a12b:free
LLM_PROVIDER=openrouter
# For OpenAI instead:
# LLM_PROVIDER=openai
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-4o-mini
# For local/cloud Ollama instead:
# LLM_PROVIDER=ollama
# OLLAMA_HOST=http://localhost:11434
# OLLAMA_MODEL=nemotron-3-nano:4b
# OLLAMA_MODEL=minimax-m2.5:cloud
DM_RULES_DATABASE_URL=postgresql://user:pass@localhost:5432/dm_db
DM_RULES_CHROMA_PATH=../dm_chroma_db
# Only set this if rules ChromaDB was built with OpenAI embeddings.
# Leave unset for ChromaDB default embeddings.
# DM_RULES_EMBEDDING_KEY=sk-...
CF_COOKIE_URL=https://duelmasters.fandom.com/wiki/DM-01_Base_Set
PLAYWRIGHT_HEADLESS=true
```

At startup, the crawler opens Chromium once with Playwright to collect Fandom /
Cloudflare cookies, then reuses those cookies in the normal `curl_cffi` requests
sessions.

## Usage

```bash
# Full pipeline from scratch
python main.py run \
  --series both

# Resume after interruption (same command — auto-skips done work)
python main.py run \
  --series both

# Check progress
python main.py status

# Run one stage at a time:

# Stage 1 — discover all set URLs
python main.py discover-sets --series both

# Stage 2 — discover card URLs from set pages
python main.py discover-cards

# Stage 3 — scrape card pages (no API key needed)
python main.py scrape-cards

# Stage 4 — LLM parse abilities (costs API credits)
python main.py parse-effects --batch-size 100 --cards-per-call 2

# Use OpenAI
python main.py parse-effects \
  --llm-provider openai \
  --api-key "$OPENAI_API_KEY" \
  --model gpt-5-nano \
  --batch-size 20 \
  --cards-per-call 2 \
  --max-tokens 20000

# Use local Ollama
python main.py parse-effects \
  --llm-provider ollama \
  --model nemotron-3-nano:4b \
  --batch-size 20 \
  --cards-per-call 2

# Use Ollama cloud model
python main.py parse-effects \
  --llm-provider ollama \
  --model minimax-m2.5:cloud \
  --batch-size 20 \
  --cards-per-call 2

# Parse with rules context disabled
python main.py parse-effects --batch-size 100 --no-rule-context

# Override model/provider for one run
python main.py parse-effects \
  --batch-size 100 \
  --cards-per-call 2 \
  --api-key "$OPENROUTER_API_KEY" \
  --base-url https://openrouter.ai/api/v1 \
  --model nvidia/nemotron-3-super-120b-a12b:free

# Test a single card
python main.py single \
  --url "https://duelmasters.fandom.com/wiki/Bolshack_Dragon"

# Reset error cards for retry
python main.py retry-errors
```

## Reprint Handling

Cards that appear in multiple sets are deduplicated automatically:
- `cards` table: one row per unique card (keyed by wiki slug)
- `card_printings` table: one row per (card × set × collector_num)

So Bolshack Dragon appearing in DM-05, DM-22, and DMBD-10 will have:
- 1 row in `cards`
- 3 rows in `card_printings`

## Set Page Parsing

Set pages vary widely in structure. The parser:
1. Looks for headings like "Card List", "Cards in this Set" etc. — extracts only those sections
2. Skips headings matching: "Reprint", "New Artwork", "Alt Art", "Secret", "Promo", "Bonus"
3. Falls back to full-page scan if no recognizable sections found

This ensures we collect each card once (from its first/canonical set) while still
recording the printing in `card_printings` for every set it appears in.

## Database Schema

See `sql/schema.sql`. Key tables:

| Table | Purpose |
|---|---|
| `card_sets` | All sets (DM-01 through latest) |
| `card_urls` | Card URLs discovered per set, with scrape status |
| `cards` | Core card data (one row per unique card) |
| `card_civilizations` | Card ↔ civilization (many-to-many) |
| `card_races` | Card ↔ race (many-to-many) |
| `card_printings` | Card × set × rarity × image |
| `card_effects` | LLM-parsed structured abilities |
| `card_rulings` | Official rulings text |
| `card_keywords` | Keyword links |
| `card_relations` | Twin Pact / evolution links |

View `v_card_engine` — full card with effects as JSON array for game engine.

## Estimated Time

~8,000–12,000 unique cards across all OCG sets.
At 2s/card avg delay: ~5–7 hours for scraping.
LLM parsing at ~0.5s/card: ~1–2 hours additional.
Total with resume support: run overnight, resume next day if needed.
