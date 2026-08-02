# 02 — Domain Model

This layer contains the business rules and nothing else. It has **zero imports from
`homeassistant`**, no database access, no I/O, and no knowledge of how it is presented.

That constraint is not stylistic. It is what makes every rule in this document testable in
milliseconds without booting Home Assistant, and it is what keeps a Home Assistant API change
from breaking the accounting logic.

---

## 2.1 The central rule

> **A balance is never stored. It is derived from an immutable sequence of movements.**

```
balance(spool) = opening_weight(spool) − Σ signed_amount(movements of spool)
```

Every other decision in this document follows from that one. A stored balance can be
corrupted by a bad write and there is no way to detect it. A derived balance can always be
recomputed from its inputs, and every gram can be traced to the event that removed it.

Corrections are therefore **new movements**, never edits. The ledger has no eraser — for the
same reason accountants do not use one.

---

## 2.2 Value Objects

Value objects are immutable, compared by value, and validate themselves on construction. An
invalid value object cannot exist.

### `Grams`

Wraps a quantity of filament mass.

- Stored internally as an integer number of **milligrams** to avoid floating-point drift
  across thousands of accumulated movements.
- Supports addition, subtraction, comparison, and scaling by a ratio.
- **Cannot be added to a bare number.** Type safety here is the entire point: a `float`
  lets you add grams to a percentage and find out in production.

```
Grams.of(24.5)        → Grams(24500 mg)
Grams.zero()          → Grams(0)
Grams.of(-5)          → allowed; movements are signed
```

Signed values are permitted because a reconciliation may *increase* a balance (if the opening
weight was understated). Rejecting negatives here would force the ledger to lie.

### `Material`

Filament material type: `PLA`, `PETG`, `ABS`, `ASA`, `TPU`, `PC`, `PA`, `PVA`, `SUPPORT`,
plus `OTHER(name)` for anything else.

Carries a **nominal density** in g/cm³, used only by estimators that work in length rather
than mass. Density is a property of the material, so it lives here — not scattered across
estimator implementations.

### `Colour`

An RGBA value, stored as `RRGGBBAA` to match the printer's own format directly, avoiding a
lossy conversion at the boundary.

Exposes a display hex for the UI and a contrast-appropriate foreground colour, so that a
swatch is legible whether the filament is black or white.

### `SpoolState`

```
SEALED     → registered, never mounted, presumed full
ACTIVE     → has been used at least once
DEPLETED   → balance reached zero or below
DISCARDED  → thrown away; retained for history, excluded from active inventory
```

Legal transitions:

```
SEALED ──→ ACTIVE ──→ DEPLETED
   │          │           │
   └──────────┴───────────┴──→ DISCARDED
```

`DEPLETED` is reversible — a reconciliation can reveal filament still on the spool, returning
it to `ACTIVE`. `DISCARDED` is terminal. Nothing leaves it, because the physical object is
gone.

### `Location`

```
Storage()          — on a shelf, not mounted
AmsSlot(index)     — mounted in AMS slot 1..4
ExternalSpool()    — feeding the printer directly, bypassing the AMS
```

A spool is in exactly one location. This models the physical world truthfully: a spool cannot
be in two places, and "in storage" is a real location, not the absence of one.

### `TagUid`

The RFID serial read from a Bambu spool. Optional — third-party or refilled spools have none,
and the model must not assume otherwise even though the reference setup uses Bambu filament
exclusively.

### `Confidence`

```
HIGH    — reconciled against a scale recently, no estimates since
MEDIUM  — only measured consumption applied since last reconciliation
LOW     — estimated consumption has accumulated without reconciliation
```

Derived, never set by hand. Rules in §2.6.

### `MovementType`

| Type | Sign | Origin | Requires approval |
|---|---|---|---|
| `OPENING_BALANCE` | + | Spool registration | No |
| `PRINT_CONSUMPTION` | − | Completed print, measured | No |
| `PURGE_WASTE` | − | Colour-change purge | No |
| `ESTIMATED_CONSUMPTION` | − | Approved review of a cancelled/failed print | **Yes** |
| `MANUAL_ADJUSTMENT` | ± | User correction | **Yes** (it *is* the user) |
| `RECONCILIATION` | ± | Scale measurement | **Yes** |
| `DISCARD` | − | Filament thrown away | **Yes** |

