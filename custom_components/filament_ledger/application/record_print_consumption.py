"""UC-04 · RecordPrintConsumption. The only fully automatic deduction in the system.

Automatic not because the number is measured — it is the slicer's plan — but because the
job ran to completion, so the plan was carried out in full. Plan and reality agree to
within flow-rate variance, which is the same variance a scale would find
(docs/04-use-cases.md UC-04).

Everything the printer could not attribute degrades to a review instead of a guess: a tray
that consumed with no spool mounted in it, and a job with no usable per-tray figure at all.
Neither throws, and neither is treated as zero — a missing figure is not a figure of zero,
and recording zero for a print that consumed 84 g is a silent, optimistic lie. The
figureless review still names the trays: every tray the job's printer holds a spool in is
listed at zero, because a card with no rows gives the user nothing to type the grams into
(`_trays_to_ask_about`).

The whole flow — the job row, the movements, any review, and the `consumption_recorded`
flag — runs in **one unit of work**. The flag is the single idempotency guard, and it only
guards anything if it commits with the movements it covers: a restart between the two
would otherwise deduct the same print twice, and a duplicate ledger entry is
indistinguishable from a real one after the fact.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

from ..domain.error import ReviewAlreadyPendingError
from ..domain.event import (
    AnomalyDetected,
    DomainEvent,
    EventPublisher,
    MovementRecorded,
    SpoolDepleted,
)
from ..domain.model.movement import record
from ..domain.model.print_job import PrintJob
from ..domain.port.clock import Clock
from ..domain.port.repositories import (
    MovementRepository,
    PrintJobRepository,
    SpoolFilter,
    SpoolRepository,
)
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.service.anomaly_detector import AnomalyDetector
from ..domain.service.balance_calculator import balance
from ..domain.value.grams import Grams
from ..domain.value.identifiers import TrayRef
from ..domain.value.location import AmsSlot
from ..domain.value.movement_type import MovementSource, MovementType
from ..domain.value.review import ReviewReason
from .review_queue import OpenPendingReview, OpenPendingReviewCommand

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecordPrintConsumption:
    jobs: PrintJobRepository
    spools: SpoolRepository
    movements: MovementRepository
    open_pending_review: OpenPendingReview
    clock: Clock
    events: EventPublisher
    uow: UnitOfWork
    anomalies: AnomalyDetector = field(default_factory=AnomalyDetector)

    async def execute(self, job: PrintJob) -> None:
        to_publish: list[DomainEvent] = []
        # One unit of work around steps 2–8: the job row, the movements, any review and
        # the idempotency flag land together or not at all. The idempotency read happens
        # inside the same unit, so two deliveries of one ending serialise — the first
        # marks the row recorded, the second finds it so and writes nothing.
        async with self.uow:
            stored = await self.jobs.get(job.id)
            if stored is not None and stored.consumption_recorded:
                LOGGER.debug(
                    "job %s (%s) already has its consumption recorded; duplicate ignored",
                    job.id,
                    job.name,
                )
                return
            recorded = replace(job, consumption_recorded=True)
            # Step 8, written first: saving the flagged row here puts "recorded" in the
            # same transaction as everything below — and the movements' foreign key needs
            # the row to exist before the first append anyway.
            await self.jobs.save(recorded)

            consuming = {
                tray: used for tray, used in (job.reported_usage or {}).items() if not used.is_zero
            }
            if not consuming:
                # Step 2: no usable per-tray figure — never materialised (`None`), named
                # no trays (`{}`), or named only zeros. A review documents the gap with
                # zero placeholders and the explicit no-data flag; nothing is deducted,
                # because a missing figure is not a figure of zero. The placeholders
                # cover every tray the printer reported *and* every tray it currently
                # holds a spool in, so the card has a row per loaded tray to type into.
                to_publish += await self._review_opened(
                    recorded, await self._trays_to_ask_about(job)
                )
            else:
                to_publish += await self._deduct(recorded, consuming)

        # Published after the commit — never for a write that could still roll back.
        for event in to_publish:
            await self.events.publish(event)

    async def _deduct(
        self, recorded: PrintJob, consuming: dict[TrayRef, Grams]
    ) -> list[DomainEvent]:
        """Steps 3–7: one PRINT_CONSUMPTION per resolved tray, one review for the rest."""
        events: list[DomainEvent] = []
        unresolved: dict[TrayRef, Grams] = {}
        now = self.clock.now()
        # Separate facts: the print finished when the job says it did; `now` is merely
        # when the ledger heard about it (docs/08-data-model.md).
        #
        # **Both come from this ledger's clock, and that is load-bearing.** Every read that
        # orders the ledger sorts on `occurred_at` — the spool detail's running balance,
        # the history's newest-first slice, and `movements_since_anchor`, which decides
        # confidence by asking which entries fall after the last reconciliation. A foreign
        # clock here would reorder those against entries stamped by `SystemClock`, and the
        # damaging direction is silent: a printer running slow sorts its print *before* a
        # reconciliation that really happened first, dropping the print out of the anchor
        # window and reporting more confidence than the spool has earned. So the printer's
        # own timestamps live in `printer_started_at`/`printer_ended_at` and never reach
        # this line.
        occurred_at = recorded.ended_at if recorded.ended_at is not None else now

        for tray, used in sorted(consuming.items()):
            mounted = await self.spools.find_by_location(AmsSlot(tray))
            if mounted is None:
                # Collected, not guessed: the figure goes to a review carrying a null
                # resolution, where the user supplies the missing half (step 7).
                unresolved[tray] = used
                continue
            await self.movements.append(
                record(
                    spool_id=mounted.id,
                    type=MovementType.PRINT_CONSUMPTION,
                    amount=-used,
                    source=MovementSource.AUTOMATIC,
                    occurred_at=occurred_at,
                    recorded_at=now,
                    # Still the single-machine sentence: the ledger follows one printer,
                    # this note is what a user reads in the history, and naming a serial
                    # they have never had to think about would be noise, not precision.
                    note=f"Slot {tray.slot} of {recorded.name}",
                    job_id=recorded.id,
                )
            )
            new_balance = balance(await self.movements.list_for_spool(mounted.id))
            events.append(
                MovementRecorded(
                    spool_id=mounted.id,
                    movement_type=MovementType.PRINT_CONSUMPTION,
                    amount=-used,
                    new_balance=new_balance,
                )
            )
            # Confidence needs no explicit step: it is derived, and the appended
            # PRINT_CONSUMPTION entries are exactly what the consumed-ratio reads on the
            # next evaluation — drifting toward MEDIUM as a spool is drawn down, never
            # to LOW, which only an estimate earns.
            for anomaly in self.anomalies.inspect(
                spool_id=mounted.id, balance=new_balance, location=mounted.location
            ):
                events.append(AnomalyDetected(anomaly=anomaly))
            if not new_balance.is_positive:
                events.append(SpoolDepleted(spool_id=mounted.id, display_name=mounted.display_name))

        if unresolved:
            events += await self._review_opened(recorded, unresolved)
        return events

    async def _trays_to_ask_about(self, job: PrintJob) -> dict[TrayRef, Grams]:
        """The trays a figureless review lists: the reported ones, plus every loaded one.

        A review with no lines is a dead end. The panel renders the no-data card with no
        tray rows, so the user can neither type the grams nor pick the spool — which is
        exactly where review `497c3c96` was left on the reference instance when an
        identical re-print published no figures (docs/12-field-notes.md, 2026-09-03).
        The trays the job's printer holds a spool in are the ones the print could have
        drawn from, so each of them is listed at zero — a placeholder awaiting the user,
        never a claim — and `OpenPendingReview._open` freezes the mounted spool as that
        zero charge, which is what puts a spool's name on the row.

        Reported trays keep whatever the printer said (zeros, on this branch) and are not
        listed twice. A job that names no printer — a row from before migration 0008 —
        adds nothing: which machine's trays to list would be a guess, and the review
        still documents the loss as it always has. Only AMS trays are listed, because
        usage is keyed by tray (docs/02 §2.3) and the direct feed has none; and only this
        printer's, because another machine's spools were not in front of this print.
        """
        amounts = dict(job.reported_usage or {})
        if job.printer is None:
            return amounts
        for spool in await self.spools.list(SpoolFilter(mounted_only=True)):
            location = spool.location
            if isinstance(location, AmsSlot) and location.tray.printer == job.printer:
                amounts.setdefault(location.tray, Grams.zero())
        return amounts

    async def _review_opened(
        self, recorded: PrintJob, amounts: dict[TrayRef, Grams]
    ) -> list[DomainEvent]:
        """Open the UNMAPPED_USAGE review inside the ambient unit, tolerating a race.

        A cancel and a finish delivered together can both correlate to one job; if the
        cancellation's review is already open, a second card would split one decision
        across two items. The figures survive on the job row either way, so this is a
        line in the log, never a crash — the same policy `TrackPrintJob` applies.
        """
        try:
            opened = await self.open_pending_review.open_within_unit(
                OpenPendingReviewCommand(
                    job=recorded, reason=ReviewReason.UNMAPPED_USAGE, amounts=amounts
                )
            )
        except ReviewAlreadyPendingError:
            LOGGER.warning(
                "job %s (%s) already has a pending review; its unmapped usage stays on "
                "the job record",
                recorded.id,
                recorded.name,
            )
            return []
        return [opened]
