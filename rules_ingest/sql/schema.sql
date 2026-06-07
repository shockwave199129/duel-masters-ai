-- ============================================================
-- Duel Masters Rules Database Schema
-- ============================================================

-- ── 1. RAW RULES ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dm_chapters (
    id           SERIAL PRIMARY KEY,
    number       INT     NOT NULL UNIQUE,   -- 0,1,2...8
    title        TEXT    NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dm_sections (
    id           SERIAL PRIMARY KEY,
    chapter_id   INT     NOT NULL REFERENCES dm_chapters(id) ON DELETE CASCADE,
    number       INT     NOT NULL,          -- 100, 101, 102 ...
    title        TEXT    NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (chapter_id, number)
);

CREATE TABLE IF NOT EXISTS dm_rules (
    id              SERIAL PRIMARY KEY,
    section_id      INT     NOT NULL REFERENCES dm_sections(id) ON DELETE CASCADE,
    rule_number     TEXT    NOT NULL UNIQUE,   -- "101.2", "101.2a"
    parent_rule     TEXT,                      -- "101.2" if this is "101.2a"
    depth           INT     NOT NULL DEFAULT 0,-- 0=top, 1=sub(a/b/c), 2=deeper
    text            TEXT    NOT NULL,

    -- ── game-engine tags ──────────────────────────────────────
    rule_category      TEXT,          -- see ENUM comment below
    applies_in_phase   TEXT[],        -- {"main","attack","any"}
    applies_in_zone    TEXT[],        -- {"battle_zone","hand","any"}
    is_state_based     BOOLEAN NOT NULL DEFAULT FALSE,
    is_turn_based      BOOLEAN NOT NULL DEFAULT FALSE,
    is_keyword_rule    BOOLEAN NOT NULL DEFAULT FALSE,
    priority           INT     NOT NULL DEFAULT 100,

    created_at  TIMESTAMPTZ DEFAULT NOW()
);
/*
  rule_category values:
    'win_loss'       – checked constantly (104, 703.4a/b)
    'turn_structure' – procedural step rules (500-512)
    'cost_payment'   – cost / mana rules (112, 601)
    'trigger'        – triggered ability rules (603)
    'replacement'    – replacement effect rules (609)
    'keyword'        – keyword definitions (701)
    'zone_rule'      – zone transition rules (400, 603.5)
    'special_card'   – special card type rules (800-822)
    'state_based'    – state-based actions (703)
    'general'        – catch-all
*/

CREATE INDEX IF NOT EXISTS idx_rules_number   ON dm_rules(rule_number);
CREATE INDEX IF NOT EXISTS idx_rules_section  ON dm_rules(section_id);
CREATE INDEX IF NOT EXISTS idx_rules_category ON dm_rules(rule_category);
CREATE INDEX IF NOT EXISTS idx_rules_sba      ON dm_rules(is_state_based);


-- ── 2. KEYWORD ABILITIES ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS dm_keywords (
    id              SERIAL PRIMARY KEY,
    name            TEXT    NOT NULL UNIQUE,  -- "Speed Attacker", "Blocker" …
    short_desc      TEXT,                     -- one-liner shown in game UI
    full_rule_ref   TEXT REFERENCES dm_rules(rule_number),

    -- behaviour flags (drive game engine decisions)
    overrides_summoning_sickness BOOLEAN NOT NULL DEFAULT FALSE,
    is_triggered                 BOOLEAN NOT NULL DEFAULT FALSE,
    is_activated                 BOOLEAN NOT NULL DEFAULT FALSE,
    is_static                    BOOLEAN NOT NULL DEFAULT FALSE,
    is_replacement               BOOLEAN NOT NULL DEFAULT FALSE,
    requires_declaration         BOOLEAN NOT NULL DEFAULT FALSE,
    usable_in_phase              TEXT[],      -- e.g. {"attack"} for Ninja Strike

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- links keywords to the cards that have them (join with your card table)
CREATE TABLE IF NOT EXISTS dm_card_keywords (
    card_id      INT  NOT NULL,              -- FK to your existing cards table
    keyword_id   INT  NOT NULL REFERENCES dm_keywords(id) ON DELETE CASCADE,
    parameters   JSONB,                      -- {"mana_count":5} for Ninja Strike
    PRIMARY KEY (card_id, keyword_id)
);


-- ── 3. GAME PHASES ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dm_game_phases (
    id           SERIAL PRIMARY KEY,
    phase_key    TEXT    NOT NULL UNIQUE,  -- "turn_start", "draw", etc.
    phase_name   TEXT    NOT NULL,         -- human label
    phase_order  INT     NOT NULL,         -- 1-6 main; sub-steps use decimals
    is_optional  BOOLEAN NOT NULL DEFAULT FALSE,
    can_repeat   BOOLEAN NOT NULL DEFAULT FALSE,   -- attack sub-steps repeat
    rule_ref     TEXT,                             -- e.g. "501"
    description  TEXT
);

CREATE TABLE IF NOT EXISTS dm_phase_actions (
    id            SERIAL PRIMARY KEY,
    phase_id      INT  NOT NULL REFERENCES dm_game_phases(id) ON DELETE CASCADE,
    action_order  INT  NOT NULL,
    actor         TEXT NOT NULL,    -- "turn_player" | "non_turn_player" | "game"
    action_type   TEXT NOT NULL,    -- "mandatory" | "optional" | "conditional"
    action_key    TEXT NOT NULL,    -- "untap_all", "draw_card" …
    condition     TEXT,             -- plain-English condition or NULL
    rule_ref      TEXT,             -- e.g. "501.1"
    description   TEXT
);


-- ── 4. STATE-BASED ACTIONS ────────────────────────────────────

CREATE TABLE IF NOT EXISTS dm_state_based_actions (
    id               SERIAL PRIMARY KEY,
    rule_number      TEXT NOT NULL REFERENCES dm_rules(rule_number),
    action_key       TEXT NOT NULL UNIQUE,
    description      TEXT NOT NULL,
    condition_json   JSONB NOT NULL,   -- structured condition for the engine
    effect_json      JSONB NOT NULL,   -- what the engine does when met
    priority         INT  NOT NULL DEFAULT 100  -- lower = checked first
);


-- ── 5. RULE RELATIONS ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dm_rule_relations (
    rule_from   TEXT NOT NULL REFERENCES dm_rules(rule_number) ON DELETE CASCADE,
    rule_to     TEXT NOT NULL REFERENCES dm_rules(rule_number) ON DELETE CASCADE,
    relation    TEXT NOT NULL,   -- "exception_to" | "overrides" | "see_also"
    notes       TEXT,
    PRIMARY KEY (rule_from, rule_to, relation)
);


-- ── 6. CARD ↔ RULE LINKS ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS dm_card_rule_links (
    id           SERIAL PRIMARY KEY,
    card_id      INT  NOT NULL,           -- FK to your existing cards table
    rule_number  TEXT NOT NULL REFERENCES dm_rules(rule_number) ON DELETE CASCADE,
    link_type    TEXT NOT NULL,           -- "governed_by"|"exception_to"|"overrides"
    notes        TEXT
);
