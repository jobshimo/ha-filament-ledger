"""Anomalies are reported, not prevented.

Refusing to record an impossible-looking balance would force the system to display a number
it knows is false. Recording it and raising a flag tells the user exactly what to do.
"""

from __future__ import annotations

from decimal import Decimal

from custom_components.filament_ledger.domain.service.anomaly_detector import (
    AnomalyDetector,
    AnomalyKind,
)
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import SlotIndex
from custom_components.filament_ledger.domain.value.location import AmsSlot, Storage

from .conftest import A_SPOOL_ID

detector = AnomalyDetector()
OPENING = Grams.of(1000)


def kinds(*, balance_g: float, printing: bool = False, mounted: bool = True) -> set[AnomalyKind]:
    found = detector.inspect(
        spool_id=A_SPOOL_ID,
        balance=Grams.of(balance_g),
        location=AmsSlot(SlotIndex(1)) if mounted else Storage(),
        is_printing=printing,
    )
    return {anomaly.kind for anomaly in found}


class TestNegativeBalance:
    def test_a_negative_balance_is_flagged(self) -> None:
        assert AnomalyKind.NEGATIVE_BALANCE in kinds(balance_g=-40)

    def test_a_healthy_balance_is_not(self) -> None:
        assert kinds(balance_g=612) == set()

    def test_the_detail_tells_the_user_what_to_do(self) -> None:
        found = detector.inspect(spool_id=A_SPOOL_ID, balance=Grams.of(-40), location=Storage())
        assert "Weigh this spool" in found[0].detail


class TestDepletedWhileLoaded:
    def test_empty_and_printing_is_flagged(self) -> None:
        assert AnomalyKind.DEPLETED_WHILE_LOADED in kinds(balance_g=0, printing=True)

    def test_empty_and_idle_is_not(self) -> None:
        assert kinds(balance_g=0, printing=False) == set()

    def test_empty_in_storage_is_not(self) -> None:
        assert kinds(balance_g=0, printing=True, mounted=False) == set()


class TestReconciliationDelta:
    def test_a_large_delta_is_flagged(self) -> None:
        anomaly = detector.inspect_reconciliation(
            spool_id=A_SPOOL_ID, delta=Grams.of(-200), opening_weight=OPENING
        )
        assert anomaly is not None
        assert anomaly.kind is AnomalyKind.LARGE_RECONCILIATION_DELTA

    def test_a_small_delta_is_the_normal_case(self) -> None:
        assert (
            detector.inspect_reconciliation(
                spool_id=A_SPOOL_ID, delta=Grams.of(-14), opening_weight=OPENING
            )
            is None
        )

    def test_the_threshold_is_symmetric(self) -> None:
        """An understated opening weight is as much a signal as an overstated one."""
        for delta in (Grams.of(200), Grams.of(-200)):
            assert (
                detector.inspect_reconciliation(
                    spool_id=A_SPOOL_ID, delta=delta, opening_weight=OPENING
                )
                is not None
            )

    def test_exactly_at_the_threshold_flags(self) -> None:
        """Inclusive on purpose. An anomaly is a prompt to look, not an accusation, so the
        boundary errs toward telling the user."""
        assert (
            detector.inspect_reconciliation(
                spool_id=A_SPOOL_ID, delta=Grams.of(-150), opening_weight=OPENING
            )
            is not None
        )

    def test_one_milligram_below_the_threshold_does_not(self) -> None:
        assert (
            detector.inspect_reconciliation(
                spool_id=A_SPOOL_ID, delta=Grams.of("-149.999"), opening_weight=OPENING
            )
            is None
        )

    def test_the_threshold_is_configurable(self) -> None:
        """The default is an informed guess, meant to be tuned rather than defended."""
        strict = AnomalyDetector(reconciliation_delta_ratio=Decimal("0.01"))
        assert (
            strict.inspect_reconciliation(
                spool_id=A_SPOOL_ID, delta=Grams.of(-14), opening_weight=OPENING
            )
            is not None
        )


class TestTheTwoBalanceFlagsAreMutuallyExclusive:
    """`NEGATIVE_BALANCE` fires below zero and `DEPLETED_WHILE_LOADED` fires *at* zero.

    No balance is both, so a spool never raises the pair. Worth pinning down: the two read
    as neighbouring severities, and a future edit that widened the depleted check to
    `<= 0` would silently start reporting the same spool twice.
    """

    def test_a_negative_balance_reports_as_negative_not_as_depleted(self) -> None:
        found = detector.inspect(
            spool_id=A_SPOOL_ID,
            balance=Grams.of(-40),
            location=AmsSlot(SlotIndex(1)),
            is_printing=True,
        )
        assert [anomaly.kind for anomaly in found] == [AnomalyKind.NEGATIVE_BALANCE]

    def test_exactly_zero_while_printing_reports_as_depleted(self) -> None:
        found = detector.inspect(
            spool_id=A_SPOOL_ID,
            balance=Grams.zero(),
            location=AmsSlot(SlotIndex(1)),
            is_printing=True,
        )
        assert [anomaly.kind for anomaly in found] == [AnomalyKind.DEPLETED_WHILE_LOADED]
