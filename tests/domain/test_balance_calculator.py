"""The central rule of the system.

If one test file in this repository has to be right, it is this one.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from custom_components.filament_ledger.domain.model.movement import record
from custom_components.filament_ledger.domain.service.balance_calculator import (
    balance,
    consumed,
    running_balances,
)
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.movement_type import (
    MovementSource,
    MovementType,
)

from .conftest import (
    A_SPOOL_ID,
    EPOCH,
    discarded,
    estimated,
    opening,
    printed,
    reconciled,
)


class TestBalance:
    def test_empty_history_is_zero(self) -> None:
        assert balance([]) == Grams.zero()

    def test_a_new_spool_holds_its_opening_weight(self) -> None:
        assert balance([opening(1000)]) == Grams.of(1000)

    def test_a_print_reduces_the_balance(self) -> None:
        """The regression this file exists for.

        The specification once said `balance = opening_weight - sum(movements)`. Amounts are
        already signed, so that formula made every print make the spool *heavier* and
        counted the opening weight twice. 1000 - (-84.1) + 1000 would have been 2084.1 g.
        """
        assert balance([opening(1000), printed(84.1)]) == Grams.of(915.9)

    def test_the_worked_example_from_the_ui_specification(self) -> None:
        """docs/06-ui-spec.md §6.5 shows this exact history arriving at 612 g."""
        history = [
            opening(1000),
            printed(162),
            discarded(8),
            printed(112),
            reconciled(6.2),
            printed(84.1),
            estimated(28.4),
        ]
        assert balance(history) == Grams.of(611.7)

    def test_a_reconciliation_can_raise_the_balance(self) -> None:
        assert balance([opening(1000), printed(100), reconciled(6.2)]) == Grams.of(906.2)

    def test_a_negative_balance_is_reported_not_rejected(self) -> None:
        """If the ledger says -40 g, the opening weight was wrong or a movement was missed.
        Refusing to record it would force the system to display a number it knows is false."""
        assert balance([opening(100), printed(140)]) == Grams.of(-40)

    @given(
        st.lists(
            st.integers(min_value=-500_000, max_value=500_000).filter(lambda mg: mg != 0),
            max_size=300,
        )
    )
    def test_balance_is_the_sum_of_its_movements(self, milligrams: list[int]) -> None:
        """The property that matters most.

        No separate opening term: the opening balance is the first movement.
        """
        movements = [
            record(
                spool_id=A_SPOOL_ID,
                type=MovementType.MANUAL_ADJUSTMENT,
                amount=Grams(value),
                source=MovementSource.USER_CONFIRMED,
                occurred_at=EPOCH,
            )
            for value in milligrams
        ]
        assert balance(movements) == Grams(sum(milligrams))

    @given(st.lists(st.integers(min_value=1, max_value=100_000), max_size=50))
    def test_order_does_not_change_the_balance(self, milligrams: list[int]) -> None:
        movements = [
            record(
                spool_id=A_SPOOL_ID,
                type=MovementType.MANUAL_ADJUSTMENT,
                amount=Grams(value),
                source=MovementSource.USER_CONFIRMED,
                occurred_at=EPOCH,
            )
            for value in milligrams
        ]
        assert balance(movements) == balance(list(reversed(movements)))


class TestRunningBalances:
    def test_each_line_carries_the_balance_after_it(self) -> None:
        lines = running_balances([opening(1000), printed(162), discarded(8)])
        assert [line.balance_after for line in lines] == [
            Grams.of(1000),
            Grams.of(838),
            Grams.of(830),
        ]

    def test_the_last_line_equals_the_balance(self) -> None:
        history = [opening(1000), printed(162), discarded(8), printed(112)]
        assert running_balances(history)[-1].balance_after == balance(history)

    def test_empty_history_produces_no_lines(self) -> None:
        assert running_balances([]) == []


class TestConsumed:
    def test_counts_only_what_left_the_spool(self) -> None:
        assert consumed([opening(1000), printed(84.1), estimated(28.4)]) == Grams.of(112.5)

    def test_an_increase_is_not_netted_off(self) -> None:
        """A reconciliation that adds 6 g does not mean 6 g fewer were printed."""
        assert consumed([printed(100), reconciled(6.2)]) == Grams.of(100)

    def test_nothing_consumed_is_zero(self) -> None:
        assert consumed([opening(1000)]) == Grams.zero()
