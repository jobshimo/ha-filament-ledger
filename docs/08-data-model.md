# 08 — Data Model

SQLite, stored in the Home Assistant config directory as `filament_ledger.db`.

Not the HA `Store` helper (a JSON blob rewritten in full on every change): the ledger grows
without bound and is queried by range, which is a relational workload. Rewriting the entire
history to append one movement would also make a partial write capable of destroying
everything before it.

---

## 8.1 Schema

```sql
CREATE TABLE spool (
    id                TEXT PRIMARY KEY,
    material          TEXT    NOT NULL,
    material_other    TEXT,
    colour            TEXT    NOT NULL,          -- RRGGBBAA
    vendor            TEXT,
    label             TEXT,
    opening_weight_mg INTEGER NOT NULL CHECK (opening_weight_mg > 0),
    core_weight_mg    INTEGER NOT NULL CHECK (core_weight_mg >= 0),
    location_kind     TEXT    NOT NULL,          -- STORAGE|AMS_SLOT|EXTERNAL_SPOOL
    location_slot     INTEGER,                   -- non-null only for AMS_SLOT
    tag_uid           TEXT,
    registered_at     TEXT    NOT NULL,
    discarded_at      TEXT,                      -- the only stored part of SpoolState
    updated_at        TEXT    NOT NULL,

    CHECK ((location_kind = 'AMS_SLOT') = (location_slot IS NOT NULL))
);

CREATE INDEX idx_spool_tag       ON spool(tag_uid) WHERE tag_uid IS NOT NULL;
CREATE INDEX idx_spool_discarded ON spool(discarded_at);

CREATE UNIQUE INDEX idx_spool_slot
    ON spool(location_slot)
    WHERE location_kind = 'AMS_SLOT' AND discarded_at IS NULL;

CREATE UNIQUE INDEX idx_spool_external
    ON spool(location_kind)
    WHERE location_kind = 'EXTERNAL_SPOOL' AND discarded_at IS NULL;
```

**There is no `state` column.** `SpoolState` is derived from `discarded_at` and the balance
([02 §2.2](02-domain-model.md)). Storing it would put a value that is *computed from the
ledger* next to the ledger, where the two can disagree and nothing detects it — the exact
arrangement [ADR-0001](adr/0001-append-only-ledger.md) exists to reject. Applying that ADR to
the balance but not to the state would have been half a decision.

**`core_weight_mg` has no default.** An omitted core weight is a bug in the caller, and the
column says so by refusing the insert. The friendly per-vendor default lives in the config
flow and is applied by the service layer ([05 §5.4](05-ha-integration.md)), in one place. A
`DEFAULT 0` here would turn that bug into a silent 250 g error inside every reconciliation —
see [02 §2.8](02-domain-model.md).

The two unique indexes enforce the physical facts that a slot holds one spool and the direct
feed holds one spool. A cross-aggregate invariant that only lives in application code is one
race condition away from being violated.

```sql
CREATE TABLE movement (
    id           TEXT PRIMARY KEY,
    spool_id     TEXT    NOT NULL REFERENCES spool(id),
    type         TEXT    NOT NULL,
    amount_mg    INTEGER NOT NULL CHECK (amount_mg != 0),
    source       TEXT    NOT NULL,               -- AUTOMATIC|USER_CONFIRMED
    occurred_at  TEXT    NOT NULL,
    recorded_at  TEXT    NOT NULL,
    job_id       TEXT             REFERENCES print_job(id),
    review_id    TEXT             REFERENCES pending_review(id),
    note         TEXT
);

CREATE INDEX idx_movement_spool ON movement(spool_id, occurred_at);
CREATE INDEX idx_movement_job   ON movement(job_id) WHERE job_id IS NOT NULL;
```

**Amounts are integer milligrams.** Floating point across thousands of accumulated movements
drifts, and a ledger that drifts is a ledger nobody trusts. Integers make addition exact.

`occurred_at` and `recorded_at` are separate: a print may have finished while HA was down. The
event time and the observation time are different facts, and collapsing them loses one.

### Immutability, enforced by the database

```sql
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
```

The repository interface already omits update and delete ([02 §2.7](02-domain-model.md)). The
triggers make it true at the last possible layer as well. Two independent enforcements of the
system's central invariant is proportionate: if this one rule fails, every number the product
reports becomes untrustworthy.

