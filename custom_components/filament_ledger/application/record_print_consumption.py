"""UC-04 · RecordPrintConsumption. The only fully automatic deduction in the system.

Automatic not because the number is measured — it is the slicer's plan — but because the
job ran to completion, so the plan was carried out in full. Plan and reality agree to
within flow-rate variance, which is the same variance a scale would find
(docs/04-use-cases.md UC-04).

Everything the printer could not attribute degrades to a review instead of a guess: a slot
that consumed with no spool mounted in it, and a job with no usable per-tray figure at all.
Neither throws, and neither is treated as zero — a missing figure is not a figure of zero,
and recording zero for a print that consumed 84 g is a silent, optimistic lie.

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
from ..domain.port.repositories import MovementRepository, PrintJobRepository, SpoolRepository
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.service.anomaly_detector import AnomalyDetector
from ..domain.service.balance_calculator import balance
from ..domain.value.grams import Grams
from ..domain.value.identifiers import SlotIndex
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
                slot: used for slot, used in (job.reported_usage or {}).items() if not used.is_zero
            }
            if not consuming:
                # Step 2: no usable per-tray figure — never materialised (`None`), named
                # no trays (`{}`), or named only zeros. A review documents the gap with
                # zero placeholders and the explicit no-data flag; nothing is deducted,
                # because a missing figure is not a figure of zero.
                to_publish += await self._review_opened(recorded, dict(job.reported_usage or {}))
            else:
                to_publish += await self._deduct(recorded, consuming)

        # Published after the commit — never for a write that could still roll back.
        for event in to_publish:
            await self.events.publish(event)

    async def _deduct(
        self, recorded: PrintJob, consuming: dict[SlotIndex, Grams]
    ) -> list[DomainEvent]:
        """Steps 3–7: one PRINT_CONSUMPTION per resolved slot, one review for the rest."""
        events: list[DomainEvent] = []
        unresolved: dict[SlotIndex, Grams] = {}
        now = self.clock.now()
        # Separate facts: the print finished when the job says it did; `now` is merely
        # when the ledger heard about it (docs/08-data-model.md).
        occurred_at = recorded.ended_at if recorded.ended_at is not None else now

        for slot, used in sorted(consuming.items()):
            mounted = await self.spools.find_by_location(AmsSlot(slot))
            if mounted is None:
                # Collected, not guessed: the figure goes to a review carrying a null
                # resolution, where the user supplies the missing half (step 7).
                unresolved[slot] = used
                continue
            await self.movements.append(
                record(
                    spool_id=mounted.id,
                    type=MovementType.PRINT_CONSUMPTION,
                    amount=-used,
                    source=MovementSource.AUTOMATIC,
                    occurred_at=occurred_at,
                    recorded_at=now,
                    note=f"Slot {slot} of {recorded.name}",
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

    async def _review_opened(
        self, recorded: PrintJob, amounts: dict[SlotIndex, Grams]
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
