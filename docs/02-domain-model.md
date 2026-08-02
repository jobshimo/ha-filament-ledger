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
balance(spool) = Σ signed_amount(movements of spool)
```

**The opening weight is not a separate term.** It enters the ledger as the spool's first
movement, an `OPENING_BALANCE` of `+1000 g`, so the balance is a plain sum with no special
case and nothing to keep in sync — the same expression [ADR-0001](adr/0001-append-only-ledger.md)
states, the same one [08 §8.3](08-data-model.md) executes as a single `SUM`, and the same one
[09 §9.2](09-testing-strategy.md) asserts as a property.

> An earlier draft wrote this as `opening_weight − Σ(movements)`, which is wrong in a way
> worth preserving as a warning. Amounts are *already signed* — a print consumption is stored
> as `−84 g`, not as `84 g` to be subtracted later. Subtracting a negative amount **increases**
> the balance, so under that formula every print would have made the spool heavier, and the
> opening weight would have been counted twice into the bargain.
>
> The formula is one line and it is the only line that has to be right. Sign conventions
> stated twice, in two directions, are how a ledger ends up disagreeing with itself.

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
SEALED     → only an OPENING_BALANCE movement exists; nothing has happened to it yet
ACTIVE     → in use: more than one movement, balance still above zero
DEPLETED   → balance is zero or below
DISCARDED  → thrown away; retained for history, excluded from active inventory
```

`ACTIVE` is defined by *"something has happened to this spool"*, not by *"filament has left
it"*. The distinction matters because a spool whose only second movement is a positive
`RECONCILIATION` — the user weighed it and found the opening weight was understated — has been
handled, corrected and verified. Calling it `SEALED` would be a lie about a spool somebody has
already opened and put on a scale.

**State is derived, not stored.** Three of these four values are a function of the movement
history and the balance it produces:

```
DISCARDED   if discarded_at is set
DEPLETED    else if balance <= 0
SEALED      else if the only movement is OPENING_BALANCE
ACTIVE      otherwise
```

Evaluated top-down, first match wins, which makes the function total — every spool has
exactly one state and no combination of movements can leave it without one.

Only `discarded_at` is stored, because discarding is a **decision** a human made at a
particular moment, not a computation. Everything else is arithmetic over the ledger.

This is [ADR-0001](adr/0001-append-only-ledger.md) applied consistently. An earlier draft
stored `state` as a column alongside a balance that was computed, which is precisely the
two-sources-of-truth arrangement that ADR rejects: a bad write flips a spool to `DEPLETED`
while its movements sum to 340 g, and nothing detects the disagreement. Deriving it means the
disagreement cannot be represented.

Legal transitions still describe reality, and now they are consequences rather than rules to
enforce:

```
SEALED ──→ ACTIVE ←──→ DEPLETED
   │          │           │
   └──────────┴───────────┴──→ DISCARDED
```

`DEPLETED` is reversible for free — a reconciliation that reveals filament still on the spool
raises the balance above zero and the state follows. `DISCARDED` is terminal, and it is the
only transition the system has to actually *perform*, because the physical object is gone.

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

### `Percentage`

A ratio in `0..100`, used for print progress. Validates its own range on construction, so a
progress value of 140 cannot exist.

Kept distinct from `Grams` deliberately: the whole reason `Grams` exists is that a `float`
lets you add a mass to a percentage. Making the percentage a bare `float` would leave half
the hole open.

### `Confidence`

```
HIGH    — no estimates since the last reconciliation, and little drawn since
MEDIUM  — consumption has accumulated, but all of it plan-derived or confirmed
LOW     — estimated consumption has accumulated without reconciliation
```

Derived, never set by hand. Rules in §2.6.

### `MovementType`