```sql
CREATE TABLE print_job (
    id               TEXT PRIMARY KEY,
    name             TEXT    NOT NULL,
    state            TEXT    NOT NULL,           -- RUNNING|FINISHED|CANCELLED|FAILED
    started_at       TEXT    NOT NULL,
    ended_at         TEXT,
    layer_reached    INTEGER,
    total_layers     INTEGER,
    progress_pct     REAL,
    reported_usage   TEXT,                       -- JSON {slot: mg}
    raw_gcode_state  TEXT,                       -- verbatim, see Q1
    raw_print_error  INTEGER,                    -- verbatim, see Q1
    consumption_recorded INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_job_state ON print_job(state, started_at);
```

`consumption_recorded` is the idempotency guard for UC-04. A print must never be deducted
twice, and a duplicate ledger entry is indistinguishable from a real one after the fact.

```sql
CREATE TABLE pending_review (
    id               TEXT PRIMARY KEY,
    job_id           TEXT    NOT NULL REFERENCES print_job(id),
    reason           TEXT    NOT NULL,           -- CANCELLED|FAILED|UNCLASSIFIED|UNMAPPED_USAGE
    estimated_usage  TEXT    NOT NULL,           -- JSON {slot: mg}
    confirmed_usage  TEXT,                       -- JSON {slot: mg}
    slot_resolution  TEXT    NOT NULL,           -- JSON {slot: spool_id|null}, frozen at open
    estimator_used   TEXT    NOT NULL,           -- GCODE_LAYER|LINEAR_PROGRESS|NONE
    state            TEXT    NOT NULL,           -- PENDING|APPROVED|DISMISSED
    opened_at        TEXT    NOT NULL,
    resolved_at      TEXT,
    resolution_note  TEXT
);

CREATE UNIQUE INDEX idx_review_job_pending
    ON pending_review(job_id) WHERE state = 'PENDING';
CREATE INDEX idx_review_state ON pending_review(state, opened_at);
```

The partial unique index means one job cannot accumulate two open reviews — the database
enforcing what UC-05 intends.

```sql
CREATE TABLE schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
```

---

## 8.2 JSON columns

`reported_usage`, `estimated_usage`, `confirmed_usage` and `slot_resolution` hold JSON maps
rather than child tables.

Justified because they are always read and written whole, never queried by key, and bounded at
four entries by the hardware. A child table would add joins and migrations for no query
benefit.

All four are keyed by **slot index**, not by spool. `slot_resolution` carries the slot→spool
mapping frozen when the review opened, with `null` for a slot that had no spool mounted —
which is the case a spool-keyed map could not represent at all
([02 §2.3](02-domain-model.md)).

**This is a deliberate exception, not a licence.** Anything queried, aggregated, or filtered
gets a real column. Movements — the data that is actually queried by range and summed — are
fully relational.

---

## 8.3 Balance computation

```sql
SELECT COALESCE(SUM(amount_mg), 0) FROM movement WHERE spool_id = ?;
```

The opening balance is itself a movement, so the balance is a plain sum with no special
cases — nothing to keep in sync, nothing that can disagree.

Cached in memory per spool, invalidated on append. If a spool ever accumulates enough
movements for the sum to become slow on a Raspberry Pi, the remedy is a periodic snapshot row.
**Not built now** — building it before the problem exists is speculative complexity, and the
schema accommodates it without a breaking change.

---

## 8.4 Migrations

Sequential numbered SQL files applied in a transaction, with the version recorded on success.

Rules:

- **Never destructive.** Columns are added, not removed or repurposed.
- **Never rewrite movement rows.** If an interpretation changes, add a column and derive.
- **Forward-only.** No down-migrations; a restored backup is the rollback path, and a
  half-applied reversal is worse than no reversal.

`migrations/0001_initial.sql` is the schema above.

---

## 8.5 Retention

**Nothing is ever deleted.** Not discarded spools, not resolved reviews, not old jobs.

The ledger is small — a heavy user might produce a few thousand movements a year, which is
kilobytes. There is no storage argument for deletion, and deleting history would break the
one guarantee the product makes.

Discarded spools and resolved reviews are filtered out of default views ([06](06-ui-spec.md)),
which is a presentation concern. Hidden is not deleted.

---

## 8.6 Backup

The database is a single file inside the HA config directory, so it is included in standard HA
backups with no extra work.

A JSON export of the full ledger is a Phase 5 feature, for portability rather than backup —
and it is also the natural place for the optional Spoolman export
([ADR-0002](adr/0002-reject-spoolman-as-foundation.md)) to plug in.
