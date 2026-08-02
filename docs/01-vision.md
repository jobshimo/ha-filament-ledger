# 01 — Vision & Scope

## 1.1 The problem, precisely

A 3D printing workflow needs to answer one question reliably: **how much filament is left on
each spool?** Getting it wrong means a print failing at 80% completion because a spool ran
dry, or buying filament that is already on the shelf.

The printer cannot answer this.

### Why the hardware cannot answer it

| Source | What it actually provides | Why it is insufficient |
|---|---|---|
| RFID tag on Bambu spools | Material, colour, temperatures, slicer profile ID | Identity only. Contains no remaining quantity. |
| AMS `remain` field | Odometry estimate: filament length per full spool rotation, assuming 1 kg = 100% | **Not reported at all on A1 / A1 Mini.** On other models, documented failures: stuck at 100% (AMS Lite), returning `-1` (P1S). |
| Slicer estimate | Weight the job *should* consume | Only known before printing. Says nothing about cancellations, failures, or purge waste. |
| `ha-bambulab` per-tray `print_weight` | Grams per AMS tray for the current job | **The slicer's plan, not a measurement.** See below. |

The A1 is the reference hardware for this project, which means the `remain` field is not a
degraded input to be corrected — **it does not exist**. Software accounting is not one design
option among several. It is the only possible approach.

### What `print_weight` actually is

This matters more than any other fact in this document, because an earlier draft of this
design was built on a misreading of it.

`ha-bambulab` exposes a `print_weight` sensor whose extra attributes break the job down per
AMS tray (`AMS 1 Tray 1`, `External Spool`, …). Verified against `pybambu/models.py` on
`main`, that value is populated by one of two paths:

- **LAN** — the sliced `.3mf` is fetched over FTP, its `slice_info` metadata is parsed, and
  the per-filament `used_g` field is mapped onto AMS trays using the MQTT `print.ams_mapping`
  array.
- **Cloud** — the per-AMS `weight` figures come from the Bambu Cloud task data.

Both are **the slicer's prediction for the whole job**. The figure is available *before the
first layer is printed* and does not change according to how the print actually went. The
printer does not weigh anything.

The consequence is precise, and it is not "the design is wrong":

- Deducting automatically when a job reaches `FINISHED` remains correct — a completed job
  executed the plan in full, so plan and reality agree to within flow-rate variance.
- But the reason is **"the plan was carried out"**, not **"the number was measured"**. Every
  place this project reasons about *measured versus estimated* means, precisely, *plan
  fully executed versus plan interrupted partway*. See [ADR-0004](adr/0004-approval-queue-for-estimates.md).

Nothing in this system is ever weighed except by the user, on a kitchen scale. That is why
[UC-08](04-use-cases.md) is called the system's ground truth and this is not.

### Why existing tools do not close the gap

