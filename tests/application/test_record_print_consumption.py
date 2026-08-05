"""UC-04 on real SQLite: the only fully automatic deduction in the system.

These drive the whole FINISHED path the way production does — a start and an ending
through `TrackPrintJob`, which hands the finished job to `RecordPrintConsumption` — so
every assertion covers the seam as wired, against the same schema and constraints
production runs on. The figures come from the Q4 capture on the reference A1
(docs/12-field-notes.md): per-tray weights populate at event time, and they flicker, which
is why the missing-figure branch is exercised as hard as the happy path.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import timedelta

import pytest

from custom_components.filament_ledger.application.reconcile_spool import ReconcileSpoolCommand
from custom_components.filament_ledger.application.register_spool import RegisterSpoolCommand
from custom_components.filament_ledger.application.review_queue import OpenPendingReviewCommand
from custom_components.filament_ledger.domain.event import (
    AnomalyDetected,
    MovementRecorded,
    ReviewOpened,
    SpoolDepleted,
)
from custom_components.filament_ledger.domain.model.pending_review import ReviewCharge
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.service.anomaly_detector import AnomalyKind
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.confidence import Confidence
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    PrintJobId,
    SlotIndex,
    SpoolId,
)
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.percentage import Percentage
from custom_components.filament_ledger.domain.value.print_event import PrintEnded, PrintStarted
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.domain.value.review import EstimatorKind, ReviewReason
from custom_components.filament_ledger.infrastructure.persistence.print_job_repository import (
    SqlitePrintJobRepository,
)
from custom_components.filament_ledger.infrastructure.persistence.review_repository import (
    SqliteReviewRepository,
)

from .conftest import EPOCH, Ledger

SLOT_1 = SlotIndex(1)
SLOT_2 = SlotIndex(2)
SLOT_4 = SlotIndex(4)


async def a_spool(ledger: Ledger, **overrides: object) -> SpoolId:
    command = RegisterSpoolCommand(
        material=Material.of(MaterialKind.PLA),
        colour=Colour.parse("000000"),
        opening_weight=Grams.of(1000),
        core_weight=Grams.of(250),
        vendor="Bambu Lab",
        **overrides,  # type: ignore[arg-type]
    )
    return await ledger.use_cases.register_spool.execute(command)


def finished(reported_usage: dict[SlotIndex, Grams] | None = None) -> PrintEnded:
    return PrintEnded(
        outcome=PrintJobState.FINISHED,
        name="bracket_v3.gcode.3mf",
        layer_reached=209,
        total_layers=209,
        progress=Percentage.of(100),
        reported_usage=reported_usage,
        raw_gcode_state="finish",
    )


async def ran_to_completion(
    ledger: Ledger, reported_usage: dict[SlotIndex, Grams] | None
) -> PrintJob:
    """One whole lifecycle through the seam as wired: a start, then the FINISHED ending."""
    await ledger.use_cases.track_print_job.execute(
        PrintStarted(name="bracket_v3.gcode.3mf", plan=None)
    )
    ledger.clock.advance(minutes=42)
    job_id = await ledger.use_cases.track_print_job.execute(finished(reported_usage))
    job = await SqlitePrintJobRepository(ledger.database).get(job_id)
    assert job is not None
    return job


async def consumption_rows(ledger: Ledger) -> list[dict[str, object]]:
    rows = await ledger.database.fetch_all(
        "SELECT spool_id, amount_mg, source, job_id, review_id, occurred_at FROM movement "
        "WHERE type = 'PRINT_CONSUMPTION' ORDER BY rowid"
    )
    return [dict(row) for row in rows]


async def balance_of(ledger: Ledger, spool_id: SpoolId) -> Grams:
    return (await ledger.use_cases.queries.detail(spool_id)).summary.balance


class TestAutomaticDeduction:
    async def test_a_finished_print_deducts_from_the_mounted_spool(self, ledger: Ledger) -> None:
        """The happy path of UC-04, end to end: one PRINT_CONSUMPTION with source
        AUTOMATIC and the job's id, the flag set, no review — the job ran to completion,
        so no decision is needed."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)

        job = await ran_to_completion(ledger, {SLOT_1: Grams.of("38.2")})

        [row] = await consumption_rows(ledger)
        assert row["spool_id"] == spool_id
        assert row["amount_mg"] == -38200
        assert row["source"] == "AUTOMATIC"
        assert row["job_id"] == job.id
        assert row["review_id"] is None
        assert job.consumption_recorded is True
        assert await balance_of(ledger, spool_id) == Grams.of("961.8")
        assert await SqliteReviewRepository(ledger.database).list_pending() == []
        [recorded] = ledger.events.of(MovementRecorded)
        assert isinstance(recorded, MovementRecorded)
        assert recorded.new_balance == Grams.of("961.8")
        assert ledger.events.of(ReviewOpened) == []

    async def test_the_q4_capture_deducts_from_tray_4(self, ledger: Ledger) -> None:
        """The exact print that closed Q4 (docs/12-field-notes.md, 2026-08-03): the weight
        sensor read 296.56 g total with `{"AMS 1 Tray 4": 296.56}` in its attributes at
        the moment the job finished. That figure, against the spool mounted in slot 4."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_4)

        await ran_to_completion(ledger, {SLOT_4: Grams.of("296.56")})

        [row] = await consumption_rows(ledger)
        assert row["spool_id"] == spool_id
        assert row["amount_mg"] == -296560
        assert await balance_of(ledger, spool_id) == Grams.of("703.44")

    async def test_the_movement_dates_the_consumption_to_the_ending(self, ledger: Ledger) -> None:
        """`occurred_at` is when the print finished — the job's ending, not some later
        moment the ledger happened to write."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)

        job = await ran_to_completion(ledger, {SLOT_1: Grams.of(10)})

        [row] = await consumption_rows(ledger)
        assert job.ended_at is not None
        assert row["occurred_at"] == job.ended_at.isoformat()

    async def test_the_printers_clock_never_reaches_the_ledgers_ordering(
        self, ledger: Ledger
    ) -> None:
        """The ordering assumption, asserted rather than argued (docs/04 UC-04).

        This machine's clock is a year out — an exaggeration of the minutes a real one
        drifts, chosen so the failure would be unmissable. The consumption entry must
        still be dated by this ledger's clock, because `occurred_at` is what every read
        that orders the ledger sorts on and a foreign clock in that column reorders a
        print against entries this integration stamped itself.
        """
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)
        a_year_late = EPOCH + timedelta(days=365)

        await ledger.use_cases.track_print_job.execute(
            PrintStarted(name="bracket_v3.gcode.3mf", printer_started_at=a_year_late)
        )
        ledger.clock.advance(minutes=42)
        job_id = await ledger.use_cases.track_print_job.execute(
            replace(
                finished({SLOT_1: Grams.of(10)}),
                printer_started_at=a_year_late,
                printer_ended_at=a_year_late + timedelta(minutes=155),
            )
        )

        [row] = await consumption_rows(ledger)
        assert row["occurred_at"] == ledger.clock.now().isoformat()
        # And the machine's own figures are on the job all the same — kept, not discarded.
        job = await SqlitePrintJobRepository(ledger.database).get(job_id)
        assert job is not None
        assert job.printer_ended_at == a_year_late + timedelta(minutes=155)

    async def test_a_wandering_printer_clock_cannot_move_a_print_past_a_reconciliation(
        self, ledger: Ledger
    ) -> None:
        """The consequence the split exists to prevent, spelled out.

        A reconciliation is the anchor confidence is measured from: everything after it is
        unaccounted for, everything before it is inside the figure it established
        ([02 §2.6]). Weigh the spool, then print 300 g of a 1000 g reel — that is 30% drawn
        since the anchor, which is MEDIUM. Had the printer's clock (a year behind here)
        dated the print, it would have sorted *before* the reconciliation, fallen out of the
        anchor window, and left the spool reading HIGH: more confidence than it has earned,
        which is the flattering direction and the one nobody notices.
        """
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)
        # A scale that disagrees by 5 g, which is what makes this a movement at all.
        await ledger.use_cases.reconcile_spool.execute(
            ReconcileSpoolCommand(spool_id=spool_id, measured=Grams.of(995), includes_core=False)
        )
        ledger.clock.advance(days=1)
        a_year_early = EPOCH - timedelta(days=365)

        await ledger.use_cases.track_print_job.execute(
            PrintStarted(name="bracket_v3.gcode.3mf", printer_started_at=a_year_early)
        )
        await ledger.use_cases.track_print_job.execute(
            replace(
                finished({SLOT_1: Grams.of(300)}),
                printer_started_at=a_year_early,
                printer_ended_at=a_year_early + timedelta(hours=5),
            )
        )

        summary = (await ledger.use_cases.queries.detail(spool_id)).summary
        assert summary.confidence is Confidence.MEDIUM

    async def test_consumption_degrades_confidence_by_volume_not_by_kind(
        self, ledger: Ledger
    ) -> None:
        """PRINT_CONSUMPTION is unattended, but confidence turns on the movement's type,
        not its source: an automatic deduction never earns LOW — that is reserved for
        estimates — it only drifts a balance toward MEDIUM once a fifth of the opening
        weight has been drawn."""
        lightly_used = await a_spool(ledger, label="lightly used")
        heavily_used = await a_spool(ledger, label="heavily used")
        await ledger.use_cases.mount_spool.execute(lightly_used, SLOT_1)
        await ledger.use_cases.mount_spool.execute(heavily_used, SLOT_2)

        await ran_to_completion(ledger, {SLOT_1: Grams.of("38.2"), SLOT_2: Grams.of(250)})

        light = await ledger.use_cases.queries.detail(lightly_used)
        heavy = await ledger.use_cases.queries.detail(heavily_used)
        assert light.summary.confidence is Confidence.HIGH
        assert heavy.summary.confidence is Confidence.MEDIUM


