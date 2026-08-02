# 04 — Use Cases

Every operation the system performs. One class each, one public method each, each a
transaction boundary.

Format: trigger, preconditions, flow, postconditions, failure modes.

---

## UC-01 · RegisterSpool

Adds a spool to inventory.

**Trigger** — user action in the panel, or HA service `filament_ledger.register_spool`.

**Input** — material, colour, vendor, opening weight, **core weight** (required; the caller
resolves the configured default before calling — see [02 §2.8](02-domain-model.md)),
`confirm_duplicate_tag` (defaults to false), optional label, optional `tag_uid`, optional
initial location (defaults to storage).

**Preconditions**
- Opening weight > 0.
- Core weight is present. It is never defaulted inside the use case, because a silent zero
  corrupts every future reconciliation.
- If `tag_uid` is supplied and already belongs to a non-discarded spool, `confirm_duplicate_tag`
  must be true. Bambu tags identify a batch, not a unit, so duplicates are legal — but they
  must be deliberate, never accidental. The caller sees the conflicting spool and says yes.

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
- **For the automatic trigger only:** the `auto_mount_on_rfid` option is enabled. When it is
  disabled, RFID detection raises `SpoolDetected` — informational, no location change — and
  the AMS view offers a manual **[ Mount ]** button for that slot. The setting exists because
  some users keep spools registered to a shelf and load them briefly; silently rewriting
  their locations is not a service.

**Flow**
1. Resolve the spool — by id, or by tag lookup.
2. **If tag resolution finds nothing: raise `UnknownSpoolDetected` and stop.** No spool is
   created.
3. **If tag resolution finds more than one non-discarded spool: raise `AmbiguousTagDetected`
   with the candidates and stop.** The slot stays unmounted.
4. If the slot holds a different spool, unmount it to storage (UC-03).
5. Set location to `AmsSlot(n)`.
6. Raise `SpoolMounted`.

**Postconditions** — spool is in the slot; at most one spool per slot.

**Failures** — spool discarded; slot index out of range; tag ambiguous.

> Steps 2 and 3 are the important ones, and they refuse in opposite directions for the same
> reason. Auto-creating a spool means inventing an opening weight. Auto-picking between two
> spools that share a tag means guessing which physical object is in the machine — and if the
> guess is wrong, every print from now on drains a spool sitting on a shelf while the one
> actually loaded runs out with no warning.
>
> An invented number in a ledger is worse than a missing one. It looks authoritative.

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
- Job has not already been recorded — `consumption_recorded` on the job is the single
  idempotency guard. It is set in the same transaction as the movements it covers.

**Flow**
1. Verify idempotency; abort silently if already recorded.
2. **If no per-tray usage is available at all, open a review (UC-05) with reason
   `UNMAPPED_USAGE`, a zero estimate and an explicit flag, mark the job recorded, and stop.**
   Deduct nothing.
3. For each slot with non-zero usage:
   a. Resolve the mounted spool. If none, collect the slot as unresolved and continue.
   b. Append `PRINT_CONSUMPTION` with source `AUTOMATIC`.
4. Re-evaluate confidence for affected spools.
5. Run anomaly detection.
6. Raise `MovementRecorded` per movement; `SpoolDepleted` where balance crossed zero.
7. If any slots were unresolved, open a review (UC-05) with reason `UNMAPPED_USAGE` covering
   only those slots, carrying their amounts and a null resolution.
8. Mark the job recorded.

**Postconditions** — balances reduced for every resolved slot; every unresolved or unavailable
amount is sitting in a review; job marked recorded exactly once.

**Failures** — no mounted spool for a consuming slot, and no per-tray figure at all. Both
degrade to a review; neither throws, and neither is treated as zero.

**Why step 2 exists.** The per-tray figure only materialises once the sliced `.3mf` has been
retrieved, and that retrieval is known to fail in LAN mode ([Q4](01-vision.md)). A missing
figure is not a figure of zero. Recording zero for a print that consumed 84 g is a silent,
optimistic lie, and it is the only failure in this system that leaves no trace at all.

**Why automatic here and nowhere else.** Not because the number is measured — it is not. It
is the slicer's plan, and [01 §1.1](01-vision.md) sets out exactly what that means. It is
automatic because **the job ran to completion, so the plan was carried out in full**. Plan and
reality agree to within flow-rate variance, which is the same variance a scale would find.

An interrupted print is different in kind: nobody knows how much of the plan executed. That is
the whole distinction, and it is the one [ADR-0004](adr/0004-approval-queue-for-estimates.md)
rests on.

Requiring approval here anyway would train the user to approve without reading — and an
approval reflex is worse than no approval step at all.

---

## UC-05 · OpenPendingReview

