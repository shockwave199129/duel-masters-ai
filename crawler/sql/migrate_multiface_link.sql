-- Migration: add other_face_id and fix is_multiface for Psychic / Dragheart cards
-- Run once against dm_database:
--   psql $DATABASE_URL -f crawler/sql/migrate_multiface_link.sql
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- ── Step 1: add other_face_id column ─────────────────────────────────────────
ALTER TABLE cards
  ADD COLUMN IF NOT EXISTS other_face_id INTEGER REFERENCES cards(id);

-- ── Step 2: set is_multiface = true for all Psychic / Dragheart cards ─────────
-- Covers:  Creature / Super Creature / Field (subtype Psychic)
--          Dragheart Weapon / Fortress / Creature
UPDATE cards
SET    is_multiface = TRUE
WHERE  card_subtype = 'Psychic'
   OR  card_type ILIKE 'Dragheart%'
   OR  card_type = 'Super Creature';

-- ── Step 3: link Dragheart face-pairs using sequential-ID heuristic ───────────
--
-- The scraper imports all faces of the same physical card in sequence.
-- For a 3-face Dragheart (Weapon → Fortress → Creature) that means IDs n, n+1, n+2.
-- For a 2-face Dragheart (Weapon → Creature, no Fortress):  IDs n, n+1.
--
-- Weapon ↔ Fortress  (exist together at consecutive IDs)
UPDATE cards AS w
SET    other_face_id = f.id
FROM   cards f
WHERE  w.card_type = 'Dragheart Weapon'
  AND  f.id        = w.id + 1
  AND  f.card_type = 'Dragheart Fortress';

UPDATE cards AS f
SET    other_face_id = w.id
FROM   cards w
WHERE  f.card_type = 'Dragheart Fortress'
  AND  w.id        = f.id - 1
  AND  w.card_type = 'Dragheart Weapon';

-- Fortress ↔ Creature (n+1 → n+2)
UPDATE cards AS f
SET    other_face_id = COALESCE(f.other_face_id, cr.id)
FROM   cards cr
WHERE  f.card_type = 'Dragheart Fortress'
  AND  cr.id       = f.id + 1
  AND  cr.card_type = 'Dragheart Creature';

UPDATE cards AS cr
SET    other_face_id = COALESCE(cr.other_face_id, f.id)
FROM   cards f
WHERE  cr.card_type = 'Dragheart Creature'
  AND  f.id         = cr.id - 1
  AND  f.card_type  = 'Dragheart Fortress';

-- Weapon ↔ Creature  (when there is NO Fortress; n → n+1 directly)
UPDATE cards AS w
SET    other_face_id = COALESCE(w.other_face_id, cr.id)
FROM   cards cr
WHERE  w.card_type  = 'Dragheart Weapon'
  AND  cr.id        = w.id + 1
  AND  cr.card_type = 'Dragheart Creature'
  AND  w.other_face_id IS NULL;

UPDATE cards AS cr
SET    other_face_id = COALESCE(cr.other_face_id, w.id)
FROM   cards w
WHERE  cr.card_type = 'Dragheart Creature'
  AND  w.id         = cr.id - 1
  AND  w.card_type  = 'Dragheart Weapon'
  AND  cr.other_face_id IS NULL;

-- ── Step 4: link Psychic Creature ↔ Awakened face ────────────────────────────
--
-- Psychic cards are scraped as SEPARATE wiki pages (different source_url) so
-- sequential IDs do NOT reliably connect them.  The card ability text says
-- "flip this creature to its higher cost side" without naming the paired card.
--
-- Best-effort heuristic: pair a lower-cost Psychic Creature (Awaken keyword in
-- its ability text) with the same-cost higher-face candidate found by checking
-- that one card's English text mentions "Awaken" and the other has matching
-- race / civilization and costs more.
--
-- This covers many straightforward pairs; edge-cases (Super Creatures with 3+
-- cells, Psychic Fields) still need manual rows.

UPDATE cards AS lo
SET    other_face_id = hi.id
FROM   cards hi
WHERE  lo.card_subtype = 'Psychic'
  AND  lo.card_type    = 'Creature'
  AND  lo.other_face_id IS NULL
  -- lower face has "Awaken" keyword in ability text
  AND  lo.faces IS NOT NULL
  AND  lo.faces::text ILIKE '%Awaken%'
  -- hi is the other Psychic card with the same civilizations and higher cost
  AND  hi.card_subtype  = 'Psychic'
  AND  hi.card_type     = 'Creature'
  AND  hi.id           <> lo.id
  AND  hi.cost          > lo.cost
  AND  hi.other_face_id IS NULL
  -- same civilization set (compare the jsonb arrays as text for a lightweight check)
  AND  (lo.faces->0->'civilizations')::text = (hi.faces->0->'civilizations')::text
  -- the hi card should NOT itself have the Awaken keyword (it is already awakened)
  AND  hi.faces::text NOT ILIKE '%Awaken—%';

-- Reverse the link
UPDATE cards AS hi
SET    other_face_id = lo.id
FROM   cards lo
WHERE  hi.card_subtype  = 'Psychic'
  AND  hi.other_face_id IS NULL
  AND  lo.other_face_id = hi.id;

-- ── Step 5: verification summary ─────────────────────────────────────────────
SELECT
  card_type,
  card_subtype,
  COUNT(*)                                                          AS total,
  COUNT(*) FILTER (WHERE is_multiface = TRUE)                      AS multiface_set,
  COUNT(*) FILTER (WHERE other_face_id IS NOT NULL)                AS face_linked
FROM cards
WHERE card_subtype = 'Psychic'
   OR card_type ILIKE 'Dragheart%'
   OR card_type = 'Super Creature'
GROUP BY card_type, card_subtype
ORDER BY card_type, card_subtype;

COMMIT;
