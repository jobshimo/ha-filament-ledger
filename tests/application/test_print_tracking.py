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
from custom_components.filament_ledger.application.review_queue import DismissReviewCommand
from custom_components.filament_ledger.domain.event import ReviewOpened
from custom_components.filament_ledger.domain.model.pending_review import ReviewCharge
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    PrinterSerial,
    PrintJobId,
    SpoolId,
    TrayRef,
)
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.percentage import Percentage
from custom_components.filament_ledger.domain.value.print_event import (
    UNKNOWN_JOB_NAME,
    PrintEnded,
    PrintEvent,
    PrintPlanObserved,
    PrintStarted,
)
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.domain.value.review import EstimatorKind, ReviewReason
from custom_components.filament_ledger.infrastructure.persistence.print_job_repository import (
    SqlitePrintJobRepository,
)
from custom_components.filament_ledger.infrastructure.persistence.review_repository import (
    SqliteReviewRepository,
)

from .conftest import A_PRINTER, ANOTHER_PRINTER, EPOCH, Ledger, a_tray

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


def started(
    plan: dict[TrayRef, Grams] | None = None,
    *,
    printer: PrinterSerial = A_PRINTER,
    printer_started_at: datetime | None = None,
    derived: bool = False,
    name: str = "bracket_v3.gcode.3mf",
) -> PrintStarted:
    return PrintStarted(
        name=name,
        printer=printer,
        plan=plan,
        printer_started_at=printer_started_at,
        derived=derived,
    )


