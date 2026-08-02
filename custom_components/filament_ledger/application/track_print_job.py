"""The print lifecycle, recorded as the printer reports it.

One use case receives every `PrintEvent` the gateway translates. A start becomes a
`RUNNING` row; an ending updates that row with the moment's figures and — for a cancelled
or failed job — opens a review through UC-05. The classification is never made here: the
event arrives with its outcome already read off the upstream event type (Q1, closed).

A `FINISHED` job goes to UC-04 (Q4, closed — docs/12-field-notes.md): the ending's row,
the automatic deduction and the `consumption_recorded` flag land in one unit inside
`RecordPrintConsumption`, which degrades to a review with reason `UNMAPPED_USAGE`
wherever a figure cannot be attributed (docs/04-use-cases.md UC-04).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from ..domain.error import ReviewAlreadyPendingError
from ..domain.model.print_job import PrintJob
from ..domain.port.clock import Clock
from ..domain.port.repositories import PrintJobRepository
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.value.identifiers import PrintJobId, new_print_job_id
from ..domain.value.print_event import PrintEnded, PrintEvent, PrintStarted
from ..domain.value.print_job_state import PrintJobState
from ..domain.value.review import ReviewReason
from .record_print_consumption import RecordPrintConsumption
from .review_queue import OpenPendingReview, OpenPendingReviewCommand

LOGGER = logging.getLogger(__name__)

# How far down the recent-jobs listing the running job is looked for. The job an ending
# belongs to is expected at the head — anything RUNNING deeper than this is a stale row
# from an ending that never arrived, not the job that just stopped.
CORRELATION_WINDOW = 10

# Why a review opens, keyed by how the job ended. `FINISHED` is deliberately absent:
# a completed job needs no decision (docs/adr/0004-approval-queue-for-estimates.md).
_REVIEW_REASONS = {
    PrintJobState.CANCELLED: ReviewReason.CANCELLED,
    PrintJobState.FAILED: ReviewReason.FAILED,
}


@dataclass(frozen=True, slots=True)
class TrackPrintJob:
    jobs: PrintJobRepository
    open_pending_review: OpenPendingReview
    record_print_consumption: RecordPrintConsumption
    clock: Clock
    uow: UnitOfWork

    async def execute(self, event: PrintEvent) -> PrintJobId:
        match event:
            case PrintStarted():
                return await self._started(event)
            case PrintEnded():
                return await self._ended(event)

    async def _started(self, event: PrintStarted) -> PrintJobId:
        """A new job, a new identity. The upstream event carries no job id, so the row
        created here is what a later ending correlates back to (see `_running_job`)."""
        job = PrintJob(
            id=new_print_job_id(),
            name=event.name,
            state=PrintJobState.RUNNING,
            started_at=self.clock.now(),
            reported_usage=event.plan,
        )
        async with self.uow:
            await self.jobs.save(job)
        return job.id

    async def _ended(self, event: PrintEnded) -> PrintJobId:
        """Close the running job with the moment's figures — creating the row first when
        a restart swallowed the start, because a review must never be lost to a restart."""
        now = self.clock.now()
        job = await self._running_job()
        if job is None:
            # The integration restarted mid-print: no RUNNING row exists for this ending.
            # The start time is gone; `now` is the honest lower bound for both timestamps.
            job = PrintJob(
                id=new_print_job_id(),
                name=event.name,
                state=PrintJobState.RUNNING,
                started_at=now,
            )
        ended = replace(
            job,
            state=event.outcome,
            ended_at=now,
            layer_reached=event.layer_reached,
            total_layers=event.total_layers,
            progress=event.progress,
            # The ending's figures win when present; otherwise the plan captured at start
            # survives — for an interrupted job those totals are exactly what the
            # estimator scales by progress (docs/07-consumption-estimation.md §7.3).
            reported_usage=(
                event.reported_usage if event.reported_usage is not None else job.reported_usage
            ),
            raw_gcode_state=event.raw_gcode_state,
            raw_print_error=event.raw_print_error,
        )

        reason = _REVIEW_REASONS.get(event.outcome)
        if reason is None:
            # FINISHED. UC-04 owns the whole write: the ending's row, the deduction and
            # the idempotency flag commit in one unit — saving the row here first would
            # put a second delivery's stale claims outside the guard.
            await self.record_print_consumption.execute(ended)
            return ended.id

        try:
            # UC-05 saves the job and the review in one unit, so the ending and the
            # decision it demands land together or not at all.
            await self.open_pending_review.execute(
                OpenPendingReviewCommand(job=ended, reason=reason)
            )
        except ReviewAlreadyPendingError:
            # Two deliveries of one ending can race: both correlate to the same RUNNING
            # job before either writes, the first opens the review, and the second finds
            # it already open. The decision item exists — a second card would split one
            # decision across two — so this is a line in the log, never a crash.
            LOGGER.warning(
                "job %s (%s) already has a pending review; duplicate %s event ignored",
                ended.id,
                ended.name,
                event.outcome.value,
            )
        return ended.id

    async def _running_job(self) -> PrintJob | None:
        """The newest RUNNING job — the one a terminal event belongs to.

        Correlation by state rather than by an in-memory id, deliberately: memory does
        not survive a restart, and the row does. If several RUNNING rows exist — endings
        that never arrived — the newest is the one that just stopped; the stale ones stay
        verbatim, reclassifiable later.
        """
        for job in await self.jobs.list_recent(CORRELATION_WINDOW):
            if job.state is PrintJobState.RUNNING:
                return job
        return None