[Spoolman](https://github.com/Donkie/Spoolman) plus [SpoolmanSync](https://github.com/gibz104/SpoolmanSync)
covers inventory and deduction on successful prints. Two gaps make it unsuitable as a base:

1. **Cancellations are unhandled.** SpoolmanSync deducts when prints *complete*. A cancelled
   print consumes real filament that is never deducted, so the recorded balance drifts
   upward — permanently, and in the optimistic direction, which is the dangerous one.
2. **It is another application.** Another container, another database, another UI. A stated
   requirement of this project is a single surface inside Home Assistant.

Spoolman remains valuable as an *optional export target*. See [ADR-0002](adr/0002-reject-spoolman-as-foundation.md).

## 1.2 Goals

**G1 — Unified inventory.** Spools mounted in the AMS and spools in storage are the same kind
of object in different locations, visible in one place.

**G2 — Automatic deduction for completed prints.** Successful prints deduct without
intervention, using the slicer's per-tray figure, because a job that ran to completion
consumed what it was planned to consume.

**G3 — Human approval for interrupted prints.** Cancelled and failed prints produce a
*proposal*, which is applied only after the user confirms or corrects the amount.

**G4 — Manual entry everywhere it matters.** The user can weigh waste on a scale and enter
the real number, overriding any estimate.

**G5 — Physical reconciliation.** Weighing a spool records the delta as a movement, keeping
accumulated drift visible rather than hidden.

**G6 — Discard tracking.** Throwing away a whole spool or part of one is a first-class
operation, not a workaround.

**G7 — Honest uncertainty.** Every balance carries a confidence level. An estimate is never
displayed as if it were a measurement.

**G8 — Full auditability.** For any balance, the complete chain of movements that produced it
can be inspected.

## 1.3 Non-goals

Explicitly out of scope. Each of these has been considered and rejected for v1.

**N1 — Printer control.** This project reads printer state. It does not start, pause, or
cancel prints. `ha-bambulab` already does that; duplicating it adds risk with no benefit.

**N2 — Slicer integration.** No plugin for Bambu Studio or OrcaSlicer.

**N3 — Multi-printer fleet management.** The domain model does not forbid it, but v1 targets a
single printer. Fleet support must not be designed for speculatively.

**N4 — Cost accounting.** Price per spool, cost per print, and currency handling are a
separate concern. The ledger records grams. Money is a later, additive feature.

**N5 — Replacing `ha-bambulab`.** This integration consumes it. It does not reimplement MQTT
transport, authentication, or firmware compatibility.

**N6 — Non-Bambu printers.** The `PrinterGateway` port makes this possible later. No other
implementation is written now.

## 1.4 Constraints

**C1 — Single surface.** All interaction happens inside Home Assistant. No additional
application, container, or UI the user must install and learn separately.

**C2 — Runs on typical HA hardware.** A Raspberry Pi is the assumed floor. The ledger must
stay performant with thousands of movements.

**C3 — Survives restarts and upgrades.** Data is durable and migratable.

**C4 — Tolerates an unavailable printer.** The printer being offline degrades functionality;
it never corrupts data. Inventory management continues to work.

**C5 — Quality is a requirement, not an aspiration.** Hexagonal architecture, SOLID, and a
tested domain layer are explicit constraints from the project owner.

## 1.5 Definition of done for v1

The system is complete when a user can:

1. Register a new spool and see it in storage.
2. Mount it in the AMS and have it recognised automatically by RFID.
3. Run a successful print and see the balance decrease without intervention.
4. Cancel a print, find it in the review queue, correct the amount, approve it, and see the
   balance decrease by the corrected value.
5. Weigh a spool, enter the real weight, and see the discrepancy recorded as a movement.
6. Discard part of a spool with a stated reason.
7. Open any spool and read the full movement history that produced its current balance.

Anything not required by those seven statements is out of scope for v1.

## 1.6 Open questions

Tracked here until resolved. Each is a genuine unknown, not a placeholder.

Four were opened during design. Reading the `ha-bambulab` source closed one outright and
changed the shape of two others — recorded below rather than silently edited away, because
how a question was answered is worth as much as the answer.

| # | Question | Status |
|---|---|---|
| Q1 | Can a user cancellation be distinguished from a system failure? | **Closed by source reading.** See below. |
| Q2 | Does the per-tray figure already include AMS purge waste? | **Open, and now cheaper to answer.** |
| Q3 | Is G-code retrievable over FTP reliably enough to be the primary estimator? | **Open, downgraded.** |
| Q4 | Which population path do the per-tray weight figures need? | **Open, and now the highest risk in the project.** |

### Q1 — closed

The original hypothesis was that `print_error == 0` distinguishes a user cancellation from a
system failure. That inference is unnecessary: `ha-bambulab` already fires `bambu_lab_event`
on the Home Assistant bus with a `type` field of `event_print_finished`,
`event_print_canceled` or `event_print_failed` (`coordinator.py`). The distinction is made
upstream, and maintaining it is upstream's problem rather than ours.

`raw_print_error` and `raw_gcode_state` are still stored verbatim ([02 §2.3](02-domain-model.md)).
Upstream can be wrong, and keeping the raw values is what makes a reclassification possible
later. Storing them costs two columns.

### Q2 — open, and now cheaper

Does the slicer's `used_g` per filament already account for the flush/purge at colour changes?

This no longer needs a printer. It needs **one sliced `.3mf`**: compare the `used_g` values in
its `slice_info` metadata against the totals Bambu Studio displays for the same plate, which
report filament and flush separately. A physical two-colour print with a scale
([09 §9.7](09-testing-strategy.md)) remains the confirmation, not the first step.

### Q3 — open, downgraded

G-code retrieval was believed to be the only route to per-tray numbers. It is not: the
per-tray totals already arrive through `print_weight` for every job.

What parsing the G-code adds is the **per-layer curve** — how consumption accumulated up to
the layer where an interrupted print stopped. That is a genuine accuracy gain for cancelled
prints and nothing more. `GcodeLayerEstimator` is therefore an enhancement, exactly where
[10 — Roadmap](10-roadmap.md) already puts it, and its failure is not a degraded product.

### Q4 — open, highest risk

The per-tray figures only exist once the sliced `.3mf` has been retrieved (LAN) or the cloud
task data has been fetched. Upstream reports that the LAN-mode download fails frequently
enough to leave the sensor unpopulated
([greghesp/ha-bambulab#959](https://github.com/greghesp/ha-bambulab/issues/959)).

An unpopulated figure means a completed print deducts **nothing, silently** — the single
worst outcome available to this system, because it is invisible and it errs optimistically.

Resolved by testing both configurations on the owner's printer and recording the success
rate. Until it is resolved, [03 §3.8](03-architecture.md) requires that a `FINISHED` job with
no usable per-tray figure open a review rather than record zero.

**No implementation decision that depends on an open question may be frozen before the
question is answered.** Where a decision cannot wait, the design must degrade safely.
