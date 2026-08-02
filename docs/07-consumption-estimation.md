# 07 — Consumption Estimation

When a print completes successfully, the printer reports what it used. Nothing needs
estimating.

When a print is **cancelled or fails**, that number never arrives — and the filament was still
consumed. This document covers how much is inferred, how accurately, and how the uncertainty
is communicated instead of hidden.

---

## 7.1 Why the obvious approach is not good enough

The intuitive formula:

```
consumed ≈ progress × total_estimated_weight
```

It is wrong in ways that matter, and all of them point the same direction.

**Consumption is not linear in layer count.**

- The first layers are denser — brims, rafts, and solid bottom layers.
- Infill density varies by height. A model with a solid base and a sparse body consumes far
  more per layer at the start.
- Solid top layers concentrate material at the end.

**Purge is the largest distortion.** On the AMS Lite, every colour change flushes filament to
clear the nozzle. In a multi-colour print this can be a substantial fraction of total
consumption, and it is spent *at colour changes* — not spread evenly. A two-colour print
cancelled at 30% may have passed through most of its colour changes, or none.

**Progress itself is ambiguous.** `mc_percent` tracks estimated time, not material. A print
with a slow, sparse top section reports progress that diverges sharply from material used.

For a single-colour print, linear estimation is a reasonable approximation. For multi-colour —
which is the entire reason to own an AMS — it can be badly wrong, and always in the direction
of under-reporting consumption. **An inventory system that errs optimistically is the failure
mode you least want**: it tells you there is enough filament when there is not.

---

## 7.2 The better source: the G-code itself

The printer stores the sliced file, and the A1 in LAN mode exposes it over FTP.

Bambu Studio and OrcaSlicer emit per-layer markers, and both record filament usage per
extruder. Parsing the file and accumulating extrusion up to the layer where the print stopped
yields consumption that is **computed, not estimated** — the actual commanded extrusion, per
tool, including purge, up to the exact stopping point.

That difference — accumulating what was commanded rather than scaling a total — is what
removes every non-linearity described above at once.

---

## 7.3 Strategy pattern

Two implementations behind one port. This is where Open/Closed earns its place in this
project.

```
ConsumptionEstimator  (port, domain)
    estimate(job: PrintJob) -> {SlotIndex: Grams}
    raises EstimationUnavailable

├── GcodeLayerEstimator      preferred · accurate
├── LinearProgressEstimator  fallback · approximate
└── CompositeEstimator       tries each in order
```

`CompositeEstimator` walks its list and returns the first success. Adding a third strategy
later means writing a class and adding it to the list — no existing file is modified, and no
use case knows the list grew.

Every implementation obeys the same contract: return per-slot grams, or raise
`EstimationUnavailable`. **None returns `None` to signal failure, and none invents a zero.**
That is Liskov substitution in practice — a caller cannot be broken by which strategy happens
to run.

### `GcodeLayerEstimator`

1. Fetch the G-code for the job through `PrinterGateway.fetch_gcode`.
2. Parse it once, building a table of cumulative filament per tool per layer.
3. Look up the cumulative value at `layer_reached`.
4. Return per-slot grams, mapping tool index to AMS slot.

Reports `EstimatorKind.GCODE_LAYER` so the UI can label it *"layer-accurate"*.

**Caching.** Parsing runs once per job and is cached, because a review may be opened, viewed
and approved across separate sessions, and re-parsing on each is waste.

**Failure modes**, all of which raise `EstimationUnavailable` rather than degrade silently:
FTP unreachable, file already deleted, unrecognised dialect, `layer_reached` unknown.

### `LinearProgressEstimator`

The fallback. Uses the best progress signal available, in order of preference:

1. `layer_reached / total_layers` — closest available proxy for material.
2. `mc_percent` — time-based, weakest.

Multiplied by the slicer's total estimated weight per tool.

Reports `EstimatorKind.LINEAR_PROGRESS`, which the UI labels *"approximate"*.

**A known-imprecise estimate presented honestly is more useful than a precise-looking one that
is wrong.** The label is not a disclaimer; it is data the user needs to decide whether to
reach for the scale.

---

## 7.4 The scale always wins

No estimator output is ever written to the ledger unreviewed. The chain is:

```
estimator → PendingReview (proposal) → user edits or weighs → movement
```

The review UI ([06 §6.3](06-ui-spec.md)) puts a weighing field beside every estimate for
exactly this reason. Weighing the failed part and the purge is the ground truth, and the
system's job is to make entering that number faster than trusting the estimate.

**[ Distribute ]** takes a single measured total and splits it across the involved spools in
the same ratio the estimator produced. The proportion is usually right even when the magnitude
is not — so the user supplies the magnitude and the estimator supplies the shape. Each does
what it is good at.

---

## 7.5 Accuracy expectations

Stated plainly so nobody is surprised later.

| Scenario | Estimator | Expected error |
|---|---|---|
| Single colour, G-code available | G-code layer | Low — dominated by flow-rate variance |
| Multi colour, G-code available | G-code layer | Low — purge is in the G-code |
| Single colour, no G-code | Linear | Moderate — non-linear infill |
| Multi colour, no G-code | Linear | **High — purge unaccounted for** |
| Failure in the first layers | Either | Absolute error small; relative error large, and it does not matter |

The bottom-right cell is the one that justifies the review queue existing. When that case
arises, the correct behaviour is to weigh — and the UI says so.

These figures are **deliberately unquantified**. Publishing a "±5%" before measuring anything
would be a fabricated number in a document about not fabricating numbers. They get filled in
from real data during Phase 4 ([10 — Roadmap](10-roadmap.md)).

---

## 7.6 Purge accounting

**Open question Q2**: does the per-tray weight reported by `ha-bambulab` for a *successful*
print already include purge?

- **If yes** — purge is covered, and `PURGE_WASTE` remains available for manual entry only.
- **If no** — a separate purge movement is needed per colour change, and the slicer's flush
  volume becomes an input the system must read.

**This must be settled by measurement, not assumption.** The test is defined in
[10 — Roadmap](10-roadmap.md) Phase 4: print a two-colour job, weigh both spools before and
after, compare the real total against what the integration reported. The `PURGE_WASTE`
movement type exists in the model from day one so that neither answer requires a schema
change.

---

## 7.7 Cancellation classification

**Open question Q1**: can a user cancellation be distinguished from a system failure?

Working hypothesis: `print_error == 0` indicates the user stopped the print; a non-zero value
indicates the printer stopped it. **Unverified, and not documented by any public source.**

The design does not depend on the answer:

1. `PrintJob` stores `raw_print_error` and `raw_gcode_state` **verbatim**.
2. `ReviewReason` includes `UNCLASSIFIED` as a legitimate value, not an error state.
3. The review UI shows the classification as a suggestion and lets the user change it.
4. When the rule is confirmed, it can be applied retroactively to jobs already stored —
   *because the raw values were kept*.

Storing raw inputs alongside derived conclusions is what makes a wrong conclusion recoverable.
Storing only the conclusion would make Q1 unanswerable after the fact, and every job recorded
before the answer would be permanently misclassified.
