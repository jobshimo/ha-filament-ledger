"""TrackPrintJob on real SQLite: the print lifecycle, recorded as the printer reports it.

These drive the use case with the `PrintEvent` values the gateway translates — the
behaviour pinned against the same schema and constraints production runs on, exactly as
`test_review_queue.py` pinned UC-05/06/07 before the gateway learned to classify endings.
Home Assistant is absent, which is the architecture's claim about this layer.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.filament_ledger.application.register_spool import RegisterSpoolCommand
from custom_components.filament_ledger.domain.event import ReviewOpened
from custom_components.filament_ledger.domain.model.pending_review import ReviewCharge
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    SpoolId,
    TrayRef,
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

from .conftest import EPOCH, Ledger, a_tray

TRAY_1 = a_tray(1)
TRAY_2 = a_tray(2)


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


def started(plan: dict[TrayRef, Grams] | None = None) -> PrintStarted:
    return PrintStarted(name="bracket_v3.gcode.3mf", plan=plan)


def ended(
    outcome: PrintJobState = PrintJobState.CANCELLED,
    *,
    layer_reached: int | None = 71,
    total_layers: int | None = 209,
    reported_usage: dict[TrayRef, Grams] | None = None,
    raw_print_error: int | None = None,
    printer_started_at: datetime | None = None,
    printer_ended_at: datetime | None = None,
) -> PrintEnded:
    return PrintEnded(
        outcome=outcome,
        name="bracket_v3.gcode.3mf",
        layer_reached=layer_reached,
        total_layers=total_layers,
        progress=Percentage.of(34),
        reported_usage=reported_usage,
        raw_gcode_state="pause",
        raw_print_error=raw_print_error,
        printer_started_at=printer_started_at,
        printer_ended_at=printer_ended_at,
    )


async def stored_jobs(ledger: Ledger) -> list[PrintJob]:
    return await SqlitePrintJobRepository(ledger.database).list_recent(10)


class TestAStartingPrint:
    async def test_a_start_becomes_a_running_job_with_the_plan_preserved(
        self, ledger: Ledger
    ) -> None:
        plan = {TRAY_1: Grams.of(209), TRAY_2: Grams.of(31)}

        job_id = await ledger.use_cases.track_print_job.execute(started(plan))

        [job] = await stored_jobs(ledger)
        assert job.id == job_id
        assert job.name == "bracket_v3.gcode.3mf"
        assert job.state is PrintJobState.RUNNING
        assert job.started_at == ledger.clock.now()
        assert job.reported_usage == plan

    async def test_a_start_without_a_plan_records_the_absence(self, ledger: Ledger) -> None:
        """The Q4-open path: no per-tray attributes means no figures — never zeros."""
        await ledger.use_cases.track_print_job.execute(started(plan=None))

        [job] = await stored_jobs(ledger)
        assert job.reported_usage is None

    async def test_every_start_is_a_new_identity(self, ledger: Ledger) -> None:
        """The upstream event carries no job id; two starts are two jobs, and the newest
        is the one a later ending correlates to."""
        first = await ledger.use_cases.track_print_job.execute(started())
        ledger.clock.advance(hours=1)
        second = await ledger.use_cases.track_print_job.execute(started())

        assert first != second
        assert len(await stored_jobs(ledger)) == 2


class TestAnInterruptedPrint:
    async def test_a_cancellation_ends_the_job_and_opens_a_review(self, ledger: Ledger) -> None:
        """The whole path: the plan captured at start survives the ending, the estimator
        scales it by the moment's progress, and the resolution freezes to the mounted
        spool. Nothing is deducted — that is the queue's whole point."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        await ledger.use_cases.track_print_job.execute(started({TRAY_1: Grams.of(209)}))
        ledger.clock.advance(minutes=42)

        job_id = await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.CANCELLED, raw_print_error=50348044)
        )

        [job] = await stored_jobs(ledger)
        assert job.id == job_id
        assert job.state is PrintJobState.CANCELLED
        assert job.ended_at == ledger.clock.now()
        assert job.layer_reached == 71
        assert job.total_layers == 209
        assert job.reported_usage == {TRAY_1: Grams.of(209)}
        assert job.raw_gcode_state == "pause"
        assert job.raw_print_error == 50348044

        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.job_id == job_id
        assert review.reason is ReviewReason.CANCELLED
        # 71 of 209 layers of a 209 g plan: 71 g, frozen to the mounted spool.
        assert review.estimated_usage == {TRAY_1: Grams.of(71)}
        assert review.charges == [(TRAY_1, ReviewCharge(spool_id, Grams.of(71)))]
        assert review.estimator_used is EstimatorKind.LINEAR_PROGRESS
        assert (await ledger.use_cases.queries.detail(spool_id)).summary.balance == Grams.of(1000)

    async def test_a_failure_opens_a_review_with_reason_failed(self, ledger: Ledger) -> None:
        """Classification comes only from the event type — never inferred from the error
        code, which is stored verbatim beside it (Q1, closed)."""
        await ledger.use_cases.track_print_job.execute(started({TRAY_1: Grams.of(209)}))

        await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FAILED, raw_print_error=50348044)
        )

        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.reason is ReviewReason.FAILED
        [event] = ledger.events.of(ReviewOpened)
        assert isinstance(event, ReviewOpened)
        assert event.job_name == "bracket_v3.gcode.3mf"

    async def test_the_endings_figures_override_the_plan_when_present(self, ledger: Ledger) -> None:
        """The weight sensor can re-report at the ending; the moment's figures win over
        the start's capture."""
        await ledger.use_cases.track_print_job.execute(started({TRAY_1: Grams.of(209)}))

        await ledger.use_cases.track_print_job.execute(
            ended(reported_usage={TRAY_1: Grams.of(209), TRAY_2: Grams.of(31)})
        )

        [job] = await stored_jobs(ledger)
        assert job.reported_usage == {TRAY_1: Grams.of(209), TRAY_2: Grams.of(31)}

    async def test_a_terminal_event_with_no_prior_row_creates_it_then(self, ledger: Ledger) -> None:
        """The integration restarted mid-print, so the start was never seen. The review
        must never be lost to a restart — the row is created at the ending."""
        await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FAILED, reported_usage={TRAY_1: Grams.of(209)})
        )

        [job] = await stored_jobs(ledger)
        assert job.state is PrintJobState.FAILED
        assert job.started_at == job.ended_at == ledger.clock.now()
        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.job_id == job.id
        assert review.reason is ReviewReason.FAILED

    async def test_the_printers_own_clock_is_stored_beside_the_ledgers_never_over_it(
        self, ledger: Ledger
    ) -> None:
        """The whole of Part 2's decision, in one assertion pair.

        The machine here says the print ran from 09:12 to 11:47 — nowhere near the fake
        clock this ledger stamps with — and both facts survive intact. `started_at` and
        `ended_at` stay this ledger's, because UC-04 derives every movement's `occurred_at`
        from `ended_at` and the whole ledger orders itself by that column
        (docs/04 UC-04); the machine's pair lands in two columns nothing compares.
        """
        machine_start = datetime(2026, 8, 4, 9, 12, tzinfo=UTC)
        machine_end = datetime(2026, 8, 4, 11, 47, tzinfo=UTC)
        await ledger.use_cases.track_print_job.execute(
            PrintStarted(name="bracket_v3.gcode.3mf", printer_started_at=machine_start)
        )
        ledger.clock.advance(minutes=42)

        await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FINISHED, printer_ended_at=machine_end)
        )

        [job] = await stored_jobs(ledger)
        assert (job.started_at, job.ended_at) == (EPOCH, ledger.clock.now())
        assert (job.printer_started_at, job.printer_ended_at) == (machine_start, machine_end)
        # The measurement the two columns exist for: the machine's, not the 42 minutes
        # this ledger's own clock happened to advance by.
        assert job.measured_duration == timedelta(hours=2, minutes=35)

    async def test_the_start_survives_an_ending_that_read_no_start_time(
        self, ledger: Ledger
    ) -> None:
        """The same rule the plan follows. A machine that had already reset `start_time`
        for the next job would otherwise take this print's start down with it."""
        machine_start = datetime(2026, 8, 4, 9, 12, tzinfo=UTC)
        await ledger.use_cases.track_print_job.execute(
            PrintStarted(name="bracket_v3.gcode.3mf", printer_started_at=machine_start)
        )

        await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FINISHED, printer_ended_at=datetime(2026, 8, 4, 11, 47, tzinfo=UTC))
        )

        [job] = await stored_jobs(ledger)
        assert job.printer_started_at == machine_start

    async def test_a_cancellation_records_the_machines_claim_without_measuring_by_it(
        self, ledger: Ledger
    ) -> None:
        """Upstream's end is derived from the time remaining, so a cancelled job reports
        the ending it was heading for rather than the one it had. Both figures are stored —
        the record keeps claims verbatim — and the duration is the ledger's 42 minutes,
        not the three hours the machine was still planning on (docs/05 §5.8)."""
        machine_start = datetime(2026, 8, 4, 9, 12, tzinfo=UTC)
        would_have_finished = datetime(2026, 8, 4, 14, 30, tzinfo=UTC)
        await ledger.use_cases.track_print_job.execute(
            PrintStarted(name="bracket_v3.gcode.3mf", printer_started_at=machine_start)
        )
        ledger.clock.advance(minutes=42)

        await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.CANCELLED, printer_ended_at=would_have_finished)
        )

        [job] = await stored_jobs(ledger)
        assert job.printer_ended_at == would_have_finished
        assert job.measured_duration == timedelta(minutes=42)

    async def test_a_machine_that_reported_neither_moment_stores_neither(
        self, ledger: Ledger
    ) -> None:
        """Every job before v1.4 is this shape, and so is every unavailable sensor. Null,
        never a stand-in from the ledger's own clock — two columns pretending to be the
        machine's report would be the exact lie the split exists to prevent."""
        await ledger.use_cases.track_print_job.execute(started())

        await ledger.use_cases.track_print_job.execute(ended())

        [job] = await stored_jobs(ledger)
        assert job.printer_started_at is None
        assert job.printer_ended_at is None
        assert job.measured_duration is None

    async def test_an_ending_ends_the_newest_running_job(self, ledger: Ledger) -> None:
        """A stale RUNNING row — an ending that never arrived — must not swallow the
        ending of the print that actually just stopped."""
        stale = await ledger.use_cases.track_print_job.execute(started())
        ledger.clock.advance(days=1)
        current = await ledger.use_cases.track_print_job.execute(started())

        ended_id = await ledger.use_cases.track_print_job.execute(ended())

        assert ended_id == current
        by_id = {job.id: job for job in await stored_jobs(ledger)}
        assert by_id[current].state is PrintJobState.CANCELLED
        assert by_id[stale].state is PrintJobState.RUNNING


