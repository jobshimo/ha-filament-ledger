-- 0005 — the void row remembers that it un-discarded its spool.
-- See docs/14-corrections-and-trash.md §14.4.1-2 and docs/08-data-model.md §8.4.
--
-- Voiding a whole-spool DISCARD clears discarded_at in the same transaction, because the
-- restitution returns the entire balance and leaving the spool DISCARDED would strand
-- those grams outside inventory. Restoring that void takes the grams straight back out,
-- so it has to redo the discard — and it could not tell that it should. The whole-spool
-- discriminator reads the history: a DISCARD with nothing after it. The void appends its
-- own reversal, so by the time anything restores it the discard is no longer last and the
-- derivation answers no. Nothing stored which kind of discard a void had undone. Now
-- something does.
--
-- Same shape as 0001-0003: one self-contained transaction whose last statement records
-- the version — either all of it lands, or none of it does. Additive, like 0003: a
-- nullable-in-effect flag on a status record, and not one statement touches `movement`.

BEGIN;

-- §14.4.1 — which kind of discard this void undid, stored because it stopped being
-- derivable. Existing rows take the default and say **no**, and that is a decision rather
-- than a convenience: the fact was never recorded, and it cannot be recovered from the
-- data either — a partial DISCARD voided on a live spool leaves exactly the same traces
-- as a whole-spool one, so any reconstruction would claim un-discards that never
-- happened. The two mistakes are not symmetric. A wrong `0` on an open chapter makes its
-- restore behave the way every restore behaved before this migration, which is the
-- behaviour that install already has; a wrong `1` would throw away a spool nobody threw
-- away, inventing a real-world event in the waste figures. Under-claim.
ALTER TABLE movement_void ADD COLUMN undiscarded_spool INTEGER NOT NULL DEFAULT 0
    -- A void that returned nothing returned no balance to strand, so it had no reason to
    -- bring a spool back and no way to: the un-discard *is* the restitution's other half.
    -- SQLite validates a new column's CHECK against the rows already there, so this
    -- clause also proves the backfill above is consistent rather than merely asserting it.
    CHECK (undiscarded_spool = 0 OR reversal_movement_id IS NOT NULL);

INSERT INTO schema_version (version, applied_at) VALUES (5, datetime('now'));

COMMIT;
