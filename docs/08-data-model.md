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
    location_printer  TEXT,                      -- non-null only for AMS_SLOT (0007)
    location_ams      INTEGER,                   -- non-null only for AMS_SLOT (0007)
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
    ON spool(location_printer, location_ams, location_slot)
    WHERE location_kind = 'AMS_SLOT' AND discarded_at IS NULL;

CREATE UNIQUE INDEX idx_spool_external
    ON spool(location_kind, location_printer)
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

The two unique indexes enforce the physical facts that a tray holds one spool and the direct
feed holds one spool. A cross-aggregate invariant that only lives in application code is one
race condition away from being violated.

**`idx_spool_external` covers the machine since migration 0008.** Each printer has exactly one
direct feed, so an index unique on `location_kind` alone stated *the direct feed holds one
spool ledger-wide* — which refused the second machine's reel to a ledger that could truthfully
hold it. `location_printer` carries the machine for both mounted kinds, which is what lets one
index per kind state *one spool per position* without either naming a column of its own.

**`idx_spool_slot` covers the whole tray reference since migration 0007**, not `location_slot`
alone. Two printers both have a tray 1, and an index over the number alone would have refused
the second machine's tray 1 to the ledger the moment the model could hold one — which is the
opposite of the invariant it exists to state. `location_printer` and `location_ams` are the
other two parts of `TrayRef` ([02 §2.3](02-domain-model.md)) and are set exactly when
`location_slot` is; SQLite's `ALTER TABLE` cannot carry a cross-column `CHECK`, so that
pairing is enforced in the domain, the same division migration 0003 made for `tag_source`.

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

**Neither index serves the global history, and none of its filters is covered by one.** That
is a reading of the schema rather than an oversight. `movements` orders the whole table by
`occurred_at DESC` and there is no index on that column alone, so the read has always been a
scan and a sort; the filters of [04 UC-12](04-use-cases.md) add tests to rows the query was
visiting anyway. A weight bound is an expression over `amount_mg`, a colour is a subquery over
the small `spool` table, and `LIKE '%…%'` can never use a B-tree at all. At a household
ledger's size — thousands of rows, not millions — that costs single-digit milliseconds. Should
it stop being true, the answer is an index on `occurred_at`, and free text is the last
predicate to reach for.

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
    reported_usage   TEXT,                       -- JSON [{printer, ams, slot, mg}]
    raw_gcode_state  TEXT,                       -- verbatim, see Q1
    raw_print_error  INTEGER,                    -- verbatim, see Q1
    consumption_recorded INTEGER NOT NULL DEFAULT 0,
    printer          TEXT                        -- which machine ran it (0008); null before it
);

