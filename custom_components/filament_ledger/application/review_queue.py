"""UC-05 · OpenPendingReview, UC-06 · ApproveReview and UC-07 · DismissReview.

The queue is how the system refuses to guess. Anything it cannot settle on its own —
an interrupted print, usage on a tray nobody mapped — becomes a pending item, and nothing
leaves the queue without a recorded decision. Opening changes no balance; only approval
writes to the ledger, and dismissal writes nothing ever.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.error import (
    EstimationUnavailableError,
    InvalidValueError,
    ReviewAlreadyPendingError,
    SpoolDiscardedError,
    SpoolReconciledSinceReviewError,
)
from ..domain.event import (
    AnomalyDetected,
    DomainEvent,
    EventPublisher,
    MovementRecorded,
    ReviewOpened,
    ReviewResolved,
    SpoolDepleted,
)
from ..domain.model.movement import record
from ..domain.model.pending_review import ReviewCharge, ReviewLine, open_review
from ..domain.model.print_job import PrintJob
from ..domain.model.spool import Spool
from ..domain.port.clock import Clock
from ..domain.port.consumption_estimator import ConsumptionEstimator
from ..domain.port.repositories import (
    MovementRepository,
    PrintJobRepository,
    ReviewRepository,
    SpoolRepository,
)
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.service.anomaly_detector import AnomalyDetector
from ..domain.service.balance_calculator import balance
from ..domain.value.grams import Grams
from ..domain.value.identifiers import ReviewId, SpoolId, TrayRef
from ..domain.value.location import AmsSlot
from ..domain.value.movement_type import MovementSource, MovementType
from ..domain.value.review import EstimatorKind, ReviewReason, ReviewState
from .errors import ReviewNotFoundError, SpoolNotFoundError


@dataclass(frozen=True, slots=True)
class OpenPendingReviewCommand:
    """The whole `PrintJob` travels in the command, not just its id.

    That is what makes the UC-05→schema ordering impossible to get wrong: the caller
    cannot open a review for a job it never had, and the use case saves the job before
    the review inside one unit — so `pending_review.job_id`'s foreign key always has a
    row to point at.

    `amounts` is UC-04's channel: when the finished job already carries per-tray figures,
    estimation is skipped — the printer reported, and estimating over a report would
    replace a fact with a guess. `reason` comes classified from the caller, because the
    classification is read off the `ha-bambulab` event type at the gateway, and this layer
    has no event to read.
    """

    job: PrintJob
    reason: ReviewReason
    amounts: dict[TrayRef, Grams] | None = None


@dataclass(frozen=True, slots=True)
class ApproveReviewCommand:
    """`assignments` and `charges` are the two ways to answer *which spool fed this tray*.

    An assignment names one spool and gives it the tray whole — the answer the queue asks
    for most, and the one that needs no arithmetic from the caller. `charges` states the
    split for a tray that fed from more than one spool, which is what happens when a spool
    empties mid-print and is replaced in the same tray. A tray may appear in one of them,
    never in both; the entity refuses the contradiction rather than picking a winner.
    """

    review_id: ReviewId
    amounts: dict[TrayRef, Grams] | None = None
    assignments: dict[TrayRef, SpoolId] | None = None
    charges: dict[TrayRef, tuple[ReviewCharge, ...]] | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class DismissReviewCommand:
    review_id: ReviewId
    note: str | None = None


@dataclass(frozen=True, slots=True)
class OpenPendingReview:
    jobs: PrintJobRepository
    reviews: ReviewRepository
    spools: SpoolRepository
    estimator: ConsumptionEstimator
    clock: Clock
    events: EventPublisher
    uow: UnitOfWork

    async def execute(self, command: OpenPendingReviewCommand) -> ReviewId:
        # Estimation runs before the unit of work: it is a pure function of the job the
        # command already carries, and the Phase 4 strategy does file I/O — which must
        # not hold the ledger's one write lock while it waits on a printer's FTP server.
        estimates, estimator_used = await self._estimates_for(command)

        async with self.uow:
            opened = await self._open(command, estimates, estimator_used)

        # Published after the commit — never for a write that could still roll back.
        await self.events.publish(opened)
        return opened.review_id

    async def open_within_unit(self, command: OpenPendingReviewCommand) -> ReviewOpened:
        """Open inside the caller's ambient unit of work — UC-04's transactional channel.

        The caller already holds the unit, so the review lands in the same transaction
        as whatever the caller has written — movements, the idempotency flag — and rolls
        back with them (docs/04-use-cases.md UC-04 steps 2 and 7). Two consequences the
        caller owns:

        - The returned `ReviewOpened` is **not published here**. The caller publishes it
          after *its* commit, because an event for a write that could still roll back
          would announce something that never happened.
        - Only the amounts channel is legal. Estimation may do file I/O (Phase 4), which
          must never run while the ledger's one write lock is held — and UC-04 always
          knows its figures, so the restriction costs nothing. Refused here rather than
          documented and hoped for.
        """
        if command.amounts is None:
            msg = "opening a review inside an ambient unit requires caller-supplied amounts"
            raise InvalidValueError(msg)
        return await self._open(command, dict(command.amounts), EstimatorKind.NONE)

    async def _open(
        self,
        command: OpenPendingReviewCommand,
        estimates: dict[TrayRef, Grams],
        estimator_used: EstimatorKind,
    ) -> ReviewOpened:
        """The transactional core: runs inside a unit of work the caller holds."""
        await self.jobs.save(command.job)
        queue = await self.reviews.list_pending()
        if any(pending.job_id == command.job.id for pending in queue):
            msg = f"job {command.job.id} already has a pending review"
            raise ReviewAlreadyPendingError(msg)

        # Freeze the attribution now. A review may sit for days while spools are swapped;
        # resolving at approval time would deduct a cancelled Tuesday print from whatever
        # happens to be in the slot on Friday. The mounted spool freezes as one charge for
        # the whole estimate, which is the honest proposal for a tray nobody has told us
        # was shared. No mounted spool freezes as no charge at all — a fact worth
        # recording, not an error.
        lines: list[ReviewLine] = []
        for tray in sorted(estimates):
            mounted = await self.spools.find_by_location(AmsSlot(tray))
            lines.append(
                ReviewLine(
                    tray=tray,
                    estimated=estimates[tray],
                    charges=(
                        (ReviewCharge(spool_id=mounted.id, amount=estimates[tray]),)
                        if mounted is not None
                        else ()
                    ),
                )
            )
        review = open_review(
            job_id=command.job.id,
            reason=command.reason,
            lines=tuple(lines),
            estimator_used=estimator_used,
            opened_at=self.clock.now(),
        )
        await self.reviews.save(review)
        return ReviewOpened(
            review_id=review.id,
            job_id=command.job.id,
            job_name=command.job.name,
            reason=command.reason,
        )

    async def _estimates_for(
        self, command: OpenPendingReviewCommand
    ) -> tuple[dict[TrayRef, Grams], EstimatorKind]:
        if command.amounts is not None:
            # UC-04 already knows the figures; `NONE` records that no estimator touched
            # them (see `EstimatorKind` for why the same member is also the no-data flag).
            return dict(command.amounts), EstimatorKind.NONE
        try:
            return await self.estimator.estimate(command.job), self.estimator.kind
        except EstimationUnavailableError:
            # The review still opens: a zero placeholder per known tray plus the explicit
            # flag. The user is asked; nothing is guessed — and when not even the trays
            # are known, the review opens empty, documenting that a loss happened whose
            # size nobody can name.
            zeros = {tray: Grams.zero() for tray in command.job.reported_usage or {}}
            return zeros, EstimatorKind.NONE


@dataclass(frozen=True, slots=True)
class ApproveReview:
    reviews: ReviewRepository
    spools: SpoolRepository
    movements: MovementRepository
    clock: Clock
    events: EventPublisher
    uow: UnitOfWork
    anomalies: AnomalyDetector = field(default_factory=AnomalyDetector)

    async def execute(self, command: ApproveReviewCommand) -> None:
        to_publish: list[DomainEvent] = []
        # One unit of work around validate-write-mark: either every movement lands *and*
        # the review turns terminal, or none of it does. A crash between the two would
        # leave deductions with no decision to explain them — or a decision that deducts
        # again on retry.
        async with self.uow:
            review = await self.reviews.get(command.review_id)
            if review is None:
                raise ReviewNotFoundError(command.review_id)
            now = self.clock.now()
            # The entity enforces its own rules here: idempotency, override validity, and
            # the sum invariant — every tray's charges add up to what it confirms — all
            # before anything writes.
            approved = review.approved(
                at=now,
                amounts=command.amounts,
                assignments=command.assignments,
                charges=command.charges,
                note=command.note,
            )

            # Load every charged spool before the first append, so a bad attribution
            # rejects the approval with nothing written.
            charged: dict[SpoolId, Spool] = {}
            for _tray, _amount, spool_id in approved.confirmed_charges:
                if spool_id in charged:
                    continue
                spool = await self.spools.get(spool_id)
                if spool is None:
                    raise SpoolNotFoundError(spool_id)
                if spool.is_discarded:
                    # Also the honest accounting: discarding wrote off the whole balance,
                    # so charging the estimate afterwards would count the loss twice.
                    msg = f"spool {spool.display_name} was discarded"
                    raise SpoolDiscardedError(msg)
                # The same double count, arriving by measurement: a reconciliation set the
                # balance to what the scale read, and the scale had already weighed this
                # print's consumption. Refused rather than dismissed on the user's behalf —
                # resolving somebody's decision silently is what this queue exists not to do.
                since_opened = await self.movements.list_since(spool_id, approved.opened_at)
                if any(movement.is_reconciliation for movement in since_opened):
                    msg = (
                        f"spool {spool.display_name} was weighed after this print, so the "
                        f"estimate is already inside that measurement; dismiss the review "
                        f"instead"
                    )
                    raise SpoolReconciledSinceReviewError(msg)
                charged[spool_id] = spool

            final_balances: dict[SpoolId, Grams] = {}
            for tray, amount, spool_id in approved.confirmed_charges:
                await self.movements.append(
                    record(
                        spool_id=spool_id,
                        type=MovementType.ESTIMATED_CONSUMPTION,
                        amount=-amount,
                        source=MovementSource.USER_CONFIRMED,
                        occurred_at=now,
                        # The single-machine sentence, for the reason UC-04's note gives.
                        note=f"Slot {tray.slot} of a reviewed print",
                        # Both keys, deliberately: without `review_id` the history can say
                        # *confirmed by you* but not which decision confirmed it, and the
                        # queue stops being an audit trail the moment it resolves.
                        job_id=approved.job_id,
                        review_id=approved.id,
                    )
                )
                new_balance = balance(await self.movements.list_for_spool(spool_id))
                final_balances[spool_id] = new_balance
                to_publish.append(
                    MovementRecorded(
                        spool_id=spool_id,
                        movement_type=MovementType.ESTIMATED_CONSUMPTION,
                        amount=-amount,
                        new_balance=new_balance,
                    )
                )
            await self.reviews.save(approved)

        # Published after the commit — never for a write that could still roll back.
        # Confidence needs no explicit step: it is derived, and the appended
        # ESTIMATED_CONSUMPTION entries are exactly what degrades the affected spools
        # toward LOW on the next evaluation.
        for spool_id, final in final_balances.items():
            spool = charged[spool_id]
            for anomaly in self.anomalies.inspect(
                spool_id=spool_id, balance=final, location=spool.location
            ):
                to_publish.append(AnomalyDetected(anomaly=anomaly))
            if not final.is_positive:
                to_publish.append(SpoolDepleted(spool_id=spool_id, display_name=spool.display_name))
        to_publish.append(
            ReviewResolved(
                review_id=approved.id, job_id=approved.job_id, state=ReviewState.APPROVED
            )
        )
        for event in to_publish:
            await self.events.publish(event)


@dataclass(frozen=True, slots=True)
class DismissReview:
    reviews: ReviewRepository
    clock: Clock
    events: EventPublisher
    uow: UnitOfWork

    async def execute(self, command: DismissReviewCommand) -> None:
        async with self.uow:
            review = await self.reviews.get(command.review_id)
            if review is None:
                raise ReviewNotFoundError(command.review_id)
            dismissed = review.dismissed(at=self.clock.now(), note=command.note)
            await self.reviews.save(dismissed)

        # Published after the commit — never for a write that could still roll back.
        await self.events.publish(
            ReviewResolved(
                review_id=dismissed.id, job_id=dismissed.job_id, state=ReviewState.DISMISSED
            )
        )
