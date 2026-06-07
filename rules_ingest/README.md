# Duel Masters Rules Ingestion Pipeline

Parses `Duel_Masters_rules.md` (Ver 1.50) and loads structured rule data
into **PostgreSQL** (game engine) and **ChromaDB** (semantic retrieval for LLM).

---

## Project Structure

```
rules_ingest/
├── main.py                     ← single entry point (run this)
├── sql/
│   └── schema.sql              ← all PostgreSQL table definitions
├── parser.py                   ← markdown → Python dataclasses
├── seed_data.py                ← hand-crafted engine data (phases, keywords, SBAs)
├── ingest_postgres.py          ← PostgreSQL writer
└── ingest_chroma.py            ← ChromaDB writer + DMRulesRetriever helper
```

---

## Quick Start

```bash
pip install -r requirements.txt

cat > .env <<'EOF'
DATABASE_URL=postgresql://postgres:12345@localhost:5432/dm_database
EOF

# Both databases
python -m rules_ingest.main \
    --md    Duel_Masters_rules.md \
    --chroma ./dm_chroma_db \
    --openai-key sk-...         # optional; omit for local embeddings

# PostgreSQL only
python -m rules_ingest.main --md ... --no-chroma

# ChromaDB only
python -m rules_ingest.main --md ... --chroma ... --no-postgres
```

---

## What Gets Stored

### PostgreSQL

| Table | Rows (approx.) | Purpose |
|---|---|---|
| `dm_chapters` | 9 | Top-level chapters (0–8) |
| `dm_sections` | 108 | Rule sections (100, 101 …) |
| `dm_rules` | 725 | Every individual rule + engine tags |
| `dm_keywords` | 38 | Keyword definitions + behavior flags |
| `dm_game_phases` | 10 | Turn steps + sub-steps |
| `dm_phase_actions` | ~35 | Ordered actions per phase |
| `dm_state_based_actions` | 13 | 703.4a–703.4m with JSON conditions |
| `dm_rule_relations` | ~500 | Parent → child rule links |

#### Key columns on `dm_rules`

| Column | Type | Example |
|---|---|---|
| `rule_number` | TEXT | `"703.4c"`, `"509.5a"` |
| `rule_category` | TEXT | `"state_based"`, `"keyword"`, `"turn_structure"` |
| `applies_in_phase` | TEXT[] | `{"direct_attack"}`, `{"main"}` |
| `applies_in_zone` | TEXT[] | `{"battle_zone", "hand"}` |
| `is_state_based` | BOOLEAN | auto-checked every game action |
| `is_turn_based` | BOOLEAN | fires on step transitions |
| `priority` | INT | lower = checked first (SBAs = 10) |

### ChromaDB

One collection `dm_rules` — every rule embedded as a document.
Metadata fields mirror `dm_rules` columns (ChromaDB-compatible flat types).

---

## How the Game Bot Uses This

```
Game Event Occurs
       │
       ├─ 1. Check dm_state_based_actions  (PostgreSQL, priority-ordered)
       │       "Did any creature reach 0 power?"
       │
       ├─ 2. Check dm_phase_actions        (PostgreSQL, current phase)
       │       "It's draw step → turn player draws 1 card"
       │
       ├─ 3. Check dm_card_keywords JOIN   (PostgreSQL)
       │       "Attacking creature has Speed Attacker + Double Breaker"
       │
       └─ 4. Ambiguous edge case → RAG     (ChromaDB)
               query = "does Dragon Evasion apply before replacement effects?"
               → top-5 relevant rules → LLM with game state as context
```

### Using the Retriever in Your Chatbot

```python
from rules_ingest.ingest_chroma import DMRulesRetriever

retriever = DMRulesRetriever("./dm_chroma_db", openai_key="sk-...")

# Semantic search
results = retriever.search(
    "can I use Ninja Strike after opponent declares attack",
    phase="attack_declare",
    n=5,
)

# Exact rule lookup
rule = retriever.get_rule("509.5a")

# Get all state-based actions (for game engine loop)
sbas = retriever.get_state_based_actions()

# Build LLM prompt context
context = retriever.build_context_for_event(
    event_description="player uses Revolution Change",
    current_phase="attack_declare",
)
```

### Querying PostgreSQL Directly

```python
import psycopg2

conn = psycopg2.connect("postgresql://...")
cur = conn.cursor()

# Get all SBAs ordered by priority (run this every game tick)
cur.execute("""
    SELECT rule_number, action_key, condition_json, effect_json, priority
    FROM dm_state_based_actions
    ORDER BY priority
""")

# Get rules for the current game phase
cur.execute("""
    SELECT rule_number, text
    FROM dm_rules
    WHERE %s = ANY(applies_in_phase)
      AND rule_category = 'turn_structure'
    ORDER BY priority
""", ("attack_declare",))

# Get keywords for cards in play (join with your cards table)
cur.execute("""
    SELECT c.name, k.name AS keyword, k.requires_declaration,
           k.overrides_summoning_sickness, ck.parameters
    FROM dm_card_keywords ck
    JOIN dm_keywords k ON k.id = ck.keyword_id
    JOIN cards c       ON c.id = ck.card_id
    WHERE ck.card_id = ANY(%s)
""", ([card_id_1, card_id_2],))
```

---

## Adding Card Keywords

After ingestion, link your scraped card data to keywords:

```python
# Example: link card_id=42 ("Bolshack Dragon") to Double Breaker
cur.execute("""
    INSERT INTO dm_card_keywords (card_id, keyword_id, parameters)
    SELECT 42, id, NULL
    FROM dm_keywords WHERE name = 'Double Breaker'
    ON CONFLICT DO NOTHING
""")
```

Or batch-insert from your card scrape:
```python
for card in scraped_cards:
    for kw_name in card["keywords"]:
        cur.execute("""
            INSERT INTO dm_card_keywords (card_id, keyword_id, parameters)
            SELECT %s, id, %s
            FROM dm_keywords WHERE name = %s
            ON CONFLICT DO NOTHING
        """, (card["id"], card.get("kw_params", {}).get(kw_name), kw_name))
```

---

## Parser Output Stats (Ver 1.50)

```
Chapters        :   9
Sections        : 108
Rules (total)   : 725
  State-based   :  13   (703.4a – 703.4m)
  Keyword rules :  52   (section 701)
  Turn-structure:  91   (sections 500–512)
```