Creates an approval item for anything the system cannot settle on its own.

**Trigger** — job reaches `CANCELLED` or `FAILED`; or UC-04 found unresolved slots or no
usable per-tray figure.

**Input** — `PrintJobId`, the originating event type, raw `gcode_state`, raw `print_error`,
and — when called from UC-04 — the slot amounts already computed.

**Flow**
1. Classify the reason:
   - `CANCELLED` / `FAILED` — taken directly from the `ha-bambulab` event type
     (`event_print_canceled` / `event_print_failed`). See [Q1](01-vision.md): this is no
     longer inferred from `print_error`.
   - `UNMAPPED_USAGE` — the job finished, but a slot's consumption cannot be attributed.
   - `UNCLASSIFIED` — the job ended without a recognisable event. A legitimate value, not an
     error state.

   `raw_gcode_state` and `raw_print_error` are stored verbatim regardless, so a
   reclassification stays possible if upstream turns out to be wrong.
2. Ask the `ConsumptionEstimator` for per-slot grams. *(Skipped when UC-04 supplied amounts.)*
3. Resolve each involved slot to its currently mounted spool, and **freeze that resolution on
   the review.** A slot with no mounted spool is frozen as unresolved — that is a fact worth
   recording, not an error.
4. Create `PendingReview` in `PENDING`, recording which estimator ran.
5. Raise `ReviewOpened`.

**Postconditions** — a pending review exists. **No movement. No balance changed.**

**Failures** — estimation unavailable: the review is still created, with a zero estimate and
an explicit flag. The user is asked; nothing is guessed.

> Freezing the resolution in step 3 matters more than it looks. A review may sit in the queue
> for days while spools are swapped in and out of the machine. Resolving at approval time
> would deduct a cancelled Tuesday print from whatever happens to be in slot 2 on Friday.
>
> The system's job here is to *notice* and *ask*. Not to decide.

---

## UC-06 · ApproveReview

Converts a review into ledger entries.

**Trigger** — user approves in the panel, or service call.

**Input** — `ReviewId`, optional per-slot corrected amounts, optional per-slot spool
assignments (to resolve slots the review froze as unresolved), optional note.

**Preconditions**
- Review is `PENDING`. **Idempotency is enforced here.** A review already resolved cannot be
  resolved again — otherwise a double-click deducts twice, and a duplicate ledger entry is
  indistinguishable from a real one after the fact.
- **Every slot with a non-zero final amount resolves to a spool** — either from the frozen
  resolution or from an assignment supplied now.

**Flow**
1. Load the review; reject if not `PENDING`.
2. Determine final amounts per slot: user-supplied values override estimates.
3. Determine final resolutions per slot: supplied assignments override the frozen ones.
4. **Reject if any slot has a non-zero amount and no spool.** Nothing is written.
5. For each non-zero amount, append `ESTIMATED_CONSUMPTION` with source `USER_CONFIRMED`,
   carrying both `job_id` and `review_id`.
6. Mark the review `APPROVED` with a timestamp, the note, and the resolutions actually used.
7. Re-evaluate confidence — approving an estimate degrades the affected spools toward `LOW`.
8. Run anomaly detection.
9. Raise `ReviewResolved` and `MovementRecorded`.

**Postconditions** — balances reduced by *confirmed* amounts; review terminal; every movement
traceable back to the decision that created it.

**Failures** — review not found; already resolved; negative amount supplied; an unresolved
slot carrying a non-zero amount.

> Step 4 refuses rather than rounds. The alternatives are inventing a spool or dropping a
> real consumption on the floor, and the second is worse because it leaves no trace. The user
> is one dropdown away from the answer; the system is not.

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
| RFID appears in slot, auto-mount on | UC-02 | ✓ |
| RFID appears in slot, auto-mount off | `SpoolDetected` only | ✓ (changes nothing) |
| RFID resolves to several spools | `AmbiguousTagDetected` | ✓ (asks, changes nothing) |
| RFID leaves slot | UC-03 | ✓ |
| `event_print_finished` | UC-04 | ✓ |
| `event_print_finished`, slot unresolved or figure missing | UC-04 → UC-05 | ✓ (opens a question) |
| `event_print_canceled` / `event_print_failed` | UC-05 | ✓ (opens a question, changes nothing) |
| Panel: approve | UC-06 | — |
| Panel: dismiss | UC-07 | — |
| Panel: "Weigh" | UC-08 | — |
| Panel: "Discard" | UC-09 | — |
| Panel: "Adjust" | UC-10 | — |

**Exactly one automatic path changes a balance: UC-04.** UC-02 and UC-03 move spools without
moving grams, and UC-05 opens a question without answering it. Everything else asks first.
