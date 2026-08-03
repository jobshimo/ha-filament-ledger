-- 0003 — corrections, provenance, and the trash. See docs/14-corrections-and-trash.md
-- and docs/adr/0007-corrections-are-more-history.md.
--
-- Everything here is additive. The movement table gains nullable link columns written
-- only at INSERT time, so the immutability triggers (0001) never fire and are not
-- touched. Same shape as 0001 and 0002: one self-contained transaction whose last
-- statement records the version — either all of it lands, or none of it does.

BEGIN;

-- §14.2 — tag provenance. Existing tags backfill as MANUAL: provenance was never
-- recorded, and claiming DETECTED would be invented history. MANUAL is the honest
-- floor — it over-grants edit rights once rather than storing a lie forever.
ALTER TABLE spool ADD COLUMN tag_source TEXT
    CHECK (tag_source IN ('MANUAL', 'DETECTED'));

UPDATE spool SET tag_source = 'MANUAL' WHERE tag_uid IS NOT NULL;

-- §14.4.3 — a spool registered by mistake. Distinct from discarded_at on purpose:
-- DISCARDED is a real-world event that counts as waste; DELETED is a bookkeeping
-- retraction that counts as nothing, anywhere.
ALTER TABLE spool ADD COLUMN deleted_at TEXT;

-- The one-spool-per-slot and one-external-spool invariants ignore deleted spools the
-- same way they already ignore discarded ones. Recreating an index is not destructive;
-- the data is untouched.
DROP INDEX idx_spool_slot;
CREATE UNIQUE INDEX idx_spool_slot
    ON spool(location_slot)
    WHERE location_kind = 'AMS_SLOT' AND discarded_at IS NULL AND deleted_at IS NULL;

DROP INDEX idx_spool_external;
CREATE UNIQUE INDEX idx_spool_external
    ON spool(location_kind)
    WHERE location_kind = 'EXTERNAL_SPOOL' AND discarded_at IS NULL AND deleted_at IS NULL;

-- §14.3 / §14.4 — correction provenance, on the movement itself. Nullable, INSERT-only.
ALTER TABLE movement ADD COLUMN reassigns_movement_id  TEXT REFERENCES movement(id);
ALTER TABLE movement ADD COLUMN reinstates_movement_id TEXT REFERENCES movement(id);

-- §14.4.1 — the void record. One row per voided movement, ever: chains re-void the
-- reinstatement, never the original, so the primary key holds by design.
-- reversal_movement_id NULL means voided without restitution — the spool was already
-- out of inventory, there was nothing to return to, and reason says so.
-- The two reinstatement columns are the only post-insert writes in this design:
-- movement_void is a status record, not a ledger. The movements it points at stay
-- immutable.
CREATE TABLE movement_void (
    movement_id               TEXT PRIMARY KEY REFERENCES movement(id),
    voided_at                 TEXT NOT NULL,
    reason                    TEXT,
    reversal_movement_id      TEXT REFERENCES movement(id),
    reinstated_at             TEXT,
    reinstatement_movement_id TEXT REFERENCES movement(id),

    -- A chapter is closed by both facts together or neither.
    CHECK ((reinstated_at IS NULL) = (reinstatement_movement_id IS NULL)),
    -- A void without restitution returned nothing, so there is nothing to deduct
    -- again: it can never be reinstated.
    CHECK (reinstatement_movement_id IS NULL OR reversal_movement_id IS NOT NULL)
);

CREATE INDEX idx_void_open
    ON movement_void(movement_id)
    WHERE reinstatement_movement_id IS NULL;

INSERT INTO schema_version (version, applied_at) VALUES (3, datetime('now'));

COMMIT;