| Type | Sign | Origin | Requires approval |
|---|---|---|---|
| `OPENING_BALANCE` | + | Spool registration | No |
| `PRINT_CONSUMPTION` | − | Completed print, slicer plan fully executed | No |
| `PURGE_WASTE` | − | Colour-change purge | *(reserved — see below)* |
| `ESTIMATED_CONSUMPTION` | − | Approved review of an interrupted print | **Yes** |
| `MANUAL_ADJUSTMENT` | ± | User correction | **Yes** (it *is* the user) |
| `RECONCILIATION` | ± | Scale measurement | **Yes** |
| `DISCARD` | − | Filament thrown away | **Yes** |

The "requires approval" column is the operational form of principle #1: *the system never
guesses silently*. Only consumption whose plan is known to have run to completion enters the
ledger unattended.

**`PURGE_WASTE` has no producer in v1, and that is deliberate.** No use case in
[04](04-use-cases.md) and no service in [05](05-ha-integration.md) creates one. The type is
reserved in the model and in the schema so that whichever way [Q2](01-vision.md) is answered,
the answer does not require a migration:

- Q2 answers *"purge is already inside the per-tray figure"* → the type stays unused, and
  costs nothing but a row in this table.
- Q2 answers *"purge is not counted"* → Phase 4 adds the producer against a type that has
  been in the schema since the first migration.

A reserved type with no producer is honest. A type documented as *"available for manual
entry"* with no mechanism to enter it is not, and that is what an earlier draft claimed.

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
Location         location
TagUid?          tag_uid
datetime         registered_at
datetime?        discarded_at        -- the only stored part of SpoolState
str?             label               -- user-assigned name
```

There is no `state` field. `SpoolState` is derived from `discarded_at` and the balance, per
§2.2.

`core_weight` exists because reconciliation is done with a kitchen scale, and a scale weighs
the whole spool. Without the core weight, the user is forced to do arithmetic the system
should be doing. It is **mandatory, with no silent default** — see §2.8.

**Invariants**

- `opening_weight > 0`
- `core_weight >= 0`
- A spool with `discarded_at` set cannot change location or accept new movements.
- A spool in `AmsSlot(n)` implies no other spool occupies slot `n`, and at most one spool is
  in `ExternalSpool()`. *(Enforced by the repository, not the entity — they are
  cross-aggregate rules.)*

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
ReviewId?        review_id           -- present for movements an approval produced
```

`review_id` records which approval created an `ESTIMATED_CONSUMPTION` entry. Without it the
history view can say *"confirmed by you"* but not *which decision* confirmed it, and the
review queue stops being an audit trail the moment a review is resolved.

`MovementSource` distinguishes `AUTOMATIC` from `USER_CONFIRMED`. This is what the confidence
calculation reads, and what allows the UI to show which numbers a human vouched for.

**Invariants**