The "requires approval" column is the operational form of principle #1: *the system never
guesses silently*. Only movements derived from measured data enter the ledger unattended.

---

## 2.3 Entities

### `Spool`

Identity: a generated `SpoolId`, **not** the RFID tag.

That distinction matters. A spool may have no tag, and — more importantly — a Bambu RFID tag
identifies a *product batch*, not a physical unit. Two identical black PLA spools can carry
the same tag payload. Using it as identity would silently merge two spools into one and
corrupt both balances.

```
SpoolId          id
Material         material
Colour           colour
str              vendor
Grams            opening_weight      -- net filament, excluding the core
Grams            core_weight         -- empty spool mass, for reconciliation
SpoolState       state
Location         location
TagUid?          tag_uid
datetime         registered_at
str?             label               -- user-assigned name
```

`core_weight` exists because reconciliation is done with a kitchen scale, and a scale weighs
the whole spool. Without the core weight, the user is forced to do arithmetic the system
should be doing.

**Invariants**

- `opening_weight > 0`
- `core_weight >= 0`
- A `DISCARDED` spool cannot change location or accept new movements.
- A spool in `AmsSlot(n)` implies no other spool occupies slot `n`. *(Enforced by the
  repository, not the entity — it is a cross-aggregate rule.)*

### `Movement`

The ledger entry. **Immutable after creation.** No setters, no update method, no delete.

```
MovementId       id
SpoolId          spool_id
MovementType     type
Grams            amount              -- signed
datetime         occurred_at
MovementSource   source              -- what produced this entry
str?             note                -- user-supplied reason
PrintJobId?      job_id              -- present for print-derived movements
```

`MovementSource` distinguishes `AUTOMATIC` from `USER_CONFIRMED`. This is what the confidence
calculation reads, and what allows the UI to show which numbers a human vouched for.

**Invariants**

- `amount != 0`. A zero movement records nothing and only adds noise.
- Sign must match the movement type's declared direction, except for the three types that are
  explicitly bidirectional (`MANUAL_ADJUSTMENT`, `RECONCILIATION`, `OPENING_BALANCE`).
- Once persisted, it is never modified. Enforced by the repository exposing no update
  operation — an interface that cannot express a mistake.

### `PrintJob`

```
PrintJobId       id
str              name
PrintJobState    state           -- RUNNING | FINISHED | CANCELLED | FAILED
datetime         started_at
datetime?        ended_at
int?             layer_reached
int?             total_layers
Percentage?      progress
{SlotIndex: Grams}  reported_usage    -- per-tray, from the printer
int?             raw_print_error     -- preserved verbatim; see Q1
str?             raw_gcode_state     -- preserved verbatim; see Q1
```

The two `raw_*` fields exist because the mapping from printer state to cancellation *reason*
is an open question (Q1). Storing the raw values means that when the answer is known, the
classification can be applied retroactively to jobs already recorded. Discarding them would
make that impossible.

### `PendingReview`

The approval queue. This entity is the mechanism behind principle #1.

```
ReviewId              id
PrintJobId            job_id
ReviewReason          reason          -- CANCELLED | FAILED | UNCLASSIFIED
{SpoolId: Grams}      estimated_usage
{SpoolId: Grams}?     confirmed_usage -- user-supplied; overrides estimate
EstimatorKind         estimator_used  -- which strategy produced the estimate
ReviewState           state           -- PENDING | APPROVED | DISMISSED
datetime              opened_at
datetime?             resolved_at
str?                  resolution_note
```

**Invariants**

- A review in `PENDING` has produced no movements. The balance is untouched until resolution.
- Approving generates exactly one `ESTIMATED_CONSUMPTION` movement per spool with a non-zero
  amount.
- **Resolution is idempotent.** A review already `APPROVED` or `DISMISSED` cannot be resolved
  again. Without this, a double-click deducts twice — and in a ledger, a duplicate entry is
  indistinguishable from a real one after the fact.
- `DISMISSED` means "this consumed nothing worth recording", not "ignore this". It is a
  recorded decision with a timestamp, not a deletion.

---

## 2.4 Aggregates and boundaries

```
Spool  (aggregate root)
  └── Movement[]        -- consistency boundary: a balance is only ever
                           computed from movements of one spool

PrintJob  (aggregate root)
  └── PendingReview     -- a review exists only for a job
```

