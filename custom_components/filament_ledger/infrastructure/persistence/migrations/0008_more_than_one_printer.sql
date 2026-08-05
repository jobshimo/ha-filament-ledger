-- 0008 — more than one printer. See docs/02-domain-model.md §2.3, docs/05-ha-integration.md
-- §5.8 and docs/08-data-model.md §8.1, §8.4.
--
-- 0007 taught the schema to *represent* a second machine; this one is the change that makes
-- the gateway follow every machine it finds. Two facts the schema still stated in the
-- singular have to stop being singular with it, and neither of them is a tray: 0007 already
-- widened those.
--
-- **Nothing here is backfilled with a name that was never recorded**, which is docs/08 §8.4's
-- rule for a backfill and the one difference from 0007. There the placeholder was forced —
-- a location has to name a tray and a tray has to name a printer — and it was safe because a
-- single-printer history cannot be ambiguous. Here neither column is load-bearing in that
-- way, so each says exactly what the old rows say.

BEGIN;

-- §8.1 — which machine ran the job. **Nullable and left null for every existing row**: the
-- ledger did not record it, and a serial written in now would be this migration inventing
-- the answer to the very question the column exists to stop being guessed at.
--
-- The consequence is one line long and it is in `TrackPrintJob._running_job`: an ending is
-- correlated to a RUNNING row of its own machine, so a nameless row is correlated to by
-- nobody. Exactly one row can be both RUNNING and nameless — the print that was in progress
-- when this migration ran — and it is left as verbatim and as reclassifiable as every other
-- ending that never arrived. One duration, once, against never attributing one printer's
-- grams to another printer's spools.
ALTER TABLE print_job ADD COLUMN printer TEXT;

-- §8.1 — the direct feed belongs to a machine too. Each printer has exactly one, so with
-- several machines the old index — one spool on `EXTERNAL_SPOOL`, ledger-wide — refuses a
-- state the hardware can plainly be in: a reel on each machine's direct feed. That is the
-- same reading that widened `idx_spool_slot` in 0007, arriving one release later because
-- this is the release where a second machine is followed rather than merely representable.
--
-- The rows are named before the index is rebuilt, and `UNIDENTIFIED` is right here for the
-- reason it was right in 0007: at most one row can exist to be named — the old index saw to
-- that — so it belongs to the one printer this ledger has ever fed directly, whatever that
-- machine turns out to be called. `printer_adoption` replaces it on the same terms as every
-- other location, which is to say only when discovery leaves no room for doubt.
UPDATE spool
   SET location_printer = 'UNIDENTIFIED'
 WHERE location_kind = 'EXTERNAL_SPOOL' AND location_printer IS NULL;

-- 0003's exclusions survive verbatim, exactly as 0007 kept them: a discarded or deleted
-- spool occupies nothing, and dropping either clause would resurrect the ghost 0003 removed.
DROP INDEX idx_spool_external;
CREATE UNIQUE INDEX idx_spool_external
    ON spool(location_kind, location_printer)
    WHERE location_kind = 'EXTERNAL_SPOOL' AND discarded_at IS NULL AND deleted_at IS NULL;

INSERT INTO schema_version (version, applied_at) VALUES (8, datetime('now'));

COMMIT;
