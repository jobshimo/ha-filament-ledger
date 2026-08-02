# ADR-0004 — Estimated consumption requires human approval

**Status:** Accepted
**Date:** 2026-08-02

## Context

A print job arrives with a **plan**: the slicer's per-tray filament figure, surfaced by
`ha-bambulab` and dissected in [01 §1.1](../01-vision.md). It is not a measurement — nothing
in this system weighs anything except the user, on a kitchen scale.

What separates the two cases is not how the number was obtained. It is **whether the plan
ran**.

- A print that reaches `FINISHED` executed its plan in full. Plan and reality agree to within
  flow-rate variance.
- A cancelled or failed print stopped partway, and how far through the plan it got can only be
  inferred. [07](../07-consumption-estimation.md) shows that inference is unreliable — badly
  so for multi-colour prints without G-code, where purge lands at colour changes rather than
  spreading evenly.

> **Correction.** An earlier version of this ADR argued the asymmetry from *measured versus
> estimated*, on the belief that the printer weighed each tray. Reading `pybambu/models.py`
> showed the figure comes from the sliced `.3mf` or from Bambu Cloud task data — the slicer's
> prediction, available before the first layer.
>
> The decision below survives unchanged; its justification did not, and it is rewritten rather
> than quietly patched. An ADR whose reasoning is wrong is worse than no ADR, because it will
> be cited.

Three options:

1. **Deduct the estimate automatically.** Balances stay current with no user effort.
2. **Ignore cancelled prints.** Deduct nothing.
3. **Propose the estimate; require confirmation.**

## Decision

Option 3. Cancelled and failed prints create a `PendingReview` that **changes no balance** until
the user approves it, with every amount editable and a field for entering a weighed value.

## Rationale

**Automatic deduction of a bad number is worse than no deduction.** Option 1 silently writes a
figure the system knows to be unreliable, and once written it is indistinguishable from a
measured one. The balance becomes a mixture of measurements and guesses with no way to tell
them apart — which destroys the audit trail that [ADR-0001](0001-append-only-ledger.md) exists
to provide.

**Ignoring them guarantees drift in the dangerous direction.** Option 2 is what SpoolmanSync
does today ([ADR-0002](0002-reject-spoolman-as-foundation.md)). Every cancellation inflates the
balance permanently, and the error is always optimistic — the system reports filament that is
not there. For an inventory whose purpose is avoiding a spool running out mid-print, that is
the worst possible bias.

**The user has information the system does not.** They watched the print fail. They can hold
the failed part. They can put it on a scale. The estimate is the system's best contribution;
the correction is theirs. A design that does not ask is discarding the better source of truth.

**Cancellations are infrequent.** This costs a few interactions a week, not a daily chore. The
friction is proportionate to how rarely it applies — which is what makes it acceptable to
insist on.

## Why this does not apply to successful prints

Deliberately asymmetric. Successful prints deduct automatically, with no review.

Not because the number is measured — it is not. Because **the plan was carried out**, which
makes the remaining uncertainty flow-rate variance: the same order of error a kitchen scale
introduces, and far smaller than the uncertainty in "how much of a 209-layer print had run
when it stopped at layer 71".

And because **an approval step applied to reliable data trains the user to approve without
reading**. An approval reflex is worse than no approval step: it produces the appearance of
oversight with none of the substance, and it would compromise the reviews that genuinely need
attention.

Approval is reserved for consumption that is actually uncertain. That is what keeps it
meaningful.

**One exception, and it runs the other way.** If a print reaches `FINISHED` but its per-tray
figure never arrived — a real possibility in LAN mode, [Q4](../01-vision.md) — there is no
plan to trust and nothing to deduct. That job opens a review too
([UC-04](../04-use-cases.md) step 2). The rule is not "finished prints deduct"; it is
**"deduct only when the plan is known and known to have completed"**. A missing figure fails
the first half, and treating it as zero would be the one error this system could never
detect.

## Consequences

**Accepted costs**

- Balances are stale between a cancellation and its approval. Visible in the UI — a pending
  review is shown, so the staleness is disclosed rather than hidden.
- A user who ignores the queue accumulates unrecorded consumption. Mitigated by the sidebar
  badge, the `sensor.fl_pending_reviews` entity, and the `filament_ledger_review_opened` event
  for notifications.
- More UI surface: the review queue is an entire view ([06 §6.3](../06-ui-spec.md)).

**Gained**

- Every gram in the ledger came from a plan known to have completed, or from a human. No third
  category — nothing enters on the strength of a guess about how far an interrupted print got.
- `MovementSource` cleanly separates the two, which is what makes the confidence model
  ([02 §2.6](../02-domain-model.md)) computable.
- Weighing waste becomes a natural part of the flow rather than an afterthought, so the most
  accurate possible number is also the easiest to enter.
- Estimator quality stops being critical. A poor estimate is a starting value the user
  corrects, not a wrong number silently committed — which is what allowed
  [10 — Roadmap](../10-roadmap.md) to defer the accurate estimator to Phase 4 without harm.

## Related

The dismissal path ([UC-07](../04-use-cases.md)) exists so the queue is never a burden. A print
that failed on the first layer is dismissed in one tap — recorded as a decision, with a
timestamp, not deleted.
