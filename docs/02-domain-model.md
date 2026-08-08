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

### `TrayRef`

```
PrinterSerial  printer   — the machine's own serial, as upstream reports it
AmsIndex       ams       — which AMS unit, numbered as the printer numbers it (AMS 1 → 1)
SlotIndex      slot      — the tray position on that unit, 1..4
```

**What actually identifies a tray.** Through v1 a tray was a bare `SlotIndex`, which was a
correct decision for as long as there was one machine to hold it and stopped being a true one
the moment the model could hold two: two printers both have a tray 1, and so do two AMS units
on one printer. Every surface that used to name a slot names one of these instead — where a
spool is mounted, the key of a job's per-tray usage, the key of a review's per-tray estimate —
because a figure keyed by an ambiguous name is a figure that lands on the wrong spool the
first time a second machine appears.

Ordered, so that every reader — the deduction loop, the review card, the persisted JSON —
sees one canonical tray order rather than each imposing its own.

**Supported since v2.0.** The gateway resolves every machine the registry describes and keys
each one's trays under its own serial ([05 §5.8](05-ha-integration.md)); the ledger follows
all of them. Ordering by printer first is what makes a listing of several machines' trays
group itself, which is how the AMS view draws a section per machine
([06 §6.4](06-ui-spec.md)).

`UNIDENTIFIED` is a reserved `PrinterSerial`: the printer a ledger has always talked to but
never recorded the name of. Migration 0007 writes it into every row that predates the
reference ([08 §8.2](08-data-model.md)), and the composition root replaces it once discovery
resolves a real serial. It is **accepted** where `TagUid`'s sixteen-zero sentinel is refused,
and the difference is the point: sixteen zeros denotes the *absence* of a tag, while this
denotes a real machine whose name is unknown — and a single-printer ledger has exactly one of
those, so every row carrying it belongs to the same printer.

**That argument is also its limit, and v2.0 draws the limit explicitly.** The sentinel is
sound only while there is one machine for it to mean. So discovery gives it to a printer whose
serial it could not read only when that printer is the only one there is; with several, an
unnamed machine is not followed at all, because two live machines answering to one name would
share a single tray space and collide slot for slot. For the same reason, adoption replaces
the placeholder only when discovery names exactly one machine — with several there is no
record of which one the rows meant, and picking is a guess with somebody's spools on the other
end ([08 §8.4](08-data-model.md)).

### `Location`

```
Storage()          — on a shelf, not mounted
AmsSlot(tray)      — mounted in the tray this `TrayRef` names
ExternalSpool(printer)
                   — feeding that printer directly, bypassing its AMS
```

A spool is in exactly one location. This models the physical world truthfully: a spool cannot
be in two places, and "in storage" is a real location, not the absence of one.

`ExternalSpool(printer)` names its machine, since v2.0. Each printer has exactly one direct
feed, so an unqualified *external spool* names as many positions as there are printers — and
the partial unique index stating *the direct feed holds one spool* ([08 §8.1](08-data-model.md))
would have refused the second machine's reel to a ledger that could truthfully hold it.
Migration 0008 widened the value and the index together, which is the same reading that
widened `idx_spool_slot` in 0007, arriving one release later because this is the release where
a second machine is followed rather than merely representable.

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
LOW     — an estimate has been applied, or so much has been drawn since the last
          weighing that the accumulated drift is worth checking
```

Derived, never set by hand. Rules in §2.6.

**`LOW` is reached two ways and the level does not say which.** That is deliberate: the level
answers *how much should I trust this number*, and both routes answer it identically — weigh
the spool. Which route was taken is a different question, answered by the basis the
application layer assembles beside the level (`ConfidenceBasis`, §2.6).

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
- A spool in `AmsSlot(t)` implies no other spool occupies the tray `t` names — all three
  parts of it — and at most one spool is in `ExternalSpool()`. *(Enforced by the repository,
  not the entity — they are cross-aggregate rules.)*

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
PrinterSerial?   printer         -- which machine ran it; null only before migration 0008
int?             layer_reached
int?             total_layers
Percentage?      progress
{TrayRef: Grams}    reported_usage    -- per-tray, from the printer
int?             raw_print_error     -- preserved verbatim; see Q1
str?             raw_gcode_state     -- preserved verbatim; see Q1
```

The two `raw_*` fields exist because the mapping from printer state to cancellation *reason*
is an open question (Q1). Storing the raw values means that when the answer is known, the
classification can be applied retroactively to jobs already recorded. Discarding them would
make that impossible.

**`printer` is what makes an ending correlatable, and null is not a machine.** An upstream
lifecycle event carries no job id, so an ending is matched to the newest `RUNNING` row — and
with two machines printing at once that row is as likely to be the other one's job. The
ending's per-tray figures ride with the match, so a mis-correlation deducts one printer's
grams from the spools in the other printer's trays and flags neither. Correlation is therefore
by state **and** by machine ([04 UC-04](04-use-cases.md), `TrackPrintJob`).