CREATE INDEX idx_job_state ON print_job(state, started_at);
```

**`printer` is what an ending is correlated by, and NULL is not a machine.** An upstream
lifecycle event carries no job id, so an ending is matched to the newest `RUNNING` row of the
printer that reported it; matching on state alone was correct while one machine printed and
became a coin toss the moment two could ([02 §2.3](02-domain-model.md)). NULL is what every
row written before migration 0008 says, because the ledger did not record the answer — and a
row that names no machine is returned for none, which is how a migrated row is left alone
rather than claimed.

`consumption_recorded` is the idempotency guard for UC-04. A print must never be deducted
twice, and a duplicate ledger entry is indistinguishable from a real one after the fact.

```sql
CREATE TABLE pending_review (
    id               TEXT PRIMARY KEY,
    job_id           TEXT    NOT NULL REFERENCES print_job(id),
    reason           TEXT    NOT NULL,           -- CANCELLED|FAILED|UNCLASSIFIED|UNMAPPED_USAGE
    estimated_usage  TEXT    NOT NULL,           -- JSON [{printer, ams, slot, mg}]
    confirmed_usage  TEXT,                       -- JSON [{printer, ams, slot, mg}]
    slot_resolution  TEXT    NOT NULL,           -- JSON [{printer, ams, slot, spool_id, mg}]
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

`reported_usage`, `estimated_usage`, `confirmed_usage` and `slot_resolution` hold JSON rather
than child tables.

Justified because they are always read and written whole, never queried by key, and bounded by
the hardware — four trays, and in practice a spool or two per tray. A child table would add
joins and migrations for no query benefit.

All four are **lists of entries keyed by tray**, not by spool, because the printer reports one
figure per tray and can report nothing else — and because a tray with no spool mounted is
exactly the case a spool-keyed map could not represent at all
([02 §2.3](02-domain-model.md)).

**Each entry names the tray in full — `printer`, `ams`, `slot` — since migration 0007.** The
first three were maps keyed by the tray number, and a JSON key holds one value while a tray
takes three to identify it. A composite key would have needed a separator that can never
appear in a printer serial, and nobody can promise that about somebody else's hardware; a list
of objects has no such problem and stays readable in a database browser, which is where a
stored document is actually inspected. `slot_resolution` had already been a list since 0004,
so this is one shape rather than two.

`slot_resolution` is the attribution, and since migration 0004 it carries a `spool_id`. It was
a `{slot: spool_id|null}` map, which was the one limitation that mattered: a spool that empties
mid-print and is replaced in the same tray leaves that tray's single reported figure belonging
to two spools, and a map could only name one of them. A tray may now appear in the list more
than once, and a tray absent from it entirely is one that froze with no spool — which is what
`null` used to say.

Both rewrites are lossless in both directions, which is why they could be data migrations
rather than new columns. See §8.4.

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

`migrations/0001_initial.sql` is the schema above. Everything later lands with the feature
that motivated it and is specified there rather than duplicated here.

**A fact that stops being derivable gets a column.** The counterpart to the second rule,
and the reason `migrations/0005_void_remembers_the_un_discard.sql` exists: whether a void
had brought its spool back out of `DISCARDED` was read off the history — a whole-spool
discard is the entry nothing follows — and the void appends its own reversal, so by the time
the restore asks, the answer has been overwritten by the question. Deriving a fact twice is
only safe while the first derivation cannot change what the second one reads
([14 §14.4.1-2](14-corrections-and-trash.md)).

A backfill states what the old rows *say*, never what would be convenient. Provenance that
was never recorded backfills to the honest floor (0003's `tag_source` → `MANUAL`); a fact
that cannot be recovered at all backfills to the reading whose mistake is the cheaper one
(0005's `undiscarded_spool` → no, because the opposite would invent waste).

`0004_review_charges.sql` rewrites `slot_resolution` from a map to a list of charges (§8.2).
It is the one migration so far that reinterprets data rather than adding to it, and it is
allowed because the reinterpretation is **lossless and mechanical**: each `{slot: spool_id}`
entry becomes exactly one charge for that slot carrying the amount that slot was charged —
the confirmed figure where a decision recorded one, the estimate otherwise — and each `null`
becomes no charge, which is already what an unresolved tray meant. There is nothing to
interpret and nothing to lose, so no review says anything different afterwards than it said
before. That claim is not asserted but tested: `tests/application/test_migrations.py`
populates a database in the old shape with literal SQL, migrates it with the real runner, and
compares what the current mapper reads back against a second, independent reading of the old
columns — estimate per tray, confirmed amount per tray, the one spool each tray resolved to,
and the charges an approval turned into movements. The movement table is untouched, so the
rule above holds where it matters most.

`0007_a_tray_knows_its_printer.sql` is the second such reinterpretation, and it is lossless
for a reason that has nothing to do with cleverness: **every existing row belongs to the one
printer this ledger has ever talked to**, so each becomes `(that serial, AMS 1, its slot)`. A
single-printer history cannot be ambiguous. AMS 1 because that is the only AMS this ledger has
ever followed and the only ordinal the printer has ever reported for it
([12](12-field-notes.md)) — the gateway dropped every other ordinal with a warning long before
this migration existed.

**The serial comes from nowhere, and that is the honest answer.** A migration runs inside
`Database.migrate()` with a bare SQLite connection: no Home Assistant, no gateway, nothing to
ask. So every row takes the reserved `UNIDENTIFIED` serial, and the composition root replaces
it once discovery has resolved a real one — `printer_adoption.adopt_unidentified_trays`, run
after `migrate()` and *before* the startup reconciliation pass. The order is load-bearing: a
pass that ran first would look tray 3 up under the newly discovered serial, find it free, and
mount a second spool into it, with the widened index correctly seeing two different trays and
looking away.

**A ledger whose printer is never seen again keeps the placeholder for ever, and nothing
breaks.** Uniqueness holds under it exactly as under a real serial — every row shares one
printer, which is true — the inventory reads the same, and a mount lands in the same tray
space, because a caller that names no printer is answered with the tray space the ledger is
actually using rather than with a bare sentinel. The only thing that never happens is the rows
learning a name.

Adoption rewrites `spool` and nothing else. A job's `reported_usage` and a review's frozen
lines are records of what was said at the time, and this ledger does not rewrite what it
recorded ([ADR-0001](adr/0001-append-only-ledger.md)); nothing resolves a spool through those
trays, so the placeholder there is both harmless and true — nobody knew the name when the row
was written. The location is the one place the name is *used* rather than remembered.

`0008_more_than_one_printer.sql` is the release that follows every machine rather than one,
and it changes two facts the schema still stated in the singular: which machine ran a job, and
which machine a direct feed belongs to (§8.1). **Neither is backfilled with a name the ledger
never recorded**, which is the one difference from 0007 and the whole of what makes it honest.
There the placeholder was forced — a location has to name a tray and a tray has to name a
printer — and it was safe because a single-printer history cannot be ambiguous. Here
`print_job.printer` is simply left NULL, which is what the old rows say; the one row the old
external index allowed takes `UNIDENTIFIED` on 0007's own terms, because there can be at most
one and it belongs to the one printer this ledger has ever fed directly.

**Adoption adopts only when discovery names exactly one machine, and there is no third option
that is honest.** The placeholder means *the one machine this ledger has always talked to*.
With one machine discovered, that machine is it. With several, the ledger has no record of
which — the config entry stores no serial, the rows store only the placeholder, and a device id
is a random identifier rather than a name a printer answers to — so every rule that would pick
one is a coin toss dressed as a heuristic, and losing it puts somebody's spools on a printer
they are not in. The rows keep the placeholder, the AMS view shows them as their own section
under a heading saying the machine was never named, and the owner moves each spool onto the
right machine with the mount control they already have ([06 §6.4](06-ui-spec.md)).

That case is narrower than it sounds. Adoption has run on every start since v1.4, so a ledger
that ever saw one nameable printer already carries its serial and passes through doing nothing.
What is left is the ledger that never resolved a printer at all until the day two appeared at
once — and for that ledger the honest answer really is *I do not know which*, said where it can
be read rather than guessed at where it cannot.

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
