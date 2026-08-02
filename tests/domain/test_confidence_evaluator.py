"""Confidence must be a total function with no unreachable branch.

The specification once had both: a band no spool fell into, and a `LOW` clause that could
never fire. Two implementers reading it produced different dots for the same spool.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from custom_components.filament_ledger.domain.model.movement import Movement
from custom_components.filament_ledger.domain.service.confidence_evaluator import (
    ConfidenceEvaluator,
    movements_since_anchor,
)
from custom_components.filament_ledger.domain.value.confidence import Confidence
from custom_components.filament_ledger.domain.value.grams import Grams

from .conftest import estimated, opening, printed, reconciled

OPENING_WEIGHT = Grams.of(1000)
evaluator = ConfidenceEvaluator()


def confidence_of(movements: list[Movement]) -> Confidence:
    return evaluator.evaluate(movements=movements, opening_weight=OPENING_WEIGHT)


class TestHigh:
    def test_a_freshly_registered_spool_is_high(self) -> None:
        """The band the specification originally left undefined.

        The opening balance counts as an anchor: it is a human-confirmed number. Treating it
        as one is what stops a spool being displayed as untrustworthy on the day it is
        registered.
        """
        assert confidence_of([opening(1000)]) is Confidence.HIGH

    def test_light_measured_use_stays_high(self) -> None:
        assert confidence_of([opening(1000), printed(84.1)]) is Confidence.HIGH

    def test_a_reconciliation_restores_high(self) -> None:
        history = [opening(1000), estimated(50), printed(400), reconciled(-12)]
        assert confidence_of(history) is Confidence.HIGH


class TestMedium:
    def test_accumulated_consumption_crosses_to_medium_at_twenty_percent(self) -> None:
        assert confidence_of([opening(1000), printed(200)]) is Confidence.MEDIUM

    def test_just_below_the_threshold_is_still_high(self) -> None:
        assert confidence_of([opening(1000), printed(199.999)]) is Confidence.HIGH

    def test_consumption_before_the_anchor_does_not_count(self) -> None:
        """The window starts at the most recent reconciliation, not at registration."""
        history = [opening(1000), printed(700), reconciled(-5), printed(10)]
        assert confidence_of(history) is Confidence.HIGH


class TestLow:
    def test_one_approved_estimate_is_enough(self) -> None:
        """Intended aggressiveness. LOW means "weigh this when you get a chance"."""
        assert confidence_of([opening(1000), estimated(1)]) is Confidence.LOW

    def test_an_estimate_beats_heavy_measured_use(self) -> None:
        assert confidence_of([opening(1000), printed(500), estimated(2)]) is Confidence.LOW

    def test_an_estimate_before_the_anchor_is_forgiven(self) -> None:
        history = [opening(1000), estimated(90), reconciled(-30)]
        assert confidence_of(history) is Confidence.HIGH


class TestAnchor:
    def test_the_opening_balance_is_an_anchor_and_is_itself_excluded(self) -> None:
        """`since` is everything *after* the anchor, in both cases.

        Inert today — an opening balance is positive, so `consumed()` skips it, and it is
        never an estimate. It stops being inert the moment a rule starts counting entries
        rather than grams.
        """
        first, tail = opening(1000), printed(50)
        assert list(movements_since_anchor([first, tail])) == [tail]

    def test_a_history_with_no_anchor_at_all_is_taken_whole(self) -> None:
        """An infrastructure query by date can hand back a partial history. Everything in
        it is unaccounted for, which is the conservative reading."""
        history = [printed(10), printed(20)]
        assert list(movements_since_anchor(history)) == history

    def test_the_window_starts_after_the_most_recent_reconciliation(self) -> None:
        first, second = reconciled(5), reconciled(-3)
        tail = printed(20)
        history = [opening(1000), first, printed(10), second, tail]
        assert list(movements_since_anchor(history)) == [tail]

    def test_a_trailing_reconciliation_leaves_an_empty_window(self) -> None:
        assert list(movements_since_anchor([opening(1000), reconciled(5)])) == []


class TestTotality:
    """`confidence in set(Confidence)` would prove nothing — a `StrEnum` can only return one
    of its own members. What is worth proving is that no history escapes the ladder, that
    every level is reachable, and that the answer never depends on anything but the rule.
    """

    @given(
        st.lists(
            st.sampled_from(["print", "estimate", "reconcile"]),
            min_size=1,
            max_size=30,
        )
    )
    def test_no_history_escapes_the_ladder(self, kinds: list[str]) -> None:
        builders = {"print": printed, "estimate": estimated, "reconcile": reconciled}
        history = [opening(1000), *(builders[kind](7) for kind in kinds)]
        confidence_of(history)

    @given(
        st.lists(
            st.sampled_from(["print", "estimate", "reconcile"]),
            min_size=1,
            max_size=20,
        )
    )
    def test_the_same_history_always_gives_the_same_answer(self, kinds: list[str]) -> None:
        """Nothing in the rule may depend on the clock, on insertion order into a set, or on
        anything else the caller cannot see."""
        builders = {"print": printed, "estimate": estimated, "reconcile": reconciled}
        history = [opening(1000), *(builders[kind](7) for kind in kinds)]
        assert confidence_of(history) is confidence_of(history)

    def test_every_level_is_reachable(self) -> None:
        """A branch nothing can reach is a branch that will rot unnoticed."""
        reached = {
            confidence_of([opening(1000), estimated(1)]),
            confidence_of([opening(1000), printed(200)]),
            confidence_of([opening(1000)]),
        }
        assert reached == set(Confidence)

    def test_an_empty_history_still_yields_a_level(self) -> None:
        assert confidence_of([]) is Confidence.HIGH


class TestPrompting:
    def test_only_low_asks_to_be_weighed(self) -> None:
        assert Confidence.LOW.needs_weighing
        assert not Confidence.MEDIUM.needs_weighing
        assert not Confidence.HIGH.needs_weighing
