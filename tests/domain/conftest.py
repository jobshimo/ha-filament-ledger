"""Builders for domain tests.

No mocks anywhere in this directory. Entities and value objects are constructed directly —
if a domain test needs a mock, the domain has a dependency it should not have, and the test
failing to be simple is the signal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.filament_ledger.domain.model.movement import Movement, record
from custom_components.filament_ledger.domain.model.spool import Spool
from custom_components.filament_ledger.domain.model.spool import register as register_spool
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import SpoolId
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.movement_type import (
    MovementSource,
    MovementType,
)

EPOCH = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
A_SPOOL_ID = SpoolId("spool-under-test")


def at(days: int = 0, minutes: int = 0) -> datetime:
    return EPOCH + timedelta(days=days, minutes=minutes)


def a_spool(
    *,
    opening_weight: Grams | None = None,
    core_weight: Grams | None = None,
    **overrides: object,
) -> Spool:
    spool = register_spool(
        material=Material.of(MaterialKind.PLA),
        colour=Colour.parse("000000"),
        opening_weight=opening_weight if opening_weight is not None else Grams.of(1000),
        core_weight=core_weight if core_weight is not None else Grams.of(250),
        registered_at=EPOCH,
        vendor="Bambu Lab",
    )
    if overrides:
        msg = f"unexpected overrides: {sorted(overrides)}"
        raise TypeError(msg)
    return spool


def a_movement(
    kind: MovementType,
    grams: float,
    *,
    source: MovementSource = MovementSource.AUTOMATIC,
    spool_id: SpoolId = A_SPOOL_ID,
    occurred_at: datetime | None = None,
) -> Movement:
    return record(
        spool_id=spool_id,
        type=kind,
        amount=Grams.of(grams),
        source=source,
        occurred_at=occurred_at if occurred_at is not None else EPOCH,
    )


def opening(grams: float = 1000) -> Movement:
    return a_movement(MovementType.OPENING_BALANCE, grams)


def printed(grams: float) -> Movement:
    return a_movement(MovementType.PRINT_CONSUMPTION, -abs(grams))


def estimated(grams: float) -> Movement:
    return a_movement(
        MovementType.ESTIMATED_CONSUMPTION,
        -abs(grams),
        source=MovementSource.USER_CONFIRMED,
    )


def reconciled(grams: float) -> Movement:
    return a_movement(
        MovementType.RECONCILIATION,
        grams,
        source=MovementSource.USER_CONFIRMED,
    )


def discarded(grams: float) -> Movement:
    return a_movement(
        MovementType.DISCARD,
        -abs(grams),
        source=MovementSource.USER_CONFIRMED,
    )