Null means the ledger did not record which machine ran the job, which is every row written
before migration 0008 — and a backfill states what the old rows say ([08 §8.4](08-data-model.md)).
A nameless row is correlated to by nobody: the single print that spanned the upgrade leaves a
stale `RUNNING` row, its ending opens a fresh one, and that is the shape a restart already
produced. Letting it match would buy back one duration by guessing which machine a row that
explicitly does not say belongs to.

### `PendingReview`

The approval queue. This entity is the mechanism behind principle #1.

```
ReviewId                     id
PrintJobId                   job_id
ReviewReason                 reason           -- CANCELLED | FAILED | UNCLASSIFIED | UNMAPPED_USAGE
{TrayRef: Grams}             estimated_usage
{TrayRef: Grams}?            confirmed_usage  -- user-supplied; overrides estimate
[(TrayRef, ReviewCharge)]    charges          -- frozen at creation; empty = no spool was mounted
EstimatorKind                estimator_used   -- which strategy produced the estimate
ReviewState                  state            -- PENDING | APPROVED | DISMISSED
datetime                     opened_at
datetime?                    resolved_at
str?                         resolution_note

ReviewCharge = (SpoolId spool_id, Grams amount)
```

The entity holds one `ReviewLine` per tray — its `estimated` figure and its `charges` — and
the two collections above are how a reader asks for one or the other.

**Amounts are keyed by tray, not by spool.** An earlier draft keyed them by `SpoolId`, which
made the case that most needs a review impossible to represent: [UC-04](04-use-cases.md)
opens a review precisely when a tray reported usage and *no spool was mounted in it*. There
is no `SpoolId` to key that entry with. Keying by tray and carrying the attribution separately
lets the review say the only honest thing available — *"slot 3 used 12 g and I do not know
which spool was in it"* — and lets the user supply the missing half.

The key is a whole `TrayRef` and not a tray number, because a review may sit in the queue for
days: a bare number would come back ambiguous the moment a second machine existed to have
one, and the deduction would land on whichever tray 1 the reader happened to resolve.

**The estimate is per tray; the attribution is per charge.** They are different shapes, and
conflating them was a real limitation rather than a tidy simplification. The printer reports
one figure per tray and can report nothing else, so `estimated_usage` is a map. But a spool
that empties mid-print and is replaced in the same tray leaves that one figure belonging to
*two* spools, and a `{tray: SpoolId}` map cannot say so — it can only charge the whole print
to whichever spool happened to be mounted when the job ended, and credit the one that fed the
first half with nothing. So the attribution is a list of charges, and a tray may appear in it
more than once.

The charges are **frozen when the review opens**, not looked up at approval time. A spool
unmounted between the print ending and the user getting to the queue must not silently
redirect the deduction to whatever is in the tray now. A tray with a mounted spool freezes as
one charge for its whole estimate — the honest proposal for a tray nobody has said was shared.

`ReviewReason` gains `UNMAPPED_USAGE` for a job that reached `FINISHED` with usage on an
unresolvable tray. The other three describe *why the print stopped*, and this one does not —
the print did not stop, the inventory was incomplete.

**Invariants**

- A review in `PENDING` has produced no movements. The balance is untouched until resolution.
- Approving generates exactly one `ESTIMATED_CONSUMPTION` movement per non-zero **charge**.
- **Each tray's charges add up to what that tray confirms.** One rule where there used to be
  two, and it says both. A tray with a non-zero amount and nothing attributed fails it, which
  is the refusal the queue has always made: the user must assign a spool, zero the amount, or
  dismiss, because approving it would either invent a spool or discard a real consumption
  silently. A tray with 10 g attributed out of 300 fails it too — the other 290 g came off
  *something*, and accepting the shortfall would lose them with no trace. It is also what
  makes the panel's **[ Load the rest ]** a subtraction rather than a feature: what is left
  to charge is the tray's amount minus what is charged so far ([06 §6.3](06-ui-spec.md)).
- **A tray charges each spool at most once.** Its attribution answers *how many grams did
  each spool give*, which is one figure per spool; the same spool twice is one answer written
  as two.
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
- Reconciliation delta **reached or exceeded** a threshold (default 15% of opening weight).
  Inclusive on purpose: an anomaly is a prompt to look, not an accusation, so the boundary
  errs toward telling the user.

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

LOW     else if total consumed in `since` >= 41% of opening weight

MEDIUM  else if total consumed in `since` >= 20% of opening weight