class TestAFinishedPrint:
    async def test_a_finish_hands_the_figures_to_uc04_and_opens_no_review(
        self, ledger: Ledger
    ) -> None:
        """The seam as wired (Q4, closed): the job ran to completion, so no decision is
        needed — UC-04 deducts from every mounted slot and marks the job recorded in the
        same pass. Its full behaviour lives in test_record_print_consumption.py; this
        pins the handoff."""
        first = await a_spool(ledger, label="first")
        second = await a_spool(ledger, label="second")
        await ledger.use_cases.mount_spool.execute(first, TRAY_1)
        await ledger.use_cases.mount_spool.execute(second, TRAY_2)
        await ledger.use_cases.track_print_job.execute(started())

        await ledger.use_cases.track_print_job.execute(
            ended(
                PrintJobState.FINISHED,
                reported_usage={TRAY_1: Grams.of("38.2"), TRAY_2: Grams.of("9.4")},
            )
        )

        [job] = await stored_jobs(ledger)
        assert job.state is PrintJobState.FINISHED
        assert job.reported_usage == {TRAY_1: Grams.of("38.2"), TRAY_2: Grams.of("9.4")}
        assert job.consumption_recorded is True
        assert await SqliteReviewRepository(ledger.database).list_pending() == []
        assert ledger.events.of(ReviewOpened) == []
        assert (await ledger.use_cases.queries.detail(first)).summary.balance == Grams.of("961.8")
        assert (await ledger.use_cases.queries.detail(second)).summary.balance == Grams.of("990.6")

    async def test_a_finish_without_figures_records_none_not_zero(self, ledger: Ledger) -> None:
        """The per-tray attributes flicker (docs/12-field-notes.md): a finish can still
        arrive figureless. `None` keeps that fact — a retrieval failure must stay
        distinguishable from a claim of zero consumption — and UC-04's missing-figure
        branch turns it into a review instead of a silent zero."""
        await ledger.use_cases.track_print_job.execute(started(plan=None))

        await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FINISHED, reported_usage=None)
        )

        [job] = await stored_jobs(ledger)
        assert job.state is PrintJobState.FINISHED
        assert job.reported_usage is None
        assert job.consumption_recorded is True
        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.reason is ReviewReason.UNMAPPED_USAGE
        assert review.estimator_used is EstimatorKind.NONE


class TestDuplicateEndings:
    async def test_racing_deliveries_of_one_ending_open_exactly_one_review(
        self, interleaved_ledger: Ledger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Two deliveries of one ending, racing: both correlate to the same RUNNING job,
        the first opens the review, and the second finds it already open — a warning in
        the log, never a crash, and never a second decision item."""
        ledger = interleaved_ledger
        await ledger.use_cases.track_print_job.execute(started({TRAY_1: Grams.of(209)}))
        event = ended(PrintJobState.CANCELLED)

        with caplog.at_level(logging.WARNING):
            await asyncio.gather(
                ledger.use_cases.track_print_job.execute(event),
                ledger.use_cases.track_print_job.execute(event),
            )

        assert len(await SqliteReviewRepository(ledger.database).list_pending()) == 1
        assert "already has a pending review" in caplog.text
        [review_opened] = ledger.events.of(ReviewOpened)
        assert isinstance(review_opened, ReviewOpened)
        assert review_opened.reason is ReviewReason.CANCELLED
        # The duplicate rolled back whole: the job still carries the first ending's
        # claims, and the queue still holds exactly the one decision.
        [job] = await stored_jobs(ledger)
        assert job.state is PrintJobState.CANCELLED
