"""PrintEvent invariants.

The gateway builds these at the boundary, so what the constructors refuse is what the
adapter can never smuggle inward: a blank name, a job "ending" while still running, and
the physically impossible figures `PrintJob` refuses for the same reasons.
"""

from __future__ import annotations

import pytest

from custom_components.filament_ledger.domain.error import InvalidValueError
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.print_event import PrintEnded, PrintStarted
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState

from .conftest import A_PRINTER, a_tray


class TestPrintStarted:
    def test_a_blank_name_cannot_exist(self) -> None:
        with pytest.raises(InvalidValueError):
            PrintStarted(name="   ", printer=A_PRINTER)

    def test_a_negative_plan_entry_cannot_exist(self) -> None:
        with pytest.raises(InvalidValueError):
            PrintStarted(name="bracket.3mf", printer=A_PRINTER, plan={a_tray(1): Grams.of(-5)})

    def test_a_missing_plan_is_not_an_empty_one(self) -> None:
        """`None` is the Q4-open path — the breakdown never materialised. `{}` is the
        printer naming no trays. Both must survive as themselves."""
        assert PrintStarted(name="bracket.3mf", printer=A_PRINTER, plan=None).plan is None
        assert PrintStarted(name="bracket.3mf", printer=A_PRINTER, plan={}).plan == {}


class TestPrintEnded:
    def test_a_print_cannot_end_running(self) -> None:
        with pytest.raises(InvalidValueError):
            PrintEnded(outcome=PrintJobState.RUNNING, name="bracket.3mf", printer=A_PRINTER)

    @pytest.mark.parametrize(
        "outcome", [PrintJobState.FINISHED, PrintJobState.CANCELLED, PrintJobState.FAILED]
    )
    def test_every_terminal_outcome_is_expressible(self, outcome: PrintJobState) -> None:
        assert PrintEnded(outcome=outcome, name="bracket.3mf", printer=A_PRINTER).outcome is outcome

    def test_a_blank_name_cannot_exist(self) -> None:
        with pytest.raises(InvalidValueError):
            PrintEnded(outcome=PrintJobState.CANCELLED, name="", printer=A_PRINTER)

    def test_a_negative_layer_count_cannot_exist(self) -> None:
        with pytest.raises(InvalidValueError):
            PrintEnded(
                outcome=PrintJobState.CANCELLED,
                name="bracket.3mf",
                printer=A_PRINTER,
                layer_reached=-1,
            )

    def test_a_plan_of_zero_layers_cannot_exist(self) -> None:
        """Same bound as `PrintJob`: `total_layers` divides an estimate, and a zero here
        would be a crash deferred into the estimator, days later, during a review."""
        with pytest.raises(InvalidValueError):
            PrintEnded(
                outcome=PrintJobState.CANCELLED,
                name="bracket.3mf",
                printer=A_PRINTER,
                total_layers=0,
            )

    def test_negative_usage_cannot_exist(self) -> None:
        with pytest.raises(InvalidValueError):
            PrintEnded(
                outcome=PrintJobState.FINISHED,
                name="bracket.3mf",
                printer=A_PRINTER,
                reported_usage={a_tray(1): Grams.of(-1)},
            )

    def test_every_figure_defaults_to_the_honest_unknown(self) -> None:
        """A gateway with every sensor unavailable still speaks: outcome and name, and
        `None` for everything it could not read — never a zero."""
        event = PrintEnded(outcome=PrintJobState.FAILED, name="bracket.3mf", printer=A_PRINTER)
        assert event.layer_reached is None
        assert event.total_layers is None
        assert event.progress is None
        assert event.reported_usage is None
        assert event.raw_gcode_state is None
        assert event.raw_print_error is None