HIGH    otherwise
```

An earlier draft added *"or cumulative estimated amount exceeds 10% of opening weight"* to the
first `LOW` branch. That clause can never fire: a movement's amount is non-zero by invariant
(§2.3), so any estimated amount at all already satisfies the condition. Dead logic in a rule
this visible is worse than verbose — it invites an implementer to reconstruct an intention
that was never there.

**One approved estimate is enough to drop a spool to `LOW`.** That is the intended
aggressiveness. `LOW` does not mean *"this number is bad"*; it means *"weigh this when you get
a chance"*, and the cost of being asked is thirty seconds with a kitchen scale.

### Why there is a second consumption rung

With one threshold the ladder had two positions across a whole spool's life. On the reference
instance a reel 21% drawn and a reel drawn to the core wore the same badge, so the signal
stopped carrying information after roughly one ordinary print.

The 20% boundary is not what was wrong with that, and it has not moved. What was missing was
somewhere above it to go, and the figure comes from the only drift this project has actually
measured — two reconciliations, recorded in [07 §7.5](07-consumption-estimation.md):

- 50.0 g adrift over 220.0 g drawn — 22.7% of what was drawn;
- 333.1 g adrift over 916.9 g drawn — 36.3% of what was drawn.

Two points are not a curve and are not fitted as one. What they do support is a **bound**:
take the worse of the two rates, 36.3% of whatever has been drawn. §2.5 already names the
disagreement this project considers worth telling the user about — a reconciliation delta
reaching 15% of the opening weight. Under that bound the drift reaches 15% of the reel once
41.3% of it has been drawn, which is where the second rung sits, rounded **down** to 41% for
the same reason the anomaly boundary is inclusive: it errs toward telling the user.

Past that point weighing the spool would plausibly *disagree* with the ledger rather than
confirm it, and *"weigh this when you get a chance"* is exactly what `LOW` has always meant.
The rung measures from the anchor like every other, so weighing answers it — a heavily used
reel is not condemned to `LOW` for the rest of its life.

Both figures are configurable and both are **explicitly provisional**. The 20% boundary is an
informed guess; the 41% one is a bound drawn from a sample of two, which is more than nothing
and much less than a characterisation. Neither is to be defended, and both should be revisited
the moment there are enough reconciliations to say more.

### The level, and the reason for it

**Evaluated top-down, first match wins.** The function is total: every spool lands on exactly
one level, including a spool registered thirty seconds ago with a single `OPENING_BALANCE`
movement, which is `HIGH`.

That totality is not pedantry. Confidence appears next to every balance in the product
([05 §5.3](05-ha-integration.md), [06 §6.2](06-ui-spec.md)), so a spool the rules do not cover
is a spool whose dot colour depends on which branch an implementer wrote first.

A spool at `LOW` triggers a prompt to weigh it, whichever branch put it there.

`ConfidenceEvaluator` returns the level and nothing else, and stays a pure function of
movements and an opening weight. The question a user actually asks — *why did it change?* —
is answered beside it by **`ConfidenceBasis`**, assembled in the application layer
(`application/query.py`): the anchor's type and date, what has left the spool since, and how
many approved estimates landed in that window. Naming the anchor is what lets a surface say
*since you weighed it* rather than *since you registered it*, which are different claims.

The basis is read from the evaluator's own window, over the same movements the level was
evaluated on, so the sentence on screen cannot describe a spool the badge does not. Assembly
belongs to the application layer for the reason [adr/0007](adr/0007-corrections-are-more-history.md)
already settled for void filtering: the domain service stays pure, and the application
prepares both what it is fed and what is shown beside its answer.

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
    async count_for_spool(SpoolId) -> int    -- for SpoolState.derive; see §2.2

PrintJobRepository
    async get(PrintJobId) -> PrintJob?
    async save(PrintJob) -> None
    async list_recent(limit, printer=None) -> PrintJob[]

ReviewRepository
    async get(ReviewId) -> PendingReview?
    async list_pending() -> PendingReview[]
    async save(PendingReview) -> None

PrinterGateway
    subscribe(listener) -> None               -- registers a callback; no I/O
    async current_trays() -> {TrayRef: TrayReading}

ConsumptionEstimator
    async estimate(PrintJob) -> {TrayRef: Grams}
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

`UnknownSpoolDetected` is raised when an unrecognised RFID appears in a tray and
auto-registration does not apply — `auto_register_on_detect` off, or a reading missing its
material or colour. The system **does not guess a spool into existence** — a guessed
identity is a fabricated fact, and a fabricated fact in a ledger is worse than a missing
one. A full Bambu reading registers instead, at the reel's own tagged weight or the
configured default when the tag gives none (UC-02).

`AmbiguousTagDetected` is raised when a recognised RFID resolves to **more than one**
non-discarded spool, which §2.3 establishes is legal. The system does not pick the newest, the
fullest, or the first. It names the candidates and asks, because choosing wrong means every
subsequent print deducts from a spool sitting on a shelf while the one in the machine runs
out unannounced.
