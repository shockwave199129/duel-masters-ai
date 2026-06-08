-- ============================================================
-- dm_scraper schema
-- ============================================================

-- Card sets discovered from the sets-list page
CREATE TABLE IF NOT EXISTS card_sets (
    id            SERIAL PRIMARY KEY,
    set_code      TEXT NOT NULL UNIQUE,   -- "DMRP-22", "DM-01"
    set_name      TEXT,
    set_url       TEXT NOT NULL,
    series        TEXT,                   -- "OCG", "TCG", "Promo"
    scraped_at    TIMESTAMPTZ,
    card_count    INT DEFAULT 0
);

-- Card URLs discovered from each set page
CREATE TABLE IF NOT EXISTS card_urls (
    id            SERIAL PRIMARY KEY,
    url           TEXT NOT NULL UNIQUE,
    card_name     TEXT,
    set_code      TEXT REFERENCES card_sets(set_code),
    discovered_at TIMESTAMPTZ DEFAULT NOW(),
    scraped_at    TIMESTAMPTZ,
    parsed_at     TIMESTAMPTZ,
    status        TEXT DEFAULT 'pending'  -- pending|scraped|parsed|error
);

-- Core card data
CREATE TABLE IF NOT EXISTS cards (
    id            SERIAL PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    cost          INT,
    power         TEXT,
    card_type     TEXT,
    card_subtype  TEXT,                   -- "Evolution", "Neo Evolution" etc.
    flavor_text   TEXT,
    is_multiface  BOOLEAN DEFAULT FALSE,
    faces         JSONB,                  -- full per-face data for Twin Pact / Psychic cards
    raw_text      TEXT,
    source_url    TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE cards ADD COLUMN IF NOT EXISTS faces JSONB;

-- Card civilizations (Fire, Water, etc.) — many-to-many
CREATE TABLE IF NOT EXISTS card_civilizations (
    card_id       INT REFERENCES cards(id) ON DELETE CASCADE,
    civilization  TEXT NOT NULL,
    PRIMARY KEY (card_id, civilization)
);

-- Card races (Dragon, Human, etc.) — many-to-many
CREATE TABLE IF NOT EXISTS card_races (
    card_id       INT REFERENCES cards(id) ON DELETE CASCADE,
    race          TEXT NOT NULL,
    PRIMARY KEY (card_id, race)
);

-- Card printings (card × set × rarity × collector number)
CREATE TABLE IF NOT EXISTS card_printings (
    id            SERIAL PRIMARY KEY,
    card_id       INT REFERENCES cards(id) ON DELETE CASCADE,
    set_code      TEXT REFERENCES card_sets(set_code),
    collector_num TEXT,
    rarity        TEXT,
    mana_number   TEXT,
    image_url     TEXT,
    UNIQUE (card_id, set_code, collector_num)
);

ALTER TABLE card_printings ADD COLUMN IF NOT EXISTS mana_number TEXT;

-- LLM-parsed structured card effects (one row per ■ ability)
CREATE TABLE IF NOT EXISTS card_effects (
    id                SERIAL PRIMARY KEY,
    card_id           INT REFERENCES cards(id) ON DELETE CASCADE,
    face_index        INT,
    face_name         TEXT,
    ability_index     INT NOT NULL,            -- order of ■ on card
    raw_text          TEXT NOT NULL,           -- raw ■ bullet text
    effect_type       TEXT,                    -- triggered|activated|static|keyword|replacement|cost_mod|spell
    trigger_event     TEXT,                    -- on_enter_battle_zone|on_attack|...
    trigger_condition JSONB,
    effect_action     TEXT,                    -- draw|destroy|return_to_hand|...
    effect_target     JSONB,
    effect_value      JSONB,
    is_optional       BOOLEAN DEFAULT FALSE,
    is_replacement    BOOLEAN DEFAULT FALSE,
    active_in_phase   TEXT[],
    active_in_zone    TEXT[],
    parse_confidence  FLOAT,
    parsed_at         TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE card_effects ADD COLUMN IF NOT EXISTS face_index INT;
ALTER TABLE card_effects ADD COLUMN IF NOT EXISTS face_name TEXT;

-- Official rulings
CREATE TABLE IF NOT EXISTS card_rulings (
    id            SERIAL PRIMARY KEY,
    card_id       INT REFERENCES cards(id) ON DELETE CASCADE,
    ruling_text   TEXT NOT NULL,
    source        TEXT
);

-- Keyword links (card → dm_keywords in rules DB)
CREATE TABLE IF NOT EXISTS card_keywords (
    card_id       INT REFERENCES cards(id) ON DELETE CASCADE,
    keyword       TEXT NOT NULL,
    PRIMARY KEY (card_id, keyword)
);

-- Twin Pact / evolution / cross-gear relations
CREATE TABLE IF NOT EXISTS card_relations (
    card_id       INT REFERENCES cards(id) ON DELETE CASCADE,
    related_slug  TEXT NOT NULL,
    relation_type TEXT NOT NULL,            -- evolution_source|twin_pact|cross_gear
    PRIMARY KEY (card_id, related_slug, relation_type)
);

-- Training decks used by dm_engine self-play.
CREATE TABLE IF NOT EXISTS training_decks (
    id                SERIAL PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE,
    owner             TEXT,
    source            TEXT,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    hyperspatial      JSONB NOT NULL DEFAULT '{}'::jsonb,
    ultra_gr          JSONB NOT NULL DEFAULT '{}'::jsonb,
    start_battle_zone JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE training_decks ADD COLUMN IF NOT EXISTS owner TEXT;
ALTER TABLE training_decks ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE training_decks ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE training_decks ADD COLUMN IF NOT EXISTS hyperspatial JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE training_decks ADD COLUMN IF NOT EXISTS ultra_gr JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE training_decks ADD COLUMN IF NOT EXISTS start_battle_zone JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE training_decks ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE training_decks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE IF NOT EXISTS training_deck_cards (
    deck_id INT NOT NULL REFERENCES training_decks(id) ON DELETE CASCADE,
    card_id INT NOT NULL REFERENCES cards(id) ON DELETE RESTRICT,
    count   INT NOT NULL CHECK (count > 0),
    PRIMARY KEY (deck_id, card_id)
);

CREATE INDEX IF NOT EXISTS idx_training_decks_active ON training_decks(is_active);

-- ============================================================
-- View for game engine — full card with effects as JSON array
-- ============================================================
DROP VIEW IF EXISTS v_card_engine;

CREATE OR REPLACE VIEW v_card_engine AS
SELECT
    c.id,
    c.name,
    c.slug,
    c.cost,
    c.power,
    c.card_type,
    c.card_subtype,
    c.is_multiface,
    c.faces,
    ARRAY_AGG(DISTINCT cc.civilization) FILTER (WHERE cc.civilization IS NOT NULL) AS civilizations,
    ARRAY_AGG(DISTINCT cr.race)         FILTER (WHERE cr.race IS NOT NULL)         AS races,
    JSONB_AGG(
        DISTINCT JSONB_BUILD_OBJECT(
            'set_code',      cp.set_code,
            'collector_num', cp.collector_num,
            'rarity',        cp.rarity,
            'mana_number',   cp.mana_number,
            'image_url',     cp.image_url
        )
    ) FILTER (WHERE cp.id IS NOT NULL) AS printings,
    JSONB_AGG(
        JSONB_BUILD_OBJECT(
            'ability_index',     ce.ability_index,
            'face_index',        ce.face_index,
            'face_name',         ce.face_name,
            'raw_text',          ce.raw_text,
            'effect_type',       ce.effect_type,
            'trigger_event',     ce.trigger_event,
            'trigger_condition', ce.trigger_condition,
            'effect_action',     ce.effect_action,
            'effect_target',     ce.effect_target,
            'effect_value',      ce.effect_value,
            'is_optional',       ce.is_optional,
            'is_replacement',    ce.is_replacement,
            'active_in_phase',   ce.active_in_phase,
            'active_in_zone',    ce.active_in_zone,
            'parse_confidence',  ce.parse_confidence
        ) ORDER BY ce.ability_index
    ) FILTER (WHERE ce.id IS NOT NULL) AS effects
FROM cards c
LEFT JOIN card_civilizations cc ON cc.card_id = c.id
LEFT JOIN card_races cr         ON cr.card_id = c.id
LEFT JOIN card_printings cp     ON cp.card_id = c.id
LEFT JOIN card_effects ce       ON ce.card_id = c.id
GROUP BY c.id;