A movement belongs to exactly one spool. There are no cross-spool transactions, which means
no distributed consistency problem — deliberately. A multi-colour print that consumes from
three spools produces three independent movements, not one shared transaction.

---

## 2.5 Domain services

Logic that does not belong to a single entity.

### `BalanceCalculator`

Computes current balance from opening weight and movements. Pure function, trivially
testable, and the single place the central rule of §2.1 is implemented.

### `ConfidenceEvaluator`

Derives a spool's `Confidence` from its movement history. Rules in §2.6.

### `AnomalyDetector`

Flags spools whose state is physically implausible:

- Balance is negative.
- Balance is zero but the spool is still mounted and printing.
- Reconciliation delta exceeded a threshold (default 15% of opening weight).

**A negative balance is permitted, not rejected.** If the ledger says −40 g, the physical
truth is that the opening weight was wrong, or a movement was missed. Refusing to record it
would force the system to display a number it knows is false. Recording it and raising an
anomaly tells the user exactly what to do: weigh the spool.

That is the difference between a system that is correct and a system that merely looks
correct.

---

## 2.6 Confidence rules

Evaluated against the movements since the most recent `RECONCILIATION` (or since
`OPENING_BALANCE` if never reconciled).

```
HIGH    reconciled, and no ESTIMATED_CONSUMPTION since,
        and total consumed since reconciliation < 20% of opening weight

MEDIUM  no ESTIMATED_CONSUMPTION since last reconciliation,
        but consumption has accumulated beyond the HIGH threshold

LOW     one or more ESTIMATED_CONSUMPTION movements since last reconciliation,
        or cumulative estimated amount exceeds 10% of opening weight
```

A spool at `LOW` triggers a prompt to weigh it. The thresholds are configurable and their
defaults are **explicitly provisional** — they are informed guesses, and they should be tuned
against real usage rather than defended.

---

## 2.7 Ports

Interfaces the domain defines and infrastructure implements. Dependencies point inward: the
domain never imports an adapter.

```
SpoolRepository
    get(SpoolId) -> Spool?
    find_by_tag(TagUid) -> Spool?
    find_by_location(Location) -> Spool?
    list(filter) -> Spool[]
    save(Spool) -> None

MovementRepository
    append(Movement) -> None          -- note: no update, no delete
    list_for_spool(SpoolId) -> Movement[]
    list_since(SpoolId, datetime) -> Movement[]

PrintJobRepository
    get(PrintJobId) -> PrintJob?
    save(PrintJob) -> None
    list_recent(limit) -> PrintJob[]

ReviewRepository
    get(ReviewId) -> PendingReview?
    list_pending() -> PendingReview[]
    save(PendingReview) -> None

PrinterGateway
    subscribe(listener) -> None       -- job and tray state changes
    current_trays() -> {SlotIndex: TrayReading}
    fetch_gcode(PrintJobId) -> GcodeDocument?

ConsumptionEstimator
    estimate(PrintJob) -> {SlotIndex: Grams}

Clock
    now() -> datetime
```

`MovementRepository` deliberately exposes no `update` or `delete`. Immutability is enforced by
the shape of the interface, not by a comment asking politely. **A rule that can only be
broken by changing the interface is a rule that holds.**

`Clock` is a port so that time-dependent rules — confidence windows, review ages — are
testable without sleeping or patching system time.

---

## 2.8 Domain events

Raised by the domain, translated to Home Assistant events by infrastructure. The domain does
not know HA consumes them.

| Event | Raised when |
|---|---|
| `SpoolRegistered` | A new spool enters inventory |
| `SpoolMounted` / `SpoolUnmounted` | Location changes to/from an AMS slot |
| `MovementRecorded` | Any ledger entry is appended |
| `SpoolDepleted` | Balance crosses zero |
| `ReviewOpened` | A cancelled or failed job needs attention |
| `ReviewResolved` | A review is approved or dismissed |
| `ConfidenceDegraded` | A spool drops to `LOW` |
| `AnomalyDetected` | `AnomalyDetector` raises a flag |

`UnknownSpoolDetected` is raised when an unrecognised RFID appears in a slot. The system
**does not auto-create a spool** — a guessed opening weight is a fabricated number, and a
fabricated number in a ledger is worse than a missing one.