def ended(
    outcome: PrintJobState = PrintJobState.CANCELLED,
    *,
    layer_reached: int | None = 71,
    total_layers: int | None = 209,
    reported_usage: dict[TrayRef, Grams] | None = None,
    raw_print_error: int | None = None,
    printer_started_at: datetime | None = None,
    printer_ended_at: datetime | None = None,
    printer: PrinterSerial = A_PRINTER,
    derived: bool = False,
    name: str = "bracket_v3.gcode.3mf",
) -> PrintEnded:
    return PrintEnded(
        outcome=outcome,
        name=name,
        printer=printer,
        derived=derived,
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


async def track(ledger: Ledger, event: PrintEvent) -> PrintJobId:
    """`execute`, for the events that must land on a job — which is most of them.

    The use case answers `None` for exactly two endings it drops on purpose: an inferred
    one with nothing running, and a second delivery of one already recorded
    (`TestTwoSignalsForOneEnding` drives both). Everywhere else a `None` would be the
    failure under test, so asserting it here states that once instead of narrowing a type
    at every call site.
    """
    job_id = await ledger.use_cases.track_print_job.execute(event)
    assert job_id is not None
    return job_id


class TestTwoSignalsForOneEnding:
    """Since v2.5 an ending reaches the ledger by two independent routes — the bus event
    and the status sensor (`bambu_gateway._DERIVED_OUTCOMES`) — because the first one is
    not delivered when a printer's connection drops across its own finish.

    The redundancy is only worth having if the second arrival is harmless, and *harmless*
    is not what the old code did: with no RUNNING row left it opened a new one, so the
    loser of the race would have charged the same print's grams a second time. These pin
    the guard from both sides — the duplicate must be swallowed, and a genuinely new print
    must not be.
    """

    async def test_an_inferred_ending_with_nothing_running_closes_nothing(
        self, ledger: Ledger
    ) -> None:
        """The level says `finish` on every idle machine. If inference could open a job,
        every restart would mint a phantom print and charge the last plan again."""
        job_id = await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FINISHED, derived=True)
        )

        assert job_id is None
        assert await stored_jobs(ledger) == []

    async def test_an_inferred_ending_closes_the_running_job_it_finds(self, ledger: Ledger) -> None:
        """The tonight case: the bus never announced anything, so the sensor has to."""
        started_id = await ledger.use_cases.track_print_job.execute(started())

        job_id = await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FINISHED, reported_usage={TRAY_1: Grams.of("248.41")}, derived=True)
        )

        assert job_id == started_id
        [job] = await stored_jobs(ledger)
        assert job.state is PrintJobState.FINISHED
        assert job.reported_usage == {TRAY_1: Grams.of("248.41")}

    async def test_the_signal_that_loses_the_race_opens_no_second_row(self, ledger: Ledger) -> None:
        """Both routes report one finish seconds apart. The second finds the row already
        closed — and must leave it closed rather than open another and charge again."""
        await ledger.use_cases.track_print_job.execute(started())
        first = await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FINISHED, reported_usage={TRAY_1: Grams.of(38)})
        )
        ledger.clock.advance(seconds=2)

        second = await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FINISHED, reported_usage={TRAY_1: Grams.of(38)})
        )

        assert second is None
        [job] = await stored_jobs(ledger)
        assert job.id == first

    async def test_a_reprint_of_the_same_file_beyond_the_window_opens_its_own_row(
        self, ledger: Ledger
    ) -> None:
        """The guard must not eat a real print. Past the window the same name is a second
        job — which is exactly the restart-mid-print repair the branch exists for."""
        await ledger.use_cases.track_print_job.execute(started())
        await ledger.use_cases.track_print_job.execute(ended(PrintJobState.FINISHED))
        ledger.clock.advance(minutes=6)

        second = await ledger.use_cases.track_print_job.execute(ended(PrintJobState.FINISHED))

        assert second is not None
        assert len(await stored_jobs(ledger)) == 2

    async def test_a_different_print_ending_moments_later_is_not_a_duplicate(
        self, ledger: Ledger
    ) -> None:
        """Two short prints can stop within the window. The name is what tells one
        ending from the other, and without it the second job would vanish."""
        await ledger.use_cases.track_print_job.execute(started())
        await ledger.use_cases.track_print_job.execute(ended(PrintJobState.FINISHED))
        ledger.clock.advance(seconds=30)

        second = await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FINISHED, name="a_different_part.gcode.3mf")
        )

        assert second is not None
        assert [job.name for job in await stored_jobs(ledger)] == [
            "a_different_part.gcode.3mf",
            "bracket_v3.gcode.3mf",
        ]

    async def test_an_inference_leaves_a_stale_running_row_alone(self, ledger: Ledger) -> None:
        """Upstream announces a start per attempt, and it announced two for one job on the
        reference machine (22:07:51 and 22:15:38 UTC, 2026-08-08, same file, no ending
        between). The ending closes the newest and the older row is left standing.

        The startup pass then offers that leftover a `finish` on *every* restart. Closing it
        would charge the machine's current plan a second time, for a print already paid for.
        """
        await ledger.use_cases.track_print_job.execute(started())
        ledger.clock.advance(minutes=8)
        await ledger.use_cases.track_print_job.execute(started())
        await ledger.use_cases.track_print_job.execute(ended(PrintJobState.FINISHED))
        ledger.clock.advance(hours=3)

        job_id = await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FINISHED, reported_usage={TRAY_1: Grams.of(100)}, derived=True)
        )

        assert job_id is None
        states = [job.state for job in await stored_jobs(ledger)]
        assert states == [PrintJobState.FINISHED, PrintJobState.RUNNING]

    async def test_a_stale_row_does_not_hide_a_real_ending_from_the_bus(
        self, ledger: Ledger
    ) -> None:
        """The same leftover, and an *announced* ending. That one fired just now, so the
        newest running row is what it describes and the guard must not swallow it."""
        await ledger.use_cases.track_print_job.execute(started())
        ledger.clock.advance(minutes=8)
        current = await ledger.use_cases.track_print_job.execute(started())

        job_id = await ledger.use_cases.track_print_job.execute(ended(PrintJobState.FINISHED))

        assert job_id == current

    async def test_a_duplicate_never_charges_the_same_print_twice(self, ledger: Ledger) -> None:
        """The whole point, measured where it hurts: the spool's balance."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        await ledger.use_cases.track_print_job.execute(started())
        usage = {TRAY_1: Grams.of("248.41")}
        await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FINISHED, reported_usage=usage)
        )
        ledger.clock.advance(seconds=1)

        await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FINISHED, reported_usage=usage, derived=True)
        )

        detail = await ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of("751.59")


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
            PrintStarted(
                name="bracket_v3.gcode.3mf", printer=A_PRINTER, printer_started_at=machine_start
            )
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
            PrintStarted(
                name="bracket_v3.gcode.3mf", printer=A_PRINTER, printer_started_at=machine_start
            )
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
            PrintStarted(
                name="bracket_v3.gcode.3mf", printer=A_PRINTER, printer_started_at=machine_start
            )
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
        stale = await track(ledger, started())
        ledger.clock.advance(days=1)
        current = await track(ledger, started())

        ended_id = await track(ledger, ended())

        assert ended_id == current
        by_id = {job.id: job for job in await stored_jobs(ledger)}
        assert by_id[current].state is PrintJobState.CANCELLED
        assert by_id[stale].state is PrintJobState.RUNNING


class TestCorrelationAcrossPrinters:
    """Two machines printing at once, which is the whole of what v2.0 had to get right.

    Correlation by state alone would hand each ending to whichever job started last, and
    the ending's per-tray figures ride with it — so UC-04 would deduct one printer's grams
    from the spools in the other printer's trays.
    """

    async def test_each_ending_closes_its_own_machines_job(self, ledger: Ledger) -> None:
        """The newest RUNNING row belongs to the *second* machine; the first machine's
        ending must reach past it to the job that is actually its own."""
        first = await track(ledger, started(printer=A_PRINTER))
        ledger.clock.advance(minutes=10)
        second = await track(ledger, started(printer=ANOTHER_PRINTER))
        ledger.clock.advance(minutes=10)

        ended_id = await track(ledger, ended(PrintJobState.FINISHED, printer=A_PRINTER))

        assert ended_id == first
        by_id = {job.id: job for job in await stored_jobs(ledger)}
        assert by_id[first].state is PrintJobState.FINISHED
        assert by_id[second].state is PrintJobState.RUNNING

    async def test_a_machines_grams_are_deducted_from_its_own_trays(self, ledger: Ledger) -> None:
        """The consequence, spelled out in balances rather than in states.

        One spool in each machine's tray 1. Both machines print; the first finishes and
        reports 38.2 g. Mis-correlation would put those grams on the job whose trays belong
        to the other machine, and UC-04 would deduct them from the wrong reel — visible here
        as the second spool losing filament it never printed.
        """
        here = a_tray(1, printer=A_PRINTER)
        there = a_tray(1, printer=ANOTHER_PRINTER)
        mine = await a_spool(ledger, label="on the first machine")
        yours = await a_spool(ledger, label="on the second machine")
        await ledger.use_cases.mount_spool.execute(mine, here)
        await ledger.use_cases.mount_spool.execute(yours, there)
        await ledger.use_cases.track_print_job.execute(started(printer=A_PRINTER))
        ledger.clock.advance(minutes=5)
        await ledger.use_cases.track_print_job.execute(started(printer=ANOTHER_PRINTER))
        ledger.clock.advance(minutes=5)

        await ledger.use_cases.track_print_job.execute(
            ended(
                PrintJobState.FINISHED,
                printer=A_PRINTER,
                reported_usage={here: Grams.of("38.2")},
            )
        )

        assert (await ledger.use_cases.queries.detail(mine)).summary.balance == Grams.of("961.8")
        assert (await ledger.use_cases.queries.detail(yours)).summary.balance == Grams.of(1000)

    async def test_a_review_opens_against_the_machine_that_was_cancelled(
        self, ledger: Ledger
    ) -> None:
        """A cancellation on one machine must not freeze the other machine's plan into a
        decision item the owner is then asked to approve."""
        here = a_tray(1, printer=A_PRINTER)
        there = a_tray(1, printer=ANOTHER_PRINTER)
        await ledger.use_cases.track_print_job.execute(
            started({here: Grams.of(209)}, printer=A_PRINTER)
        )
        await ledger.use_cases.track_print_job.execute(
            started({there: Grams.of(500)}, printer=ANOTHER_PRINTER)
        )

        await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.CANCELLED, printer=A_PRINTER)
        )

        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        # 71 of 209 layers of the *first* machine's 209 g plan.
        assert review.estimated_usage == {here: Grams.of(71)}

    async def test_a_long_job_stays_correlatable_behind_another_machines_burst(
        self, ledger: Ledger
    ) -> None:
        """Why the correlation window is counted per printer.

        One machine runs a long print while the other runs a dozen short ones. A window of
        ten shared across the ledger would have pushed the long job out of view, and its
        ending would have opened a duplicate row instead of closing the job it belongs to.
        """
        long_job = await ledger.use_cases.track_print_job.execute(started(printer=A_PRINTER))
        for _ in range(12):
            ledger.clock.advance(minutes=20)
            await ledger.use_cases.track_print_job.execute(started(printer=ANOTHER_PRINTER))
            ledger.clock.advance(minutes=1)
            await ledger.use_cases.track_print_job.execute(
                ended(PrintJobState.FINISHED, printer=ANOTHER_PRINTER)
            )
        ledger.clock.advance(hours=1)

        ended_id = await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FINISHED, printer=A_PRINTER)
        )

        assert ended_id == long_job

    async def test_a_job_that_names_no_machine_is_correlated_to_by_nobody(
        self, ledger: Ledger
    ) -> None:
        """The migrated row, and the price of refusing to guess about it.

        A print that spanned the upgrade to migration 0008 leaves a RUNNING row naming no
        machine. Its ending arrives naming one, finds nothing of its own, and opens a fresh
        row — the same shape a restart already produced. The stale row stays verbatim and
        reclassifiable; letting it match would be inventing which machine it belonged to.
        """
        jobs = SqlitePrintJobRepository(ledger.database)
        await jobs.save(
            PrintJob(
                id=PrintJobId("job-from-before-0008"),
                name="bracket_v3.gcode.3mf",
                state=PrintJobState.RUNNING,
                started_at=EPOCH,
            )
        )
        ledger.clock.advance(minutes=42)

        ended_id = await track(ledger, ended(PrintJobState.CANCELLED, printer=A_PRINTER))

        assert ended_id != PrintJobId("job-from-before-0008")
        by_id = {job.id: job for job in await stored_jobs(ledger)}
        assert by_id[PrintJobId("job-from-before-0008")].state is PrintJobState.RUNNING
        assert by_id[ended_id].state is PrintJobState.CANCELLED
        assert by_id[ended_id].printer == A_PRINTER


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


class TestAPrintNobodySawEndGoesToTheQueue:
    """The gap this release closes, measured on the reference instance.

    `_started` has always detected the shape: the machine is plainly running a print that
    the open row does not describe, so that row's ending never arrived. It fired ten times
    in nine days on the reference machine — 08-12 15:30 through 08-13 23:37 — and opened
    zero reviews, because the discovery ended at a `LOGGER.warning`.

    The user's symptom was the consequence: a print ran, its filament was never deducted,
    and the ledger said nothing at all. A silent zero is the one answer an inventory must
    never give.
    """

    async def test_the_unaccounted_job_lands_in_the_review_queue(self, ledger: Ledger) -> None:
        await ledger.use_cases.track_print_job.execute(started())
        orphan = (await stored_jobs(ledger))[0].id
        ledger.clock.advance(minutes=8)

        await ledger.use_cases.track_print_job.execute(started())

        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.job_id == orphan

    async def test_the_reason_says_no_ending_was_ever_recognised(self, ledger: Ledger) -> None:
        """`UNCLASSIFIED` already means exactly *the job ended without a recognisable
        event*, so it is reused rather than joined by a near-synonym."""
        await ledger.use_cases.track_print_job.execute(started())
        ledger.clock.advance(minutes=8)

        await ledger.use_cases.track_print_job.execute(started())

        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.reason is ReviewReason.UNCLASSIFIED

    async def test_the_orphan_row_stays_running_because_that_is_what_was_observed(
        self, ledger: Ledger
    ) -> None:
        """Its state is the ledger's own observation, and the ledger observed no ending.
        `FINISHED` would claim a completion nobody saw; the outcome is the review's to
        settle, by the one party who actually knows."""
        await ledger.use_cases.track_print_job.execute(started())
        ledger.clock.advance(minutes=8)

        await ledger.use_cases.track_print_job.execute(started())

        states = [job.state for job in await stored_jobs(ledger)]
        assert states == [PrintJobState.RUNNING, PrintJobState.RUNNING]

    async def test_the_review_is_announced(self, ledger: Ledger) -> None:
        """Same event any other review raises, so an existing notification automation
        covers this without knowing it is new."""
        await ledger.use_cases.track_print_job.execute(started())
        orphan = (await stored_jobs(ledger))[0].id
        ledger.clock.advance(minutes=8)

        await ledger.use_cases.track_print_job.execute(started())

        [opened] = ledger.events.of(ReviewOpened)
        assert isinstance(opened, ReviewOpened)
        assert opened.job_id == orphan

    async def test_the_print_now_running_still_gets_its_row(self, ledger: Ledger) -> None:
        """The queue must never cost the live print its tracking — that would trade one
        unaccounted print for the next one."""
        await ledger.use_cases.track_print_job.execute(started())
        ledger.clock.advance(minutes=8)

        current = await ledger.use_cases.track_print_job.execute(started())

        assert current is not None
        assert current == (await stored_jobs(ledger))[0].id

    async def test_a_third_start_does_not_queue_the_same_orphan_twice(self, ledger: Ledger) -> None:
        """Upstream announces a start per attempt. The orphan is already waiting, and a
        queue that refuses a duplicate is the ordinary outcome, not an error."""
        await ledger.use_cases.track_print_job.execute(started())
        ledger.clock.advance(minutes=8)
        await ledger.use_cases.track_print_job.execute(started())
        ledger.clock.advance(minutes=8)

        third = await ledger.use_cases.track_print_job.execute(started())

        assert third is not None
        pending = await SqliteReviewRepository(ledger.database).list_pending()
        assert len(pending) == 2, "one per orphan, never two for the same one"

    async def test_a_healthy_print_never_looks_unaccounted(self, ledger: Ledger) -> None:
        """The guard that keeps this from firing on every ordinary print: a closed job is
        not an open row, so the next start finds nothing to send anywhere.

        Asserted on the *reason* rather than on an empty queue, because a finished print
        that reported no figures opens an `UNMAPPED_USAGE` review of its own — long-
        standing, correct, and none of this method's business.
        """
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        await ledger.use_cases.track_print_job.execute(started({TRAY_1: Grams.of(209)}))
        await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FINISHED, reported_usage={TRAY_1: Grams.of(209)})
        )
        ledger.clock.advance(hours=2)

        await ledger.use_cases.track_print_job.execute(started())

        reasons = [r.reason for r in await SqliteReviewRepository(ledger.database).list_pending()]
        assert ReviewReason.UNCLASSIFIED not in reasons


A_PREVIOUS_PRINT = datetime(2026, 8, 14, 15, 51, 13, tzinfo=UTC)
THIS_PRINT = datetime(2026, 8, 14, 21, 15, 2, tzinfo=UTC)


class TestTheStartTimeThatIsStaleForTheFirstMinute:
    """v2.6.1's duplicate-row bug, pinned from the production data that exposed it.

    The two timestamps above are verbatim from the reference ledger. At the instant the
    stage sensor turns, `start_time` still holds the value of the print *before* — so the
    inferred start opened a row stamped 15:51:13 for a print that began at 21:15:02, the
    announced start arrived 25.6 s later carrying the corrected figure, and `_same_print`
    read five and a half hours of disagreement as *a different print*. Every print
    therefore left one row behind, and every one of those rows went to the review queue
    carrying exactly the grams the surviving row had already deducted.

    Twelve `UNCLASSIFIED` reviews on the reference instance, eleven of them false.
    """

    async def test_the_corrected_start_time_does_not_open_a_second_row(
        self, ledger: Ledger
    ) -> None:
        await ledger.use_cases.track_print_job.execute(
            started(printer_started_at=A_PREVIOUS_PRINT, derived=True)
        )
        ledger.clock.advance(seconds=26)

        second = await ledger.use_cases.track_print_job.execute(
            started(printer_started_at=THIS_PRINT)
        )

        assert second is None, "the announcement describes the print already being tracked"
        assert len(await stored_jobs(ledger)) == 1

    async def test_nothing_reaches_the_queue_for_a_print_being_tracked(
        self, ledger: Ledger
    ) -> None:
        """The symptom the user reported: cards for prints that were already deducted."""
        await ledger.use_cases.track_print_job.execute(
            started(printer_started_at=A_PREVIOUS_PRINT, derived=True)
        )
        ledger.clock.advance(seconds=26)

        await ledger.use_cases.track_print_job.execute(started(printer_started_at=THIS_PRINT))

        reasons = [r.reason for r in await SqliteReviewRepository(ledger.database).list_pending()]
        assert ReviewReason.UNCLASSIFIED not in reasons

    async def test_the_row_adopts_the_corrected_start_time(self, ledger: Ledger) -> None:
        """Suppressing the second row is only half of it. Left alone, the survivor keeps a
        figure wrong by a whole job — and that figure is the machine's own elapsed time."""
        await ledger.use_cases.track_print_job.execute(
            started(printer_started_at=A_PREVIOUS_PRINT, derived=True)
        )
        ledger.clock.advance(seconds=26)

        await ledger.use_cases.track_print_job.execute(started(printer_started_at=THIS_PRINT))

        [job] = await stored_jobs(ledger)
        assert job.printer_started_at == THIS_PRINT

    async def test_a_stale_reading_never_moves_the_recorded_start_backwards(
        self, ledger: Ledger
    ) -> None:
        """Corrections only ever move forward — a stale value names an earlier print and
        upstream's truncation rounds down. So whichever signal speaks last does not win;
        the later reading does."""
        await ledger.use_cases.track_print_job.execute(started(printer_started_at=THIS_PRINT))
        ledger.clock.advance(seconds=26)

        await ledger.use_cases.track_print_job.execute(
            started(printer_started_at=A_PREVIOUS_PRINT, derived=True)
        )

        [job] = await stored_jobs(ledger)
        assert job.printer_started_at == THIS_PRINT

    async def test_a_print_that_really_went_unobserved_still_reaches_the_queue(
        self, ledger: Ledger
    ) -> None:
        """The safety net this fix must not cost, asserted with the same disagreeing
        timestamps that the correction case uses.

        Only the row's age differs — and that is the whole point. The one print the
        reference instance genuinely lost had been open 4 h 51 m when the next start found
        it; the slowest correction took 55.9 s. Two orders of magnitude apart, and the
        window sits between them.
        """
        await ledger.use_cases.track_print_job.execute(
            started(printer_started_at=A_PREVIOUS_PRINT, derived=True)
        )
        orphan = (await stored_jobs(ledger))[0].id
        ledger.clock.advance(hours=4, minutes=51)

        await ledger.use_cases.track_print_job.execute(started(printer_started_at=THIS_PRINT))

        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.job_id == orphan
        assert review.reason is ReviewReason.UNCLASSIFIED
        assert len(await stored_jobs(ledger)) == 2, "the live print still gets its own row"

    async def test_the_window_is_the_row_age_and_not_the_timestamp_gap(
        self, ledger: Ledger
    ) -> None:
        """Just past the window the answer flips, and it flips on the ledger's own clock —
        the two `start_time` readings are the same pair as the passing case above."""
        await ledger.use_cases.track_print_job.execute(
            started(printer_started_at=A_PREVIOUS_PRINT, derived=True)
        )
        ledger.clock.advance(minutes=5, seconds=1)

        second = await ledger.use_cases.track_print_job.execute(
            started(printer_started_at=THIS_PRINT)
        )

        assert second is not None
        assert len(await stored_jobs(ledger)) == 2


