"""`SpoolState` is derived, and the derivation must be total.

A state machine that only permits is not a state machine; and a spool the rules do not
cover is a spool whose displayed state depends on which branch an implementer wrote first.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.spool_state import SpoolState

from .conftest import EPOCH


def derive(
    *,
    discarded_at: datetime | None = None,
    balance_g: float = 1000,
    movement_count: int = 1,
) -> SpoolState:
    return SpoolState.derive(
        discarded_at=discarded_at,
        balance=Grams.of(balance_g),
        movement_count=movement_count,
    )


class TestDerivation:
    def test_only_an_opening_balance_is_sealed(self) -> None:
        assert derive(movement_count=1) is SpoolState.SEALED

    def test_a_drawn_spool_is_active(self) -> None:
        assert derive(balance_g=612, movement_count=4) is SpoolState.ACTIVE

    def test_a_spool_corrected_upward_but_never_drawn_is_active(self) -> None:
        """`ACTIVE` means "something has happened to this spool", not "filament has left it".

        A spool whose second movement is a positive reconciliation has been opened, weighed
        and corrected. Calling it SEALED would be a lie about a spool already on a scale.
        """
        assert derive(balance_g=1006, movement_count=2) is SpoolState.ACTIVE

    def test_zero_balance_is_depleted(self) -> None:
        assert derive(balance_g=0, movement_count=5) is SpoolState.DEPLETED

    def test_negative_balance_is_depleted(self) -> None:
        assert derive(balance_g=-40, movement_count=5) is SpoolState.DEPLETED

    def test_discarded_wins_over_everything(self) -> None:
        assert derive(discarded_at=EPOCH, balance_g=612, movement_count=4) is SpoolState.DISCARDED
        assert derive(discarded_at=EPOCH, balance_g=0, movement_count=9) is SpoolState.DISCARDED
        assert derive(discarded_at=EPOCH, movement_count=1) is SpoolState.DISCARDED


class TestReversibility:
    def test_depleted_returns_to_active_when_a_reconciliation_finds_filament(self) -> None:
        """DEPLETED is reversible for free, because it was never stored."""
        assert derive(balance_g=0, movement_count=5) is SpoolState.DEPLETED
        assert derive(balance_g=18, movement_count=6) is SpoolState.ACTIVE

    def test_discarded_is_terminal(self) -> None:
        assert SpoolState.DISCARDED.is_terminal
        assert not SpoolState.ACTIVE.is_terminal
        assert not SpoolState.DEPLETED.is_terminal


class TestTotality:
    """`state in set(SpoolState)` would prove nothing — a `StrEnum` can only ever return one
    of its own members, so that assertion holds for any function that returns at all.

    What is worth proving is that the ladder is **exhaustive over its own predicates** and
    that **no branch is dead**.
    """

    @pytest.mark.parametrize("discarded", [True, False])
    @pytest.mark.parametrize("milligrams", [-40_000, -1, 0, 1, 612_000])
    @pytest.mark.parametrize("movement_count", [0, 1, 2, 40])
    def test_the_ladder_matches_an_independently_written_truth_table(
        self, *, discarded: bool, milligrams: int, movement_count: int
    ) -> None:
        """Every combination of the three predicates, checked against the rule restated as
        a table rather than as the same if/elif chain."""
        expected = {
            (True, None, None): SpoolState.DISCARDED,
            (False, "empty", None): SpoolState.DEPLETED,
            (False, "positive", "first"): SpoolState.SEALED,
            (False, "positive", "used"): SpoolState.ACTIVE,
        }
        if discarded:
            key: tuple[bool, str | None, str | None] = (True, None, None)
        elif milligrams <= 0:
            key = (False, "empty", None)
        else:
            key = (False, "positive", "first" if movement_count <= 1 else "used")

        assert (
            SpoolState.derive(
                discarded_at=EPOCH if discarded else None,
                balance=Grams(milligrams),
                movement_count=movement_count,
            )
            is expected[key]
        )

    @given(
        discarded=st.booleans(),
        milligrams=st.integers(min_value=-5_000_000, max_value=5_000_000),
        movement_count=st.integers(min_value=0, max_value=500),
    )
    def test_no_input_escapes_the_ladder(
        self, *, discarded: bool, milligrams: int, movement_count: int
    ) -> None:
        """The ladder ends in an unconditional `return`, so the only way out is a raise."""
        SpoolState.derive(
            discarded_at=EPOCH if discarded else None,
            balance=Grams(milligrams),
            movement_count=movement_count,
        )

    def test_every_state_is_reachable(self) -> None:
        """A branch nothing can reach is a branch that will rot unnoticed."""
        reached = {
            derive(discarded_at=EPOCH),
            derive(balance_g=0, movement_count=5),
            derive(movement_count=1),
            derive(balance_g=612, movement_count=4),
        }
        assert reached == set(SpoolState)


class TestStock:
    @pytest.mark.parametrize(
        ("state", "counts"),
        [
            (SpoolState.SEALED, True),
            (SpoolState.ACTIVE, True),
            (SpoolState.DEPLETED, True),
            (SpoolState.DISCARDED, False),
        ],
    )
    def test_only_discarded_leaves_the_stock_figure(
        self, state: SpoolState, *, counts: bool
    ) -> None:
        """A depleted spool is still a real object until it is thrown away."""
        assert state.counts_as_stock is counts
