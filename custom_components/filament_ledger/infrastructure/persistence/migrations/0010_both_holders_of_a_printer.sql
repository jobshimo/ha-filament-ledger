-- 0010 — both holders of a printer. See docs/02-domain-model.md §2.2,
-- docs/05-ha-integration.md §5.8 and docs/08-data-model.md §8.1, §8.4.
--
-- 0008 gave the direct feed a machine, on the reading that each machine has exactly one of
-- them. That reading was true of the reference A1 and is false of the range: a dual-nozzle
-- printer (X2D/H2D/H2C) carries two holders, upstream models exactly two — `external_spool[0]`
-- and `external_spool[1]`, reported under the active-tray indexes 255 and 254 — and the live
-- X2D keys the second one's consumption as `External Spool 2` on its own weight sensor
-- (docs/12-field-notes.md, 2026-09-23). So the index that states *the direct feed of a machine
-- holds one spool* refuses a state the hardware is plainly in, exactly as the ledger-wide index
-- refused the second machine's reel before 0008.

BEGIN;

-- §8.1 — which of the machine's holders. Nullable, and set for exactly the rows whose kind is
-- EXTERNAL_SPOOL, which mirrors how `location_ams`/`location_slot` pair with AMS_SLOT: a column
-- that means nothing for a location is null rather than zero, so a row can never half-describe
-- a position.
ALTER TABLE spool ADD COLUMN location_holder INTEGER;

-- §8.4 — **the backfill is not a guess, and that is the one thing that makes it permissible.**
-- The old index was unique on (kind, printer), so a machine could hold at most one external row
-- at a time; every row that exists is therefore that machine's *first* holder, whichever nozzle
-- the user actually threaded it through. 0007's placeholder was forced by the same kind of
-- argument — at most one thing it could mean — and 0009's refusal to backfill `reel_uid` is the
-- other side of it: nothing is written in here that the old shape did not already imply.
UPDATE spool SET location_holder = 1 WHERE location_kind = 'EXTERNAL_SPOOL';

-- 0003's exclusions survive verbatim, exactly as 0008 kept them from 0007: a discarded or
-- deleted spool occupies nothing, and dropping either clause would resurrect the ghost 0003
-- removed. The invariant is not weakened — one spool per holder still holds — it is stated
-- about the position the hardware actually has.
DROP INDEX idx_spool_external;
CREATE UNIQUE INDEX idx_spool_external
    ON spool(location_kind, location_printer, location_holder)
    WHERE location_kind = 'EXTERNAL_SPOOL' AND discarded_at IS NULL AND deleted_at IS NULL;

-- **No JSON column is rewritten**, unlike 0007. A stored per-position entry names the direct
-- feed with `"external": true` and no holder, and `tray_json.tray_from` reads a missing holder
-- as the first — which is the same statement the UPDATE above makes, applied where a rewrite
-- would have had to make it row by row.

INSERT INTO schema_version (version, applied_at) VALUES (10, datetime('now'));

COMMIT;