- `amount != 0`. A zero movement records nothing and only adds noise.
- Sign must match the movement type's declared direction, except for the two types marked `±`
  in §2.2: `MANUAL_ADJUSTMENT` and `RECONCILIATION`. `OPENING_BALANCE` is **not** among them —
  it is always positive, because `opening_weight > 0` is a `Spool` invariant and a spool
  cannot be born owing filament.
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
ReviewId               id
PrintJobId             job_id
ReviewReason           reason           -- CANCELLED | FAILED | UNCLASSIFIED | UNMAPPED_USAGE
{SlotIndex: Grams}     estimated_usage
{SlotIndex: Grams}?    confirmed_usage  -- user-supplied; overrides estimate
{SlotIndex: SpoolId?}  slot_resolution  -- frozen at creation; None = no spool was mounted
EstimatorKind          estimator_used   -- which strategy produced the estimate
ReviewState            state            -- PENDING | APPROVED | DISMISSED
datetime               opened_at
datetime?              resolved_at
str?                   resolution_note
```

**Amounts are keyed by slot, not by spool.** An earlier draft keyed them by `SpoolId`, which
made the case that most needs a review impossible to represent: [UC-04](04-use-cases.md)
opens a review precisely when a slot reported usage and *no spool was mounted in it*. There
is no `SpoolId` to key that entry with. Keying by slot and carrying the resolution separately
lets the review say the only honest thing available — *"slot 3 used 12 g and I do not know
which spool was in it"* — and lets the user supply the missing half.

`slot_resolution` is **frozen when the review opens**, not looked up at approval time. A
spool unmounted between the print ending and the user getting to the queue must not silently
redirect the deduction to whatever is in the slot now.

`ReviewReason` gains `UNMAPPED_USAGE` for a job that reached `FINISHED` with usage on an
unresolvable slot. The other three describe *why the print stopped*, and this one does not —
the print did not stop, the inventory was incomplete.

**Invariants**

- A review in `PENDING` has produced no movements. The balance is untouched until resolution.
- Approving generates exactly one `ESTIMATED_CONSUMPTION` movement per **resolved** slot with
  a non-zero amount.
- **A slot with a non-zero confirmed amount and no resolved spool blocks approval.** The user
  must assign a spool, zero the amount, or dismiss. Approving it would either invent a spool
  or discard a real consumption silently, and both are the failure this project exists to
  prevent.
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

Sums a spool's movements. That is the whole of it — the opening balance is one of them, so
there is no second input and no special case. Pure function, trivially testable, and the
single place the central rule of §2.1 is implemented.

It takes movements and returns `Grams`. It does **not** take a `Spool`, because then two
callers could disagree about whether the opening weight had been applied yet.

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

Let **`anchor`** be the most recent `RECONCILIATION`, or the `OPENING_BALANCE` if the spool
has never been reconciled. Let **`since`** be every movement after the anchor.

The opening balance counts as an anchor because it is a human-confirmed number — the user
read it off the packaging or weighed the spool. It is the weakest possible anchor, but it is
an anchor, and treating it as one is what keeps a freshly registered spool from being
displayed as untrustworthy on the day it is registered.

```
LOW     if `since` contains any ESTIMATED_CONSUMPTION

MEDIUM  else if total consumed in `since` >= 20% of opening weight

HIGH    otherwise
```

An earlier draft added *"or cumulative estimated amount exceeds 10% of opening weight"* to the
`LOW` branch. That clause can never fire: a movement's amount is non-zero by invariant
(§2.3), so any estimated amount at all already satisfies the first condition. Dead logic in a
rule this visible is worse than verbose — it invites an implementer to reconstruct an
intention that was never there.

**One approved estimate is enough to drop a spool to `LOW`.** That is the intended
aggressiveness. `LOW` does not mean *"this number is bad"*; it means *"weigh this when you get
a chance"*, and the cost of being asked is thirty seconds with a kitchen scale.

**Evaluated top-down, first match wins.** The function is total: every spool lands on exactly
one level, including a spool registered thirty seconds ago with a single `OPENING_BALANCE`
movement, which is `HIGH`.

That totality is not pedantry. Confidence appears next to every balance in the product
([05 §5.3](05-ha-integration.md), [06 §6.2](06-ui-spec.md)), so a spool the rules do not cover
is a spool whose dot colour depends on which branch an implementer wrote first.

A spool at `LOW` triggers a prompt to weigh it. The thresholds are configurable and their
defaults are **explicitly provisional** — they are informed guesses, and they should be tuned
against real usage rather than defended.

---

## 2.7 Ports

Interfaces the domain defines and infrastructure implements. Dependencies point inward: the
domain never imports an adapter.

**Every port that performs I/O is `async`.** Home Assistant runs on asyncio and SQLite work
is dispatched to an executor, so the boundary is a fact of the host, not a preference — see
[ADR-0005](adr/0005-async-io-ports.md). `Clock` stays synchronous because reading a clock is
not I/O.

```
SpoolRepository
    async get(SpoolId) -> Spool?
    async find_by_tag(TagUid) -> Spool[]      -- may return several; see below
    async find_by_location(Location) -> Spool?
    async list(filter) -> Spool[]
    async save(Spool) -> None