def plan_observed(
    plan: dict[TrayRef, Grams] | None = None,
    *,
    name: str | None = None,
    printer: PrinterSerial = A_PRINTER,
    printer_started_at: datetime | None = None,
) -> PrintPlanObserved:
    return PrintPlanObserved(
        printer=printer,
        plan=plan if plan is not None else {TRAY_1: Grams.of("50.82")},
        name=name,
        printer_started_at=printer_started_at,
    )


class TestAStaleRowRefusesAnotherPrintsPlan:
    """The write that never asked (docs/12-field-notes.md, 2026-08-31).

    An observation is delivered to whichever row is open, and after a dead reconnection
    window the open row can be an orphan from hours ago. Adopting onto it dressed the
    orphan in the running print's name and figures, and its review card then offered
    grams the running print's own row deducted again — a double charge wearing a
    review's clothing.
    """

    async def test_an_old_row_refuses_figures_stamped_for_another_print(
        self, ledger: Ledger
    ) -> None:
        await ledger.use_cases.track_print_job.execute(started(printer_started_at=A_PREVIOUS_PRINT))
        ledger.clock.advance(hours=4)

        result = await ledger.use_cases.track_print_job.execute(
            plan_observed(
                {TRAY_1: Grams.of("62.23")},
                name="0.08mm layer, 2 walls, 15% infill.3mf",
                printer_started_at=THIS_PRINT,
            )
        )

        assert result is None
        [job] = await stored_jobs(ledger)
        assert job.reported_usage is None, "an orphan keeps no figures that are not its own"
        assert job.name != "0.08mm layer, 2 walls, 15% infill.3mf"

    async def test_a_young_row_still_adopts_through_the_stale_sensor(self, ledger: Ledger) -> None:
        """`start_time` names the previous print for the first moments of every job, so a
        fresh row disagreeing with the sensor is the ordinary case, not the poisoning."""
        await ledger.use_cases.track_print_job.execute(started(printer_started_at=THIS_PRINT))
        ledger.clock.advance(minutes=4)

        await ledger.use_cases.track_print_job.execute(
            plan_observed({TRAY_1: Grams.of(10)}, printer_started_at=A_PREVIOUS_PRINT)
        )

        [job] = await stored_jobs(ledger)
        assert job.reported_usage == {TRAY_1: Grams.of(10)}

    async def test_an_unstamped_observation_adopts_as_it_always_did(self, ledger: Ledger) -> None:
        await ledger.use_cases.track_print_job.execute(started(printer_started_at=A_PREVIOUS_PRINT))
        ledger.clock.advance(hours=4)

        await ledger.use_cases.track_print_job.execute(plan_observed({TRAY_1: Grams.of(9)}))

        [job] = await stored_jobs(ledger)
        assert job.reported_usage == {TRAY_1: Grams.of(9)}

    async def test_an_old_row_under_the_sentinel_is_not_renamed_either(
        self, ledger: Ledger
    ) -> None:
        """The guard is by identity, and a row that would dearly like a name is no
        exception: the name and the figures belong to the print running now."""
        await ledger.use_cases.track_print_job.execute(
            started(name=UNKNOWN_JOB_NAME, printer_started_at=A_PREVIOUS_PRINT)
        )
        ledger.clock.advance(hours=4)

        result = await ledger.use_cases.track_print_job.execute(
            plan_observed(name="bracket_v3.gcode.3mf", printer_started_at=THIS_PRINT)
        )

        assert result is None
        [job] = await stored_jobs(ledger)
        assert job.name == UNKNOWN_JOB_NAME
        assert job.reported_usage is None


