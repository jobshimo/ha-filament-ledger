"""`Grams` is the type that makes a class of bug impossible. These tests are why."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from custom_components.filament_ledger.domain.value.grams import Grams, total


class TestConstruction:
    def test_converts_grams_to_milligrams(self) -> None:
        assert Grams.of(24.5).milligrams == 24_500

    def test_float_conversion_is_exact(self) -> None:
        """Naive float arithmetic gives 24500.000000000004 here. Decimal does not."""
        assert Grams.of(24.5).milligrams == 24_500
        assert Grams.of(0.1).milligrams == 100
        assert Grams.of(1000.3).milligrams == 1_000_300

    def test_accepts_strings_and_decimals(self) -> None:
        assert Grams.of("24.5") == Grams.of(Decimal("24.5")) == Grams.of(24.5)

    def test_rounds_half_up_to_the_nearest_milligram(self) -> None:
        assert Grams.of("0.0005").milligrams == 1
        assert Grams.of("0.0004").milligrams == 0

    def test_zero(self) -> None:
        assert Grams.zero().milligrams == 0
        assert Grams.zero().is_zero

    def test_negatives_are_allowed(self) -> None:
        """A reconciliation may increase a balance. Rejecting negatives would make the
        ledger lie about an understated opening weight."""
        assert Grams.of(-5).milligrams == -5000
        assert Grams.of(-5).is_negative

    def test_bool_is_rejected_by_of(self) -> None:
        with pytest.raises(TypeError):
            Grams.of(True)

    def test_bool_is_rejected_by_the_constructor(self) -> None:
        """`bool` is a subclass of `int`, so this is a real hole worth closing."""
        with pytest.raises(TypeError):
            Grams(True)


class TestArithmetic:
    def test_addition(self) -> None:
        assert Grams.of(1000) + Grams.of(-84.1) == Grams.of(915.9)

    def test_subtraction(self) -> None:
        assert Grams.of(1000) - Grams.of(84.1) == Grams.of(915.9)

    def test_negation_and_abs(self) -> None:
        assert -Grams.of(40) == Grams.of(-40)
        assert abs(Grams.of(-40)) == Grams.of(40)

    def test_ordering(self) -> None:
        assert Grams.of(1) < Grams.of(2)
        assert max(Grams.of(1), Grams.of(2)) == Grams.of(2)

    @pytest.mark.parametrize("bare", [5, 5.0, "5", Decimal(5)])
    def test_cannot_be_added_to_a_bare_number(self, bare: object) -> None:
        """The entire point of the type.

        `mypy --strict` rejects this before it runs; this proves the runtime does too, for
        the callers a type checker never sees.
        """
        with pytest.raises(TypeError):
            Grams.of(10) + bare  # type: ignore[operator]

    def test_cannot_be_subtracted_from_a_bare_number(self) -> None:
        with pytest.raises(TypeError):
            Grams.of(10) - 5  # type: ignore[operator]

    def test_scaling_by_a_ratio(self) -> None:
        assert Grams.of(100).scaled_by(Decimal("0.5")) == Grams.of(50)
        assert Grams.of(34.5).scaled_by(Decimal("0.823")) == Grams.of("28.3935")

    def test_ratio_to(self) -> None:
        assert Grams.of(612).ratio_to(Grams.of(1000)) == Decimal("0.612")

    def test_ratio_to_zero_is_zero_not_a_crash(self) -> None:
        assert Grams.of(5).ratio_to(Grams.zero()) == Decimal(0)


class TestAccumulationIsExact:
    def test_a_thousand_additions_do_not_drift(self) -> None:
        """The reason milligrams are integers.

        Summing 0.1 a thousand times in float arithmetic gives 99.9999999999986.
        """
        running = Grams.zero()
        for _ in range(1000):
            running = running + Grams.of(0.1)
        assert running == Grams.of(100)

    @given(st.lists(st.integers(min_value=-10_000_000, max_value=10_000_000), max_size=200))
    def test_total_equals_repeated_addition(self, milligrams: list[int]) -> None:
        quantities = [Grams(value) for value in milligrams]
        by_fold = Grams.zero()
        for quantity in quantities:
            by_fold = by_fold + quantity
        assert total(quantities) == by_fold


class TestDisplay:
    def test_string_carries_one_decimal(self) -> None:
        assert str(Grams.of(611.7)) == "611.7 g"
        assert str(Grams.of(1000)) == "1000.0 g"

    def test_as_decimal_is_exact(self) -> None:
        assert Grams.of(24.5).as_decimal == Decimal("24.5")
