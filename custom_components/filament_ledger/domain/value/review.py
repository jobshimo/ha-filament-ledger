"""The review queue's vocabulary: why a review exists, where it stands, and who estimated.

Grouped in one module the way `movement_type.py` groups the ledger's vocabulary — these
three enums are only ever read together, on a `PendingReview`.
"""

from __future__ import annotations

from enum import StrEnum


class ReviewReason(StrEnum):
    """Why the system could not settle a job's consumption on its own.

    `CANCELLED` and `FAILED` are taken directly from the `ha-bambulab` event type
    (`event_print_canceled` / `event_print_failed`), never inferred from `print_error` —
    the classification is made upstream, by code that reads the MQTT stream for a living
    (docs/04-use-cases.md UC-05). Those two describe *why the print stopped*;
    `UNMAPPED_USAGE` does not — the print finished, the inventory was incomplete.
    """

    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    # The job finished, but a slot's consumption cannot be attributed to a spool.
    UNMAPPED_USAGE = "UNMAPPED_USAGE"
    # The job ended without a recognisable event. A legitimate value, not an error state —
    # upstream can be wrong, and the raw fields on the job keep reclassification possible.
    UNCLASSIFIED = "UNCLASSIFIED"


class ReviewState(StrEnum):
    """A review is open exactly once and terminal forever after.

    Both terminal states are *recorded decisions* with a timestamp, not deletions. The
    queue is an audit trail, not an inbox to be emptied.
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DISMISSED = "DISMISSED"

    @property
    def is_resolved(self) -> bool:
        return self is not ReviewState.PENDING


class EstimatorKind(StrEnum):
    """Which strategy produced a review's estimated amounts.

    Stored on every review and shown on every review card — *"estimated from progress ·
    approximate"* — because a known-imprecise estimate presented honestly is more useful
    than a precise-looking one that is wrong (docs/07-consumption-estimation.md §7.3).

    `NONE` deliberately does double duty. It is both a provenance label and the explicit
    no-consumption-data flag from UC-04/UC-05: a review carrying `NONE` holds figures no
    estimator produced — either the caller already knew the amounts (the printer reported
    them, and estimating over a report would be a downgrade), or estimation was unavailable
    and the zero amounts are a placeholder awaiting the user, **not a claim that nothing
    was consumed**. Encoding the flag as a member keeps the schema unchanged, and the
    accompanying amounts disambiguate the two readings: reported figures are non-zero,
    a no-data placeholder is all zeros.

    `GCODE_LAYER` arrives in Phase 4 with the estimator that reports it. Adding the member
    before its producer would let a review claim an accuracy nothing can deliver yet.
    """

    LINEAR_PROGRESS = "LINEAR_PROGRESS"
    NONE = "NONE"
