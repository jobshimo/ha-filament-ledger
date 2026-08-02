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

The A1 is the reference hardware for this project, which means the `remain` field is not a
degraded input to be corrected — **it does not exist**. Software accounting is not one design
option among several. It is the only possible approach.

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

**G2 — Automatic deduction for measured consumption.** Successful prints deduct without
intervention, because the printer reports actual per-tray weight.

**G3 — Human approval for estimated consumption.** Cancelled and failed prints produce a
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

| # | Question | Impact | How it gets resolved |
|---|---|---|---|
| Q1 | Does `print_error == 0` reliably distinguish a user cancellation from a system failure? | Determines whether cancellation reason can be auto-classified or must always be user-set | Instrument both fields, capture real cancellations and real failures, compare |
| Q2 | Does the per-tray weight reported by `ha-bambulab` already include AMS purge waste? | Determines whether purge needs separate accounting or is already covered | Print a two-colour job, compare reported total against a scale measurement |
| Q3 | Is G-code retrievable via FTP on the A1 reliably enough to base the primary estimator on it? | Determines whether `GcodeLayerEstimator` is the default or an enhancement | Attempt retrieval across several jobs, measure success rate and latency |
| Q4 | Which population path do the `print weight` sensors need — Bambu Cloud, or LAN with FTP model data enabled? | Determines the required user setup and documentation | Test both configurations against the owner's actual printer |

**No implementation decision that depends on these questions may be frozen before the
question is answered.** Where a decision cannot wait, the design must degrade safely — see
[07 — Consumption Estimation](07-consumption-estimation.md) for how the estimator handles Q3.
