# 04 — Use Cases

Every operation the system performs. One class each, one public method each, each a
transaction boundary.

Format: trigger, preconditions, flow, postconditions, failure modes.

---

## UC-01 · RegisterSpool

Adds a spool to inventory.

**Trigger** — user action in the panel, or HA service `filament_ledger.register_spool`.

**Input** — material, colour, vendor, opening weight, core weight, optional label, optional
`tag_uid`, optional initial location (defaults to storage).

**Preconditions**
- Opening weight > 0.
- If `tag_uid` is supplied and already belongs to a non-discarded spool, the caller must have
  confirmed the duplicate. Bambu tags identify a batch, not a unit, so duplicates are legal —
  but they must be deliberate, never accidental.

**Flow**
1. Build the `Spool` in state `SEALED`.
2. Persist it.
3. Append an `OPENING_BALANCE` movement for the opening weight.
4. Raise `SpoolRegistered`.

**Postconditions** — spool exists, balance equals opening weight, one movement in history.

**Failures** — invalid material, non-positive weight, unconfirmed duplicate tag.

> A spool is born with a ledger entry. There is no such thing as a balance without a movement
> that explains it.

---

## UC-02 · MountSpool

Associates a spool with an AMS slot.

**Trigger** — automatic, on RFID detection from `PrinterGateway`; or manual, for spools
without a tag.

**Input** — `SpoolId` (manual) or `TagUid` + slot index (automatic).

**Preconditions**
- Spool is not `DISCARDED`.
- Target slot is free, or holds a spool that is implicitly displaced.

**Flow**
1. Resolve the spool — by id, or by tag lookup.
2. **If tag resolution fails: raise `UnknownSpoolDetected` and stop.** No spool is created.
3. If the slot holds a different spool, unmount it to storage (UC-03).
4. Set location to `AmsSlot(n)`.
5. Raise `SpoolMounted`.

**Postconditions** — spool is in the slot; at most one spool per slot.

**Failures** — spool discarded; slot index out of range.

> Step 2 is the important one. Auto-creating a spool means inventing an opening weight, and an
> invented number in a ledger is worse than a missing one — it looks authoritative.

---

## UC-03 · UnmountSpool

Returns a spool to storage.

**Trigger** — RFID absence detected, or user action.

**Flow** — set location to `Storage()`, raise `SpoolUnmounted`.

**Note** — unmounting records no movement. Moving a spool consumes no filament. The
distinction between *location change* and *quantity change* is kept strict; conflating them is
how inventory systems start lying.

---

## UC-04 · RecordPrintConsumption

Deducts filament after a successful print. **The only fully automatic deduction in the
system.**

**Trigger** — `PrinterGateway` reports a job reaching `FINISHED`.

**Input** — `PrintJobId`, per-slot reported usage.

**Preconditions**
- Job is `FINISHED`, not cancelled or failed.
- Job has not already been recorded — checked by existing movements carrying its `job_id`.

**Flow**
1. Verify idempotency; abort silently if already recorded.
2. For each slot with non-zero usage:
   a. Resolve the mounted spool. If none, collect as an orphan and continue.
   b. Append `PRINT_CONSUMPTION` with source `AUTOMATIC`.
3. Re-evaluate confidence for affected spools.
4. Run anomaly detection.
5. Raise `MovementRecorded` per movement; `SpoolDepleted` where balance crossed zero.
6. If any orphans were collected, open a review (UC-05) covering only those slots.

**Postconditions** — balances reduced by measured amounts; job marked recorded.

**Failures** — no mounted spool for a consuming slot (degrades to a review, does not throw).

**Why automatic here and nowhere else:** this number is *measured by the printer*, not
inferred. Requiring approval for measured data would train the user to approve without
reading — and an approval reflex is worse than no approval step at all.

---

## UC-05 · OpenPendingReview

Creates an approval item for a cancelled or failed print.

**Trigger** — job reaches `CANCELLED` or `FAILED`; or UC-04 found orphan slots.

**Input** — `PrintJobId`, raw `gcode_state`, raw `print_error`.

**Flow**
1. Classify the reason: `CANCELLED`, `FAILED`, or `UNCLASSIFIED`.
   **Provisional rule pending Q1** — `print_error == 0` suggests user cancellation; non-zero
   suggests system failure. Both raw values are stored verbatim so the classification can be
   recomputed once the rule is confirmed.
2. Ask the `ConsumptionEstimator` for per-slot grams.
3. Map slots to mounted spools.
4. Create `PendingReview` in `PENDING`, recording which estimator ran.
5. Raise `ReviewOpened`.

**Postconditions** — a pending review exists. **No movement. No balance changed.**

**Failures** — estimation unavailable: the review is still created, with a zero estimate and
an explicit flag. The user is asked; nothing is guessed.

> The system's job here is to *notice* and *ask*. Not to decide.

---

## UC-06 · ApproveReview

Converts a review into ledger entries.

**Trigger** — user approves in the panel, or service call.

