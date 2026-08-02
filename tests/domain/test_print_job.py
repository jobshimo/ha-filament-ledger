"""PrintJob invariants.

The entity is a record of the printer's claims, held verbatim — so the validation is
about physical impossibility, never about second-guessing the report.
"""

from __future__ import annotations

import dataclasses

import pytest

from custom_components.filament_ledger.domain.error import InvalidValueError
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import SlotIndex
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState

from .conftest import a_cancelled_job, at


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
            a_cancelled_job(reported_usage={SlotIndex(1): Grams.of(-5)})

    def test_a_zero_usage_tray_is_a_legitimate_report(self) -> None:
        """The printer reports 0 g for a tray the job loaded but barely touched. Refusing
        it would drop the tray from the record entirely, which is a different claim."""
        job = a_cancelled_job(reported_usage={SlotIndex(1): Grams.zero()})
        assert job.reported_usage == {SlotIndex(1): Grams.zero()}

    def test_a_missing_usage_report_is_not_an_empty_one(self) -> None:
        """`None` means the figure never materialised; `{}` would mean the printer named
        no trays. A missing figure is not a figure of zero (docs/04-use-cases.md UC-04)."""
        assert a_cancelled_job(reported_usage=None).reported_usage is None


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
