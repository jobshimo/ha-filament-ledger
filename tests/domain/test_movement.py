"""Movement invariants.

A movement that cannot be constructed wrongly is a ledger entry nobody has to audit.
"""

from __future__ import annotations

import dataclasses

import pytest

from custom_components.filament_ledger.domain.error import InvalidValueError
from custom_components.filament_ledger.domain.model.movement import record
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.movement_type import (
    Direction,
    MovementSource,
    MovementType,
)

from .conftest import A_SPOOL_ID, EPOCH, a_movement, at


def build(kind: MovementType, grams: float) -> object:
    return record(
        spool_id=A_SPOOL_ID,
        type=kind,
        amount=Grams.of(grams),
        source=MovementSource.USER_CONFIRMED,
        occurred_at=EPOCH,
    )


class TestAmount:
    @pytest.mark.parametrize("kind", list(MovementType))
    def test_a_zero_movement_is_refused(self, kind: MovementType) -> None:
        """A zero movement records nothing and only adds noise."""
        with pytest.raises(InvalidValueError):
            build(kind, 0)

    @pytest.mark.parametrize(
        "kind",
        [
            MovementType.PRINT_CONSUMPTION,
            MovementType.PURGE_WASTE,
            MovementType.ESTIMATED_CONSUMPTION,
            MovementType.DISCARD,
        ],
    )
    def test_consuming_types_must_be_negative(self, kind: MovementType) -> None:
        with pytest.raises(InvalidValueError):
            build(kind, 10)
        assert build(kind, -10) is not None

    def test_opening_balance_must_be_positive(self) -> None:
        """A spool cannot be born owing filament."""
        with pytest.raises(InvalidValueError):
            build(MovementType.OPENING_BALANCE, -1000)

    @pytest.mark.parametrize("kind", [MovementType.MANUAL_ADJUSTMENT, MovementType.RECONCILIATION])
    def test_bidirectional_types_accept_either_sign(self, kind: MovementType) -> None:
        assert build(kind, 10) is not None
        assert build(kind, -10) is not None


class TestDirections:
    def test_exactly_the_corrections_and_the_scale_are_bidirectional(self) -> None:
        """The specification once listed OPENING_BALANCE as bidirectional, contradicting
        both its own sign table and the `opening_weight > 0` invariant.

        The set grew by three in v1.0, and each one earns it (docs/14 §14.7): a
        `VOID_REVERSAL` negates whatever it undoes, a `REINSTATEMENT` repeats it, and a
        `REASSIGNMENT` is one type for both legs of a compensating pair. Nothing that
        describes a physical direction of travel joined them.
        """
        either = {kind for kind in MovementType if kind.direction is Direction.EITHER}
        assert either == {
            MovementType.MANUAL_ADJUSTMENT,
            MovementType.RECONCILIATION,
            MovementType.VOID_REVERSAL,
            MovementType.REINSTATEMENT,
            MovementType.REASSIGNMENT,
        }

    def test_every_type_declares_a_direction(self) -> None:
        for kind in MovementType:
            assert kind.direction in set(Direction)


class TestApproval:
    def test_only_completed_prints_and_registration_enter_unattended(self) -> None:
        unattended = {kind for kind in MovementType if not kind.requires_approval}
        assert unattended == {
            MovementType.OPENING_BALANCE,
            MovementType.PRINT_CONSUMPTION,
        }


class TestImmutability:
    def test_a_movement_cannot_be_modified(self) -> None:
        movement = a_movement(MovementType.PRINT_CONSUMPTION, -84.1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            movement.amount = Grams.of(-1)  # type: ignore[misc]


class TestTimestamps:
    def test_recorded_at_defaults_to_occurred_at(self) -> None:
        movement = record(
            spool_id=A_SPOOL_ID,
            type=MovementType.PRINT_CONSUMPTION,
            amount=Grams.of(-10),
            source=MovementSource.AUTOMATIC,
            occurred_at=EPOCH,
        )
        assert movement.recorded_at == EPOCH

    def test_they_can_differ_when_a_print_finished_while_ha_was_down(self) -> None:
        movement = record(
            spool_id=A_SPOOL_ID,
            type=MovementType.PRINT_CONSUMPTION,
            amount=Grams.of(-10),
            source=MovementSource.AUTOMATIC,
            occurred_at=EPOCH,
            recorded_at=at(minutes=90),
        )
        assert movement.occurred_at < movement.recorded_at


class TestClassification:
    def test_an_estimate_is_flagged_as_one(self) -> None:
        assert a_movement(MovementType.ESTIMATED_CONSUMPTION, -28.4).is_estimate
        assert not a_movement(MovementType.PRINT_CONSUMPTION, -28.4).is_estimate

    def test_a_reconciliation_is_flagged_as_one(self) -> None:
        assert a_movement(MovementType.RECONCILIATION, 6.2).is_reconciliation

    def test_identities_are_unique(self) -> None:
        first = a_movement(MovementType.PRINT_CONSUMPTION, -1)
        second = a_movement(MovementType.PRINT_CONSUMPTION, -1)
        assert first.id != second.id
