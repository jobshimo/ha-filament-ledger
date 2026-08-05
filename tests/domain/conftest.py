"""Builders for domain tests.

No mocks anywhere in this directory. Entities and value objects are constructed directly —
if a domain test needs a mock, the domain has a dependency it should not have, and the test
failing to be simple is the signal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.filament_ledger.domain.model.movement import Movement, record
from custom_components.filament_ledger.domain.model.pending_review import (
    PendingReview,
    ReviewCharge,
    ReviewLine,
    open_review,
)
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.model.spool import Spool
from custom_components.filament_ledger.domain.model.spool import register as register_spool
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    AmsIndex,
    PrinterSerial,
    PrintJobId,
    SlotIndex,
    SpoolId,
    TrayRef,
)
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.movement_type import (
    MovementSource,
    MovementType,
)
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.domain.value.review import EstimatorKind, ReviewReason

EPOCH = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
A_SPOOL_ID = SpoolId("spool-under-test")
A_JOB_ID = PrintJobId("job-under-test")

# The machine these tests talk to, anonymised the same way the registry fixture is. One
# printer, because the ledger still follows one — the second serial exists only where a
# test is about two of them being different.
A_PRINTER = PrinterSerial("00000000TESTSER")
ANOTHER_PRINTER = PrinterSerial("00000000OTHERSR")


def a_tray(slot: int, *, printer: PrinterSerial = A_PRINTER, ams: int = 1) -> TrayRef:
    """One tray, named in full. The default is the machine under test, AMS 1."""
    return TrayRef(printer=printer, ams=AmsIndex(ams), slot=SlotIndex(slot))


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


def a_cancelled_job(
    *,
    layer_reached: int | None = 71,
    total_layers: int | None = 209,
    reported_usage: dict[TrayRef, Grams] | None = None,
) -> PrintJob:
    """The worked example from docs/06-ui-spec.md §6.3: bracket_v3, stopped at layer 71."""
    return PrintJob(
        id=A_JOB_ID,
        name="bracket_v3.gcode.3mf",
        state=PrintJobState.CANCELLED,
        started_at=EPOCH,
        ended_at=at(minutes=90),
        layer_reached=layer_reached,
        total_layers=total_layers,
        reported_usage=reported_usage,
    )


def a_line(slot: int, estimated: float, spool_id: SpoolId | None = A_SPOOL_ID) -> ReviewLine:
    """One tray as `OpenPendingReview` freezes it: the mounted spool takes the whole
    estimate, and no mounted spool freezes as no charge at all."""
    return ReviewLine(
        tray=a_tray(slot),
        estimated=Grams.of(estimated),
        charges=(
            (ReviewCharge(spool_id=spool_id, amount=Grams.of(estimated)),)
            if spool_id is not None
            else ()
        ),
    )


def a_charge(grams: float, spool_id: SpoolId = A_SPOOL_ID) -> ReviewCharge:
    return ReviewCharge(spool_id=spool_id, amount=Grams.of(grams))


def a_pending_review(*lines: ReviewLine) -> PendingReview:
    return open_review(
        job_id=A_JOB_ID,
        reason=ReviewReason.CANCELLED,
        lines=lines or (a_line(1, 28.4),),
        estimator_used=EstimatorKind.LINEAR_PROGRESS,
        opened_at=EPOCH,
    )