MovementRepository
    async append(Movement) -> None            -- note: no update, no delete
    async list_for_spool(SpoolId) -> Movement[]
    async list_since(SpoolId, datetime) -> Movement[]

PrintJobRepository
    async get(PrintJobId) -> PrintJob?
    async save(PrintJob) -> None
    async list_recent(limit) -> PrintJob[]

ReviewRepository
    async get(ReviewId) -> PendingReview?
    async list_pending() -> PendingReview[]
    async save(PendingReview) -> None

PrinterGateway
    subscribe(listener) -> None               -- registers a callback; no I/O
    async current_trays() -> {SlotIndex: TrayReading}

ConsumptionEstimator
    async estimate(PrintJob) -> {SlotIndex: Grams}
    raises EstimationUnavailable

Clock
    now() -> datetime                         -- synchronous, deliberately
```

`MovementRepository` deliberately exposes no `update` or `delete`. Immutability is enforced by
the shape of the interface, not by a comment asking politely. **A rule that can only be
broken by changing the interface is a rule that holds.**

`find_by_tag` returns a **list**, not an optional single spool. §2.3 states that a Bambu tag
identifies a product batch rather than a physical unit and that two spools may legitimately
carry the same payload; a port returning one spool would force the adapter to pick one of
them, silently, and deduct from a spool the user never loaded. The port returns what is true
and [UC-02](04-use-cases.md) decides what to do with an ambiguous answer — which is to ask.

`fetch_gcode` **is not here.** An earlier draft put it on `PrinterGateway` returning a
`GcodeDocument`, which drags a file format into a layer that is supposed to know nothing about
files. G-code retrieval is an infrastructure detail of one estimator; `GcodeLayerEstimator`
depends on an infrastructure-level source, and the domain only ever sees
`ConsumptionEstimator`. See [07 §7.3](07-consumption-estimation.md).

`Clock` is a port so that time-dependent rules — confidence windows, review ages — are
testable without sleeping or patching system time.

## 2.8 Values the domain refuses to default

`core_weight` is **mandatory on a `Spool`** and has no fallback inside the domain.

The configuration flow offers a per-vendor default ([05 §5.2](05-ha-integration.md)) and the
application layer resolves it before constructing the entity. That is a convenience for the
user, applied in one place, above the domain.

The reason this is written down: `core_weight` is subtracted from every scale reading in
[UC-08](04-use-cases.md), the operation this project calls its ground truth. A silent fallback
to zero would not fail — it would quietly report every reconciliation as roughly 250 g heavier
than reality, forever, and the error would look like drift rather than like a bug.

**A default that corrupts a measurement is worse than a missing value that stops you.**

---

## 2.9 Domain events

Raised by the domain, translated to Home Assistant events by infrastructure. The domain does
not know HA consumes them.

| Event | Raised when |
|---|---|
| `SpoolRegistered` | A new spool enters inventory |
| `SpoolMounted` / `SpoolUnmounted` | Location changes to/from an AMS slot |
| `MovementRecorded` | Any ledger entry is appended |
| `SpoolDepleted` | Balance crosses zero |
| `ReviewOpened` | An interrupted job, or unmapped usage, needs attention |
| `ReviewResolved` | A review is approved or dismissed |
| `ConfidenceDegraded` | A spool drops to `LOW` |
| `AnomalyDetected` | `AnomalyDetector` raises a flag |

Two events exist specifically to refuse a guess:

`UnknownSpoolDetected` is raised when an unrecognised RFID appears in a slot. The system
**does not auto-create a spool** — a guessed opening weight is a fabricated number, and a
fabricated number in a ledger is worse than a missing one.

`AmbiguousTagDetected` is raised when a recognised RFID resolves to **more than one**
non-discarded spool, which §2.3 establishes is legal. The system does not pick the newest, the
fullest, or the first. It names the candidates and asks, because choosing wrong means every
subsequent print deducts from a spool sitting on a shelf while the one in the machine runs
out unannounced.
