"""PrintJob invariants.

The entity is a record of the printer's claims, held verbatim — so the validation is
about physical impossibility, never about second-guessing the report.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from custom_components.filament_ledger.domain.error import InvalidValueError
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState

from .conftest import A_JOB_ID, EPOCH, a_cancelled_job, a_tray, at


class TestValidation:
    def test_a_negative_layer_count_cannot_exist(self) -> None:
        with pytest.raises(InvalidValueError):
            a_cancelled_job(layer_reached=-1)

    def test_a_plan_of_zero_layers_cannot_exist(self) -> None:
        """`total_layers` divides an estimate; zero here would be a crash deferred to the
        worst possible moment — inside the estimator, days later, during a review."""
        with pytest.raises(InvalidValueError):
            a_cancelled_job(total_layers=0)

    def test_a_job_cannot_end_before_it_started(self) -> None:
        job = a_cancelled_job()
        with pytest.raises(InvalidValueError):
            dataclasses.replace(job, ended_at=at(days=-1))

    def test_reported_usage_cannot_be_negative(self) -> None:
        with pytest.raises(InvalidValueError):
            a_cancelled_job(reported_usage={a_tray(1): Grams.of(-5)})

    def test_a_zero_usage_tray_is_a_legitimate_report(self) -> None:
        """The printer reports 0 g for a tray the job loaded but barely touched. Refusing
        it would drop the tray from the record entirely, which is a different claim."""
        job = a_cancelled_job(reported_usage={a_tray(1): Grams.zero()})
        assert job.reported_usage == {a_tray(1): Grams.zero()}

    def test_a_missing_usage_report_is_not_an_empty_one(self) -> None:
        """`None` means the figure never materialised; `{}` would mean the printer named
        no trays. A missing figure is not a figure of zero (docs/04-use-cases.md UC-04)."""
        assert a_cancelled_job(reported_usage=None).reported_usage is None


def a_finished_job(**overrides: object) -> PrintJob:
    """The same worked example, run to completion: 90 minutes on the ledger's clock."""
    return dataclasses.replace(
        a_cancelled_job(),
        state=PrintJobState.FINISHED,
        **overrides,  # type: ignore[arg-type]
    )


class TestMeasuredDuration:
    """How long the print ran — the printer's own answer where it is a measurement."""

    def test_the_ledgers_pair_is_used_when_the_machine_said_nothing(self) -> None:
        """Every job recorded before v1.4 is this shape, and so is every job on a machine
        whose timestamp sensors are unavailable."""
        assert a_finished_job().measured_duration == timedelta(minutes=90)

    def test_the_printers_pair_wins_over_the_ledgers_for_a_finished_print(self) -> None:
        """The ledger's pair is bounded by when Home Assistant *heard* — a restart, a
        reload or a busy bus lands inside that subtraction, and none of it happened to the
        print. The machine's own pair measures the print."""
        job = a_finished_job(printer_started_at=at(minutes=2), printer_ended_at=at(minutes=85))

        assert job.measured_duration == timedelta(minutes=83)

    def test_an_interrupted_print_keeps_the_ledgers_pair(self) -> None:
        """The one that would have done real damage quietly.

        Upstream derives `end_time` from the time remaining, so a job cancelled at layer 71
        still reports the ending it was heading for — here, three hours after it actually
        stopped. Preferring it would report a 90-minute print as a 285-minute one, in the
        card whose whole claim is that it measures rather than estimates. An interrupted
        print is different in kind (docs/adr/0004), and this is where that costs something.
        """
        job = dataclasses.replace(
            a_cancelled_job(),
            printer_started_at=EPOCH,
            printer_ended_at=at(minutes=285),
        )

        assert job.measured_duration == timedelta(minutes=90)

    def test_an_interrupted_print_still_records_what_the_machine_claimed(self) -> None:
        """Not used is not discarded. The record keeps the printer's claims verbatim, the
        way it keeps `raw_gcode_state`, so a later reading of them stays possible."""
        job = dataclasses.replace(
            a_cancelled_job(),
            printer_started_at=EPOCH,
            printer_ended_at=at(minutes=285),
        )

        assert job.printer_started_at == EPOCH
        assert job.printer_ended_at == at(minutes=285)

    def test_the_two_clocks_are_never_subtracted_from_each_other(self) -> None:
        """A half-reported pair falls back whole rather than borrowing the other clock's
        half. Mixing them would measure the offset between two clocks and call it a print.
        """
        started_only = a_finished_job(printer_started_at=at(minutes=2))
        ended_only = a_finished_job(printer_ended_at=at(minutes=85))

        assert started_only.measured_duration == timedelta(minutes=90)
        assert ended_only.measured_duration == timedelta(minutes=90)

    def test_an_incoherent_printer_pair_falls_back_rather_than_going_negative(self) -> None:
        """An ending read after the machine reset `start_time` for the next job. The events
        record the pair verbatim — refusing it at the boundary would cost a job row and the
        review it carries — so this is where it has to make sense, and it declines."""
        job = a_finished_job(printer_started_at=at(minutes=200), printer_ended_at=at(minutes=85))

        assert job.measured_duration == timedelta(minutes=90)

    def test_a_job_that_lost_its_start_to_a_restart_has_no_measurable_duration(self) -> None:
        """Both ledger timestamps are the moment the ending arrived. That row's duration is
        zero, and zero is not how long a print took (docs/06 §6.7)."""
        job = PrintJob(
            id=A_JOB_ID,
            name="recovered.3mf",
            state=PrintJobState.FINISHED,
            started_at=EPOCH,
            ended_at=EPOCH,
        )

        assert job.measured_duration is None

    def test_that_same_row_becomes_measurable_when_the_machine_reported(self) -> None:
        """The restart cost the ledger its start; it did not cost the printer anything."""
        job = PrintJob(
            id=A_JOB_ID,
            name="recovered.3mf",
            state=PrintJobState.FINISHED,
            started_at=EPOCH,
            ended_at=EPOCH,
            printer_started_at=at(minutes=-40),
            printer_ended_at=EPOCH,
        )

        assert job.measured_duration == timedelta(minutes=40)

    def test_a_running_job_has_not_taken_any_time_yet(self) -> None:
        """No ending in either pair. `None` rather than *so far*, because a duration is
        what a finished print has."""
        job = PrintJob(
            id=A_JOB_ID,
            name="printing.3mf",
            state=PrintJobState.RUNNING,
            started_at=EPOCH,
        )

        assert job.measured_duration is None


class TestImmutability:
    def test_a_job_cannot_be_modified(self) -> None:
        job = a_cancelled_job()
        with pytest.raises(dataclasses.FrozenInstanceError):
            job.name = "rewritten"  # type: ignore[misc]


class TestLifecycle:
    @pytest.mark.parametrize(
        ("state", "terminal"),
        [
            (PrintJobState.RUNNING, False),
            (PrintJobState.FINISHED, True),
            (PrintJobState.CANCELLED, True),
            (PrintJobState.FAILED, True),
        ],
    )
    def test_only_a_running_job_is_not_terminal(self, state: PrintJobState, terminal: bool) -> None:
        assert state.is_terminal is terminal
