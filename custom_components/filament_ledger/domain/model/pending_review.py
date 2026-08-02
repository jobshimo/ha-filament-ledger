"""The approval queue's item. This entity is the mechanism behind principle #1.

A review in `PENDING` has produced no movements — the balance is untouched until a person
decides. Both estimate and slot→spool resolution are **frozen when the review opens**: a
review may sit in the queue for days while spools are swapped in and out of the machine,
and resolving at approval time would deduct a cancelled Tuesday print from whatever happens
to be in slot 2 on Friday (docs/04-use-cases.md UC-05).

Amounts are keyed by *slot*, not by spool. The case that most needs a review is a slot that
reported usage with no spool mounted in it — there is no `SpoolId` to key that entry with.
A line says the only honest thing available: *"slot 3 used 12 g and I do not know which
spool was in it"* — and lets the user supply the missing half (docs/02-domain-model.md §2.3).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from ..error import InvalidValueError, ReviewAlreadyResolvedError, UnresolvedSlotError
from ..value.grams import Grams
from ..value.identifiers import PrintJobId, ReviewId, SlotIndex, SpoolId, new_review_id
from ..value.review import EstimatorKind, ReviewReason, ReviewState


@dataclass(frozen=True, slots=True)
class ReviewLine:
    """One slot's frozen half-facts: the estimated amount, and the spool that was mounted.

    `spool_id` of `None` is a fact worth recording, not an error — no spool was mounted
    when the review opened, and the approval flow is where the user supplies the answer.
    """

    slot: SlotIndex
    estimated: Grams
    spool_id: SpoolId | None

    def __post_init__(self) -> None:
        # Amounts on a review are consumption magnitudes; the sign is applied when a
        # movement is written. A negative magnitude has no physical reading.
        if self.estimated.is_negative:
            msg = f"slot {self.slot} cannot have a negative estimate, got {self.estimated}"
            raise InvalidValueError(msg)


@dataclass(frozen=True, slots=True)
class PendingReview:
    id: ReviewId
    job_id: PrintJobId
    reason: ReviewReason
    lines: tuple[ReviewLine, ...]
    estimator_used: EstimatorKind
    state: ReviewState
    opened_at: datetime
    confirmed_usage: dict[SlotIndex, Grams] | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None

    def __post_init__(self) -> None:
        slots = [line.slot for line in self.lines]
        if len(set(slots)) != len(slots):
            msg = f"review {self.id} carries the same slot twice"
            raise InvalidValueError(msg)
        # A pending review with a resolution timestamp — or a resolved one without — is a
        # record that contradicts itself, and both halves would be believed by somebody.
        if (self.state is ReviewState.PENDING) != (self.resolved_at is None):
            msg = f"state {self.state} and resolved_at {self.resolved_at} contradict each other"
            raise InvalidValueError(msg)
        if self.state is ReviewState.PENDING and self.confirmed_usage is not None:
            msg = "a pending review has confirmed nothing yet"
            raise InvalidValueError(msg)

    # -- derived -----------------------------------------------------------------------

    @property
    def estimated_usage(self) -> dict[SlotIndex, Grams]:
        return {line.slot: line.estimated for line in self.lines}

    @property
    def slot_resolution(self) -> dict[SlotIndex, SpoolId | None]:
        return {line.slot: line.spool_id for line in self.lines}

    @property
    def is_resolved(self) -> bool:
        return self.state.is_resolved

    @property
    def confirmed_charges(self) -> list[tuple[SlotIndex, Grams, SpoolId]]:
        """Every non-zero confirmed amount with the spool it charges, sorted by slot.

        Empty until approval. Zero amounts never appear — a zero movement records nothing
        and only adds noise, so `ApproveReview` must not even be tempted to write one. The
        approval invariant guarantees every non-zero amount has a spool, which is what
        makes the narrowed `SpoolId` honest here.
        """
        if self.confirmed_usage is None:
            return []
        resolution = self.slot_resolution
        return [
            (slot, amount, spool_id)
            for slot, amount in sorted(self.confirmed_usage.items())
            if not amount.is_zero and (spool_id := resolution.get(slot)) is not None
        ]

    # -- transitions -------------------------------------------------------------------

    def _guard_pending(self) -> None:
        if self.is_resolved:
            msg = (
                f"review {self.id} is already {self.state}; resolving it again would "
                f"deduct twice, and a duplicate ledger entry is indistinguishable from "
                f"a real one after the fact"
            )
            raise ReviewAlreadyResolvedError(msg)

    def approved(
        self,
        *,
        at: datetime,
        amounts: Mapping[SlotIndex, Grams] | None = None,
        assignments: Mapping[SlotIndex, SpoolId] | None = None,
        note: str | None = None,
    ) -> PendingReview:
        """Steps 2–4 and 6 of UC-06: merge the user's corrections, refuse the unresolvable.

        User-supplied `amounts` override the frozen estimates; `assignments` override the
        frozen resolutions. Both may only reference slots the review froze — the queue
        card renders exactly these rows, so an override for any other slot is a caller
        bug, not a decision.

        A slot with a non-zero final amount and no spool blocks the whole approval. The
        alternatives are inventing a spool or dropping a real consumption on the floor,
        and the second is worse because it leaves no trace. The user is one dropdown away
        from the answer; the system is not.

        The returned review records the amounts and resolutions *actually used* — the
        decision, not the proposal. The proposal's provenance survives in
        `estimator_used`, and the untouched estimates survive per line.
        """
        self._guard_pending()
        frozen = self.estimated_usage
        for name, overrides in (("amount", amounts), ("assignment", assignments)):
            unknown = sorted(set(overrides or {}) - set(frozen))
            if unknown:
                msg = f"{name} supplied for slot(s) {unknown} this review does not cover"
                raise InvalidValueError(msg)
        for slot, amount in (amounts or {}).items():
            if amount.is_negative:
                msg = f"slot {slot} cannot be confirmed at a negative {amount}"
                raise InvalidValueError(msg)

        final_amounts = {**frozen, **(amounts or {})}
        final_spools: dict[SlotIndex, SpoolId | None] = {
            **self.slot_resolution,
            **(assignments or {}),
        }
        unresolved = sorted(
            slot
            for slot, amount in final_amounts.items()
            if not amount.is_zero and final_spools[slot] is None
        )
        if unresolved:
            msg = (
                f"slot(s) {[slot.value for slot in unresolved]} carry a non-zero amount "
                f"and no spool; assign a spool, zero the amount, or dismiss"
            )
            raise UnresolvedSlotError(msg)

        return replace(
            self,
            state=ReviewState.APPROVED,
            resolved_at=at,
            resolution_note=note,
            confirmed_usage=final_amounts,
            lines=tuple(
                ReviewLine(
                    slot=line.slot, estimated=line.estimated, spool_id=final_spools[line.slot]
                )
                for line in self.lines
            ),
        )

    def dismissed(self, *, at: datetime, note: str | None = None) -> PendingReview:
        """UC-07: resolve without recording consumption.

        A recorded decision with a timestamp and a reason — not a deletion. Nothing here
        touches amounts or resolutions, because nothing was confirmed.
        """
        self._guard_pending()
        return replace(self, state=ReviewState.DISMISSED, resolved_at=at, resolution_note=note)


def open_review(
    *,
    job_id: PrintJobId,
    reason: ReviewReason,
    lines: tuple[ReviewLine, ...],
    estimator_used: EstimatorKind,
    opened_at: datetime,
) -> PendingReview:
    """Build a new review in `PENDING`, generating its identity.

    Lines are stored sorted by slot so that every reader — the queue card, the approval
    loop, the persisted JSON — sees the same order without each imposing its own.
    """
    return PendingReview(
        id=new_review_id(),
        job_id=job_id,
        reason=reason,
        lines=tuple(sorted(lines, key=lambda line: line.slot)),
        estimator_used=estimator_used,
        state=ReviewState.PENDING,
        opened_at=opened_at,
    )