class TestADecidedOrphanIsNotAskedAgain:
    """Dismiss, print, the same ghost again (observed live, 2026-08-31).

    Deciding an orphan's review settles the job. Before that write existed, the stale
    RUNNING row was re-detected on every later start and re-minted its card each time
    the user resolved it — an unkillable review for a print that ended hours ago.
    """

    async def test_a_dismissed_orphan_opens_no_second_review(self, ledger: Ledger) -> None:
        spool = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool, TRAY_1)
        await ledger.use_cases.track_print_job.execute(started(printer_started_at=A_PREVIOUS_PRINT))
        ledger.clock.advance(hours=4)
        await ledger.use_cases.track_print_job.execute(started(printer_started_at=THIS_PRINT))
        reviews = SqliteReviewRepository(ledger.database)
        [orphan_review] = await reviews.list_pending()
        await ledger.use_cases.dismiss_review.execute(
            DismissReviewCommand(review_id=orphan_review.id)
        )
        # The live print finishes cleanly — mounted spool, reported figures — so its own
        # ending opens nothing and the orphan is back to being the newest RUNNING row.
        await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.FINISHED, reported_usage={TRAY_1: Grams.of(30)})
        )
        ledger.clock.advance(hours=1)

        await ledger.use_cases.track_print_job.execute(
            started(printer_started_at=THIS_PRINT + timedelta(hours=1))
        )

        assert await reviews.list_pending() == [], "a decided orphan is not asked again"
        assert len(await stored_jobs(ledger)) == 3, "the new print still gets its row"


