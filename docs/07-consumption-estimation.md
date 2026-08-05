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

**What this does *not* buy.** The per-tool totals for a job already arrive without any of
this: `ha-bambulab` parses them out of the sliced `.3mf` and exposes them as attributes of
`print_weight` ([05 §5.8](05-ha-integration.md)). What the G-code adds is the **curve** — how
that total accumulated layer by layer, so an interrupted print can be cut at the right point.

Worth being precise about, because it moves the whole estimator question from *"can we get
numbers at all"* to *"how good is the shape between zero and the total"*. That is why
[Q3](01-vision.md) is an accuracy question rather than a feasibility one, and why
[10 — Roadmap](10-roadmap.md) can leave this to Phase 4 without the product being crippled
until then.

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

1. Fetch the sliced file through `GcodeSource`, an **infrastructure-level** collaborator.
   Deliberately not a method on the domain's `PrinterGateway`: FTP retrieval of a file format
   is not something the domain should be able to name ([02 §2.7](02-domain-model.md)).
2. Parse it once, building a table of cumulative filament per tool per layer.
3. Look up the cumulative value at `layer_reached`.
4. Map tool index to AMS slot using the printer's `ams_mapping`, and return per-slot grams.

Step 4 is not a detail. The slicer numbers its filaments in its own order, and the mapping
onto physical trays is chosen per job — `ams_mapping` is what upstream itself uses to attribute
`used_g` to trays. Assuming filament *n* means tray *n* works right up until the first job
where it does not, and then it deducts from the wrong spool without ever looking wrong.

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

**[ Distribute ]** takes a single measured total and splits it across the involved trays in
the same ratio the estimator produced. The proportion is usually right even when the magnitude
is not — so the user supplies the magnitude and the estimator supplies the shape. Each does
what it is good at.

**[ Load the rest ]** is its sibling, one level down: it splits a *tray's* amount across the
spools that fed it, by subtraction, when a spool emptied mid-print and another finished the
job ([06 §6.3](06-ui-spec.md)). The estimator has nothing to say there — it never knew the
tray was shared — so there is no ratio to supply and the user names the first figure while
the button computes the second. Both are the same idea from the panel's side: the arithmetic
belongs to the machine, and the measurement belongs to the person holding the scale.

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

### What has actually been measured: two observations

**Sample size: two.** One instance, one printer, one operator. Every reconciliation on that
instance was examined; every other one had **zero** consumption between anchors — back-to-back
weighings — so it says nothing about drift and is not counted here.

| Drawn since the previous anchor | Reconciliation delta | Delta as a share of what was drawn |
|---|---|---|
| 220.0 g (22.0% of the reel) | +50.0 g | 22.7% |
| 916.9 g (91.7% of the reel) | −333.1 g | 36.3% |

**Two points are not a curve, and this table is not the one above.** It records what two
reconciliations happened to say on one machine; it is not an accuracy characterisation, it
cannot be turned into one, and nothing here fills a cell in §7.5's table — those stay
qualitative until there is a sample worth quantifying. Both observations sit in the
*no G-code, linear estimator* row, so they say nothing at all about the G-code path.

What two points can support is narrow, and it is the whole of what has been built on them:

- the error is **larger than "flow-rate variance"** suggests — the first observation is already
  22.7% of what was drawn, at a point where the ledger had only 22% of the reel to be wrong
  about;
- the error **keeps growing well past 20% drawn**, which is the point at which the confidence
  ladder used to stop moving.

The second claim is why [02 §2.6](02-domain-model.md) grew a rung above its 20% boundary, and
the worse of these two rates is the bound that rung's position is derived from. That derivation
is stated where the rung is, not here, and it is provisional in exactly the way a figure drawn
from two observations has to be.

---

## 7.6 Purge accounting

**Open question Q2**: does the per-tray figure — the slicer's `used_g` — already include the
flush at colour changes?

- **If yes** — purge is covered, and `PURGE_WASTE` stays a reserved type with no producer.
- **If no** — a separate purge movement is needed per colour change, the slicer's flush volume
  becomes an input the system must read, and Phase 4 writes the producer.

**This must be settled by evidence, not assumption** — and the first step no longer needs a
printer. Slice a two-colour plate and compare the `used_g` values in the `.3mf`'s `slice_info`
metadata against the filament and flush totals Bambu Studio reports for that plate. The
physical two-colour print with a scale ([09 §9.7](09-testing-strategy.md)) then confirms it.

`PURGE_WASTE` is reserved in the model and in the first migration precisely so that neither
answer requires a schema change. It has **no producer in v1**, and
[02 §2.2](02-domain-model.md) says so plainly rather than implying a manual-entry path that
does not exist.

---

## 7.7 Cancellation classification

**Q1 is closed**, and how it closed is worth keeping.

The working hypothesis was that `print_error == 0` indicates a user cancellation and a
non-zero value indicates the printer stopped the print. It was unverified and undocumented,
and a physical procedure was written to test it.

It never needed testing. `ha-bambulab` already fires distinct events —
`event_print_canceled` and `event_print_failed` — on the Home Assistant bus. The classification
is made upstream, by code that reads the MQTT stream for a living, and consuming it is both
more accurate and someone else's maintenance burden.

**The cheapest answer to a hard question is often that somebody already answered it.** A day
spent reading a dependency's source removed a physical test procedure from the plan and made
a provisional rule unnecessary.

The design's tolerance for being wrong stays exactly as it was, because upstream can be wrong
too:

1. `PrintJob` stores `raw_print_error` and `raw_gcode_state` **verbatim**.
2. `ReviewReason` includes `UNCLASSIFIED` as a legitimate value, not an error state.
3. The review UI shows the classification as a suggestion and lets the user change it.
4. If the classification ever turns out to be wrong, it can be recomputed retroactively for
   jobs already stored — *because the raw values were kept*.

Storing raw inputs alongside derived conclusions is what makes a wrong conclusion recoverable.
Storing only the conclusion would leave every job recorded before the discovery permanently
misclassified.