**Input** — `ReviewId`, optional per-spool corrected amounts, optional note.

**Preconditions**
- Review is `PENDING`. **Idempotency is enforced here.** A review already resolved cannot be
  resolved again — otherwise a double-click deducts twice, and a duplicate ledger entry is
  indistinguishable from a real one after the fact.

**Flow**
1. Load the review; reject if not `PENDING`.
2. Determine final amounts: user-supplied values override estimates, per spool.
3. For each non-zero amount, append `ESTIMATED_CONSUMPTION` with source `USER_CONFIRMED`.
4. Mark the review `APPROVED` with a timestamp and the note.
5. Re-evaluate confidence — approving an estimate degrades the affected spools toward `LOW`.
6. Run anomaly detection.
7. Raise `ReviewResolved` and `MovementRecorded`.

**Postconditions** — balances reduced by *confirmed* amounts; review terminal.

**Failures** — review not found; already resolved; negative amount supplied.

---

## UC-07 · DismissReview

Resolves a review without recording consumption.

**Use case** — a print that failed on the first layer, or a false positive.

**Flow** — mark `DISMISSED`, store the note, raise `ReviewResolved`. No movement.

**Note** — dismissal is a *recorded decision* with a timestamp and a reason, not a deletion.
The queue is an audit trail, not an inbox to be emptied.

---

## UC-08 · ReconcileSpool

Corrects a balance against a physical measurement. **The system's ground truth.**

**Trigger** — user weighs a spool and enters the reading.

**Input** — `SpoolId`, measured weight, and whether the measurement includes the core.

**Flow**
1. Compute net filament: if the reading includes the core, subtract `core_weight`.
2. Compute `delta = measured_net − current_balance`.
3. If delta is zero, record nothing and inform the user. *(A zero movement is noise.)*
4. Append `RECONCILIATION` for the delta, source `USER_CONFIRMED`.
5. Reset confidence to `HIGH`.
6. If `|delta|` exceeds the anomaly threshold, raise `AnomalyDetected` — a large correction
   means something upstream is systematically wrong and deserves attention.
7. If the spool was `DEPLETED` and measured net is positive, return it to `ACTIVE`.

**Postconditions** — balance equals measurement; confidence `HIGH`; drift recorded and visible.

> The delta is not an embarrassment to be hidden. It is the system's error signal — the only
> honest measure of how wrong the estimates have been.

---

## UC-09 · DiscardFilament

Records filament thrown away.

**Trigger** — user action.

**Input** — `SpoolId`, mode (`WHOLE_SPOOL` or `PARTIAL`), amount if partial, reason.

**Flow**
1. If `WHOLE_SPOOL`: amount is the entire current balance; append `DISCARD`; set state
   `DISCARDED`; move location to storage.
2. If `PARTIAL`: append `DISCARD` for the supplied amount. State is unchanged unless the
   balance reaches zero.
3. Raise `MovementRecorded`; `SpoolDepleted` if applicable.

**Postconditions** — a discarded spool is excluded from active inventory but **retained in
full**, with its history intact.

**Failures** — partial amount exceeding balance is *permitted*, producing a negative balance
and an anomaly. The physical event happened; the ledger records reality and flags the
inconsistency rather than refusing the truth.

---

## UC-10 · AdjustSpool

A free-form manual correction, for cases the specific use cases do not cover.

**Input** — `SpoolId`, signed amount, mandatory reason.

**Flow** — append `MANUAL_ADJUSTMENT`, source `USER_CONFIRMED`. Re-evaluate confidence.

**The reason is mandatory.** An unexplained adjustment in a ledger is indistinguishable from a
bug, and six months later the user will not remember either.

---

## UC-11 · SpoolOverview *(query)*

Read model for the main view.

**Returns** — per spool: identity, balance, percentage remaining, confidence, location,
anomaly flags, last movement timestamp.

Read-only, no side effects, optimised for display. Kept separate from the command use cases —
a query has no business emitting events or mutating state.

---

## UC-12 · MovementHistory *(query)*

Full audit trail for one spool: every movement, its source, its note, and the running balance
after each entry.

This is the use case that makes the ledger *worth* being a ledger. Without it, immutability is
overhead with no payoff.

---

## Trigger map

| Trigger | Use case | Automatic |
|---|---|---|
| Panel: "New spool" | UC-01 | — |
| RFID appears in slot | UC-02 | ✓ |
| RFID leaves slot | UC-03 | ✓ |
| Job → `FINISHED` | UC-04 | ✓ |
| Job → `CANCELLED` / `FAILED` | UC-05 | ✓ (opens a question, changes nothing) |
| Panel: approve | UC-06 | — |
| Panel: dismiss | UC-07 | — |
| Panel: "Weigh" | UC-08 | — |
| Panel: "Discard" | UC-09 | — |
| Panel: "Adjust" | UC-10 | — |

Exactly three automatic paths change a balance: UC-02 and UC-03 change none, and UC-04 is the
only automatic deduction. Everything else asks first.