class TestThePlanIsPersistedWhileTheJobRuns:
    """Figures the machine published mid-print used to live only in the gateway's memory.

    They reached the row at the ending and nowhere else, so a job whose ending never
    arrived kept `reported_usage` null — and the review it eventually opened had a filename
    and nothing else on it. On the reference instance that was 62.23 g across three trays,
    published 24 seconds after the start, held, and dropped.
    """

    async def test_the_running_row_carries_the_figures(self, ledger: Ledger) -> None:
        job_id = await ledger.use_cases.track_print_job.execute(started())

        await ledger.use_cases.track_print_job.execute(
            plan_observed({TRAY_1: Grams.of("50.82"), TRAY_2: Grams.of("7.65")})
        )

        [job] = await stored_jobs(ledger)
        assert job.id == job_id
        assert job.reported_usage == {TRAY_1: Grams.of("50.82"), TRAY_2: Grams.of("7.65")}
        assert job.state is PrintJobState.RUNNING, "observing figures does not end a job"

    async def test_a_later_reading_supersedes_an_earlier_one(self, ledger: Ledger) -> None:
        """The sensor is republished in bursts through the print; the last one is the
        machine's current answer."""
        await ledger.use_cases.track_print_job.execute(started())
        await ledger.use_cases.track_print_job.execute(plan_observed({TRAY_1: Grams.of(10)}))

        await ledger.use_cases.track_print_job.execute(plan_observed({TRAY_1: Grams.of(31)}))

        [job] = await stored_jobs(ledger)
        assert job.reported_usage == {TRAY_1: Grams.of(31)}

    async def test_the_name_is_corrected_when_the_file_sensor_catches_up(
        self, ledger: Ledger
    ) -> None:
        """The file sensor lags the start too, so a row opened at the first `prepare`
        carries the previous print's filename until something refreshes it."""
        await ledger.use_cases.track_print_job.execute(started())

        await ledger.use_cases.track_print_job.execute(
            plan_observed(name="2429842-Royal Crest.gcode")
        )

        [job] = await stored_jobs(ledger)
        assert job.name == "2429842-Royal Crest.gcode"

    async def test_an_unnamed_observation_leaves_the_name_alone(self, ledger: Ledger) -> None:
        """`None` is *the sensor did not say*, never a blank name."""
        await ledger.use_cases.track_print_job.execute(started())

        await ledger.use_cases.track_print_job.execute(plan_observed(name=None))

        [job] = await stored_jobs(ledger)
        assert job.name == "bracket_v3.gcode.3mf"

    async def test_a_row_opened_under_the_sentinel_takes_the_observations_name(
        self, ledger: Ledger
    ) -> None:
        """Every name sensor can be silent at the start — a restart, a reconnect — so the
        row opens as `unknown print`. The first named observation is the row learning
        what it is called, and it lands with the figures in one write."""
        await ledger.use_cases.track_print_job.execute(started(name=UNKNOWN_JOB_NAME))

        await ledger.use_cases.track_print_job.execute(
            plan_observed({TRAY_1: Grams.of("21.88")}, name="bracket_v3.gcode.3mf")
        )

        [job] = await stored_jobs(ledger)
        assert job.name == "bracket_v3.gcode.3mf"
        assert job.reported_usage == {TRAY_1: Grams.of("21.88")}

    async def test_an_observation_that_only_knows_the_sentinel_leaves_a_named_row_alone(
        self, ledger: Ledger
    ) -> None:
        """The figures still land; the name is not replaced by the admission that no
        sensor spoke when they were read."""
        await ledger.use_cases.track_print_job.execute(started())

        await ledger.use_cases.track_print_job.execute(
            plan_observed({TRAY_1: Grams.of(31)}, name=UNKNOWN_JOB_NAME)
        )

        [job] = await stored_jobs(ledger)
        assert job.name == "bracket_v3.gcode.3mf"
        assert job.reported_usage == {TRAY_1: Grams.of(31)}

    async def test_figures_with_no_running_job_write_nothing(self, ledger: Ledger) -> None:
        """The sensor republishes between prints. Inventing a row for that would mint a
        phantom job out of a weight reading."""
        job_id = await ledger.use_cases.track_print_job.execute(plan_observed())

        assert job_id is None
        assert await stored_jobs(ledger) == []

    async def test_the_ending_still_wins(self, ledger: Ledger) -> None:
        """The ending's own figures overwrite whatever the row was carrying — this feeds
        the fallback, it does not compete with the ending."""
        await ledger.use_cases.track_print_job.execute(started())
        await ledger.use_cases.track_print_job.execute(plan_observed({TRAY_1: Grams.of(10)}))

        await ledger.use_cases.track_print_job.execute(
            ended(PrintJobState.CANCELLED, reported_usage={TRAY_1: Grams.of(209)})
        )

        [job] = await stored_jobs(ledger)
        assert job.reported_usage == {TRAY_1: Grams.of(209)}

    async def test_an_ending_with_no_figures_keeps_the_observed_ones(self, ledger: Ledger) -> None:
        """The whole point of the fallback: it used to fall back to null."""
        await ledger.use_cases.track_print_job.execute(started())
        await ledger.use_cases.track_print_job.execute(plan_observed({TRAY_1: Grams.of(31)}))

        await ledger.use_cases.track_print_job.execute(ended(PrintJobState.CANCELLED))

        [job] = await stored_jobs(ledger)
        assert job.reported_usage == {TRAY_1: Grams.of(31)}

    async def test_the_orphans_review_is_no_longer_empty(self, ledger: Ledger) -> None:
        """**The user-visible point.** A print whose ending nobody saw now reaches the
        queue carrying the machine's own per-tray figures, so the card asks a question the
        user can answer instead of showing a filename and a blank."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        await ledger.use_cases.track_print_job.execute(started())
        orphan = (await stored_jobs(ledger))[0].id
        await ledger.use_cases.track_print_job.execute(plan_observed({TRAY_1: Grams.of("62.23")}))
        ledger.clock.advance(minutes=8)

        # The next print begins; the row nobody closed is discovered and queued.
        await ledger.use_cases.track_print_job.execute(started())

        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.job_id == orphan
        assert review.estimated_usage != {}, "the card would be blank"
        assert review.charges == [(TRAY_1, ReviewCharge(spool_id, Grams.of("62.23")))]
