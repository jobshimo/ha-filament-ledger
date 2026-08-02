-- 0001 — initial schema. See docs/08-data-model.md.
--
-- All four tables land in the first migration even though Phase 1 only writes to two of
-- them. print_job and pending_review are referenced by movement's foreign keys, and
-- reserving them now means the Phase 2 answer to Q2 does not require a schema change.
--
-- The whole migration is one transaction, and the last statement inside it records the
-- version. Either everything lands — tables, triggers, and the schema_version row — or
-- nothing does: a crash mid-migration must not leave half a schema behind with no version
-- recorded, which would make every subsequent start fail on "table already exists".

BEGIN;

CREATE TABLE spool (
    id                TEXT PRIMARY KEY,
    material          TEXT    NOT NULL,
    material_other    TEXT,
    colour            TEXT    NOT NULL,          -- RRGGBBAA
    vendor            TEXT,
    label             TEXT,
    opening_weight_mg INTEGER NOT NULL CHECK (opening_weight_mg > 0),
    -- No DEFAULT. An omitted core weight is a bug in the caller, and the column says so by
    -- refusing the insert rather than quietly writing a 250 g error into every future
    -- reconciliation.
    core_weight_mg    INTEGER NOT NULL CHECK (core_weight_mg >= 0),
    location_kind     TEXT    NOT NULL,          -- STORAGE|AMS_SLOT|EXTERNAL_SPOOL
    location_slot     INTEGER,                   -- non-null only for AMS_SLOT
    tag_uid           TEXT,
    registered_at     TEXT    NOT NULL,
    -- The only stored part of SpoolState. The rest is derived from the ledger.
    discarded_at      TEXT,
    updated_at        TEXT    NOT NULL,

    CHECK ((location_kind = 'AMS_SLOT') = (location_slot IS NOT NULL))
);

CREATE INDEX idx_spool_tag       ON spool(tag_uid) WHERE tag_uid IS NOT NULL;
CREATE INDEX idx_spool_discarded ON spool(discarded_at);

-- One spool per AMS slot, and one spool on the direct feed. A cross-aggregate invariant
-- that only lives in application code is one race condition away from being violated.
CREATE UNIQUE INDEX idx_spool_slot
    ON spool(location_slot)
    WHERE location_kind = 'AMS_SLOT' AND discarded_at IS NULL;

CREATE UNIQUE INDEX idx_spool_external
    ON spool(location_kind)
    WHERE location_kind = 'EXTERNAL_SPOOL' AND discarded_at IS NULL;

CREATE TABLE print_job (
    id                   TEXT PRIMARY KEY,
    name                 TEXT    NOT NULL,
    state                TEXT    NOT NULL,       -- RUNNING|FINISHED|CANCELLED|FAILED
    started_at           TEXT    NOT NULL,
    ended_at             TEXT,
    layer_reached        INTEGER,
    total_layers         INTEGER,
    progress_pct         REAL,
    reported_usage       TEXT,                   -- JSON {slot: mg}
    raw_gcode_state      TEXT,                   -- verbatim; upstream can be wrong
    raw_print_error      INTEGER,                -- verbatim; upstream can be wrong
    consumption_recorded INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_job_state ON print_job(state, started_at);

CREATE TABLE pending_review (
    id               TEXT PRIMARY KEY,
    job_id           TEXT    NOT NULL REFERENCES print_job(id),
    reason           TEXT    NOT NULL,           -- CANCELLED|FAILED|UNCLASSIFIED|UNMAPPED_USAGE
    estimated_usage  TEXT    NOT NULL,           -- JSON {slot: mg}
    confirmed_usage  TEXT,                       -- JSON {slot: mg}
    slot_resolution  TEXT    NOT NULL,           -- JSON {slot: spool_id|null}, frozen at open
    estimator_used   TEXT    NOT NULL,
    state            TEXT    NOT NULL,           -- PENDING|APPROVED|DISMISSED
    opened_at        TEXT    NOT NULL,
    resolved_at      TEXT,
    resolution_note  TEXT
);

CREATE UNIQUE INDEX idx_review_job_pending
    ON pending_review(job_id) WHERE state = 'PENDING';
CREATE INDEX idx_review_state ON pending_review(state, opened_at);

CREATE TABLE movement (
    id           TEXT PRIMARY KEY,
    spool_id     TEXT    NOT NULL REFERENCES spool(id),
    type         TEXT    NOT NULL,
    -- Integer milligrams. Floating point across thousands of accumulated movements drifts,
    -- and a ledger that drifts is a ledger nobody trusts.
    amount_mg    INTEGER NOT NULL CHECK (amount_mg != 0),
    source       TEXT    NOT NULL,               -- AUTOMATIC|USER_CONFIRMED
    -- Separate facts: a print may have finished while Home Assistant was down.
    occurred_at  TEXT    NOT NULL,
    recorded_at  TEXT    NOT NULL,
    job_id       TEXT             REFERENCES print_job(id),
    review_id    TEXT             REFERENCES pending_review(id),
    note         TEXT
);

CREATE INDEX idx_movement_spool ON movement(spool_id, occurred_at);
CREATE INDEX idx_movement_job   ON movement(job_id) WHERE job_id IS NOT NULL;

-- The repository interface already omits update and delete. These make it true at the last
-- possible layer as well. Two independent enforcements of the system's central invariant is
-- proportionate: if this one rule fails, every number the product reports becomes
-- untrustworthy.
CREATE TRIGGER movement_no_update
BEFORE UPDATE ON movement
BEGIN
    SELECT RAISE(ABORT, 'movements are immutable');
END;

CREATE TRIGGER movement_no_delete
BEFORE DELETE ON movement
BEGIN
    SELECT RAISE(ABORT, 'movements cannot be deleted');
END;

INSERT INTO schema_version (version, applied_at) VALUES (1, datetime('now'));

COMMIT;