class TestIdempotency:
    async def test_a_replayed_job_deducts_nothing(self, ledger: Ledger) -> None:
        """Step 1: the guard is the stored row's flag, not the argument's — a redelivered
        job value claiming to be unrecorded still aborts silently."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)
        job = await ran_to_completion(ledger, {SLOT_1: Grams.of("38.2")})

        await ledger.use_cases.record_print_consumption.execute(
            replace(job, consumption_recorded=False)
        )

        assert len(await consumption_rows(ledger)) == 1
        assert await balance_of(ledger, spool_id) == Grams.of("961.8")
        assert len(ledger.events.of(MovementRecorded)) == 1

    async def test_racing_deliveries_of_one_finish_deduct_exactly_once(
        self, interleaved_ledger: Ledger
    ) -> None:
        """Two deliveries of one FINISHED ending, racing: both correlate to the same
        RUNNING job, and the flag commits in the same transaction as the movements — so
        the second delivery finds the row recorded and writes nothing at all."""
        ledger = interleaved_ledger
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)
        await ledger.use_cases.track_print_job.execute(
            PrintStarted(name="bracket_v3.gcode.3mf", plan=None)
        )
        event = finished({SLOT_1: Grams.of("38.2")})

        await asyncio.gather(
            ledger.use_cases.track_print_job.execute(event),
            ledger.use_cases.track_print_job.execute(event),
        )

        assert len(await consumption_rows(ledger)) == 1
        assert await balance_of(ledger, spool_id) == Grams.of("961.8")
        [job] = await SqlitePrintJobRepository(ledger.database).list_recent(10)
        assert job.consumption_recorded is True


class TestTheMissingFigureBranch:
    """Step 2: `None`, `{}` and all-zero are three shapes of the same fact — no usable
    per-tray figure — and a missing figure is not a figure of zero. Each opens a review
    with the explicit no-data flag, deducts nothing, and still marks the job recorded."""

    async def test_a_figure_that_never_materialised_becomes_a_review(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)

        job = await ran_to_completion(ledger, None)

        assert job.consumption_recorded is True
        assert job.reported_usage is None
        assert await consumption_rows(ledger) == []
        assert await balance_of(ledger, spool_id) == Grams.of(1000)
        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.job_id == job.id
        assert review.reason is ReviewReason.UNMAPPED_USAGE
        assert review.estimator_used is EstimatorKind.NONE
        # Not even the slots are known: the review documents that a loss happened whose
        # size nobody can name, with no line inventing one.
        assert review.lines == ()
        [opened] = ledger.events.of(ReviewOpened)
        assert isinstance(opened, ReviewOpened)
        assert opened.reason is ReviewReason.UNMAPPED_USAGE

    async def test_a_report_naming_no_trays_becomes_a_review(self, ledger: Ledger) -> None:
        """`{}` is a different fact from `None` — the printer reported and named no
        trays — but it is equally unusable, and it must not be read as zero consumption."""
        job = await ran_to_completion(ledger, {})

        assert job.consumption_recorded is True
        assert job.reported_usage == {}
        assert await consumption_rows(ledger) == []
        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.reason is ReviewReason.UNMAPPED_USAGE
        assert review.estimator_used is EstimatorKind.NONE
        assert review.lines == ()

    async def test_an_all_zero_report_becomes_a_review_with_placeholders(
        self, ledger: Ledger
    ) -> None:
        """Zeros on every named tray are a placeholder awaiting the user, not a claim
        that nothing was consumed — the slots are known, so the review freezes them,
        resolution and all, for the approval flow to fill in."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)

        job = await ran_to_completion(ledger, {SLOT_1: Grams.zero()})

        assert job.consumption_recorded is True
        assert await consumption_rows(ledger) == []
        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.reason is ReviewReason.UNMAPPED_USAGE
        assert review.estimator_used is EstimatorKind.NONE
        assert review.estimated_usage == {SLOT_1: Grams.zero()}
        # The spool is a fact, the amount is not: a zero charge is what says so.
        assert review.charges == [(SLOT_1, ReviewCharge(spool_id, Grams.zero()))]


class TestUnresolvedSlots:
    async def test_usage_on_an_unoccupied_slot_becomes_a_review_carrying_the_amount(
        self, ledger: Ledger
    ) -> None:
        """Step 7: no spool mounted means no deduction and no guess. The review carries
        the printer's figure with a null resolution — *slot 2 used 20 g and nobody knows
        which spool was in it* — for the user to supply the missing half."""
        job = await ran_to_completion(ledger, {SLOT_2: Grams.of(20)})

        assert job.consumption_recorded is True
        assert await consumption_rows(ledger) == []
        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.reason is ReviewReason.UNMAPPED_USAGE
        assert review.estimated_usage == {SLOT_2: Grams.of(20)}
        assert review.charges == []
        assert review.estimator_used is EstimatorKind.NONE

    async def test_resolved_slots_still_deduct_when_others_cannot(self, ledger: Ledger) -> None:
        """Mixed: the mounted slot deducts automatically, the unmounted one goes to a
        review carrying only its own amount. One failure must not hold the other
        hostage — and both land in the same transaction as the flag."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)

        job = await ran_to_completion(ledger, {SLOT_1: Grams.of(30), SLOT_2: Grams.of(20)})

        [row] = await consumption_rows(ledger)
        assert row["spool_id"] == spool_id
        assert row["amount_mg"] == -30000
        assert await balance_of(ledger, spool_id) == Grams.of(970)
        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.job_id == job.id
        assert review.estimated_usage == {SLOT_2: Grams.of(20)}
        assert review.charges == []
        [opened] = ledger.events.of(ReviewOpened)
        assert isinstance(opened, ReviewOpened)
        assert opened.reason is ReviewReason.UNMAPPED_USAGE


class TestARaceWithAnInterruptedEnding:
    async def test_an_already_pending_review_does_not_block_the_deduction(
        self, ledger: Ledger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A cancel and a finish delivered together can both correlate to one job, and
        the cancellation's review may already be open when UC-04 wants one for its
        unmapped slot. A second card would split one decision across two items, so the
        refusal is a warning — and it must not cost the rest of the pass: the resolved
        slot still deducts, and the job is still marked recorded."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)
        job = PrintJob(
            id=PrintJobId("job-1"),
            name="bracket_v3.gcode.3mf",
            state=PrintJobState.FINISHED,
            started_at=EPOCH,
            ended_at=EPOCH,
            reported_usage={SLOT_1: Grams.of(30), SLOT_2: Grams.of(20)},
        )
        await ledger.use_cases.open_pending_review.execute(
            OpenPendingReviewCommand(job=job, reason=ReviewReason.CANCELLED)
        )

        with caplog.at_level(logging.WARNING):
            await ledger.use_cases.record_print_consumption.execute(job)

        assert "already has a pending review" in caplog.text
        [row] = await consumption_rows(ledger)
        assert row["spool_id"] == spool_id
        assert row["amount_mg"] == -30000
        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.reason is ReviewReason.CANCELLED
        stored = await SqlitePrintJobRepository(ledger.database).get(job.id)
        assert stored is not None
        assert stored.consumption_recorded is True


class TestDepletionAndAnomalies:
    async def test_crossing_zero_announces_the_depletion(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)

        await ran_to_completion(ledger, {SLOT_1: Grams.of(1000)})

        assert await balance_of(ledger, spool_id) == Grams.zero()
        [depleted] = ledger.events.of(SpoolDepleted)
        assert isinstance(depleted, SpoolDepleted)
        assert depleted.spool_id == spool_id
        assert ledger.events.of(AnomalyDetected) == []

    async def test_a_deduction_past_the_balance_is_recorded_and_flagged(
        self, ledger: Ledger
    ) -> None:
        """The physical event happened; the ledger records reality and flags the
        inconsistency rather than refusing the truth — the same policy as every other
        movement-writing use case."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)

        await ran_to_completion(ledger, {SLOT_1: Grams.of(1100)})

        assert await balance_of(ledger, spool_id) == Grams.of(-100)
        [anomaly] = ledger.events.of(AnomalyDetected)
        assert isinstance(anomaly, AnomalyDetected)
        assert anomaly.anomaly.kind is AnomalyKind.NEGATIVE_BALANCE
        assert len(ledger.events.of(SpoolDepleted)) == 1
