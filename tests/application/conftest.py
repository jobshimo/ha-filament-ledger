"""A whole ledger, wired against a real SQLite file and nothing else.

Deliberately not a fake database. The point of these tests is to verify the mapping and the
constraints, and a fake verifies neither — the triggers, the partial unique indexes and the
`CHECK` clauses are half the design.

Home Assistant is absent. That is the architecture's central claim, and this fixture is what
makes it observable: the entire product below the adapter layer runs on `run_inline`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from custom_components.filament_ledger.application.adjust_spool import (
    AdjustSpool,
    DiscardFilament,
)
from custom_components.filament_ledger.application.delete_spool import (
    DeleteSpool,
    RestoreSpool,
)
from custom_components.filament_ledger.application.detect_spool import DetectSpool
from custom_components.filament_ledger.application.move_spool import (
    EditSpoolDetails,
    MountSpool,
    UnmountSpool,
)
from custom_components.filament_ledger.application.query import Queries
from custom_components.filament_ledger.application.reassign_movement import ReassignMovement
from custom_components.filament_ledger.application.reconcile_spool import ReconcileSpool
from custom_components.filament_ledger.application.record_print_consumption import (
    RecordPrintConsumption,
)
from custom_components.filament_ledger.application.register_spool import RegisterSpool
from custom_components.filament_ledger.application.review_queue import (
    ApproveReview,
    DismissReview,
    OpenPendingReview,
)
from custom_components.filament_ledger.application.track_print_job import TrackPrintJob
from custom_components.filament_ledger.application.use_cases import UseCases
from custom_components.filament_ledger.application.void_movement import (
    RestoreMovement,
    VoidMovement,
)
from custom_components.filament_ledger.domain.event import DomainEvent
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    AmsIndex,
    PrinterSerial,
    SlotIndex,
    TrayRef,
)
from custom_components.filament_ledger.infrastructure.estimation.linear_progress_estimator import (
    LinearProgressEstimator,
)
from custom_components.filament_ledger.infrastructure.persistence.database import (
    Database,
    Executor,
    run_inline,
)
from custom_components.filament_ledger.infrastructure.persistence.movement_repository import (
    SqliteMovementRepository,
)
from custom_components.filament_ledger.infrastructure.persistence.movement_void_repository import (
    SqliteMovementVoidRepository,
)
from custom_components.filament_ledger.infrastructure.persistence.print_job_repository import (
    SqlitePrintJobRepository,
)
from custom_components.filament_ledger.infrastructure.persistence.review_repository import (
    SqliteReviewRepository,
)
from custom_components.filament_ledger.infrastructure.persistence.spool_repository import (
    SqliteSpoolRepository,
)

EPOCH = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)

# The machine every scenario here talks to, anonymised the way the registry fixture is.
A_PRINTER = PrinterSerial("00000000TESTSER")
ANOTHER_PRINTER = PrinterSerial("00000000OTHERSR")


def a_tray(slot: int, *, printer: PrinterSerial = A_PRINTER, ams: int = 1) -> TrayRef:
    """One tray, named in full — printer, AMS unit, tray.

    The printer and the AMS default because every scenario here is about a ledger following
    one machine, and spelling a serial out at each of two hundred call sites would bury
    what each scenario is actually testing. A scenario about *two* machines passes them.
    """
    return TrayRef(printer=printer, ams=AmsIndex(ams), slot=SlotIndex(slot))


async def run_yielding[T](target: Callable[[], T]) -> T:
    """An executor that yields to the event loop first, the way a real thread-pool hop
    does. `run_inline` never yields, so two gathered use cases run to completion one after
    the other and a race can never be observed with it — this one is what lets the
    concurrency tests interleave the way production would."""
    await asyncio.sleep(0)
    return target()


@dataclass
class FakeClock:
    """Time under the test's control. No sleeping, no patching."""

    moment: datetime = EPOCH

    def now(self) -> datetime:
        return self.moment

    def advance(self, **delta: float) -> None:
        self.moment = self.moment + timedelta(**delta)


@dataclass
class RecordingEventBus:
    """Twenty lines, stores in a list, and every test reads as a scenario."""

    published: list[DomainEvent] = field(default_factory=list)

    async def publish(self, event: DomainEvent) -> None:
        self.published.append(event)

    def of[T: DomainEvent](self, kind: type[T]) -> list[T]:
        """Generic in the event type, so a scenario can read the payload it filtered for.

        The alternative is an `isinstance` assertion after every filter — ceremony that
        says nothing a reader did not already know from the line above it.
        """
        return [event for event in self.published if isinstance(event, kind)]


@dataclass
class Ledger:
    use_cases: UseCases
    clock: FakeClock
    events: RecordingEventBus
    database: Database


async def build_ledger(path: Path, executor: Executor) -> Ledger:
    """Wire a whole ledger by hand, exactly as the composition root does — the `Database`
    doubles as the unit of work, there as here."""
    database = await Database.open(path / "ledger.db", executor)
    await database.migrate()

    spools = SqliteSpoolRepository(database)
    movements = SqliteMovementRepository(database)
    voids = SqliteMovementVoidRepository(database)
    jobs = SqlitePrintJobRepository(database)
    reviews = SqliteReviewRepository(database)
    clock = FakeClock()
    events = RecordingEventBus()

    # The production estimator, not a fake: it is pure arithmetic over the job the
    # test itself constructs, so faking it would only test the fake.
    open_pending_review = OpenPendingReview(
        jobs, reviews, spools, LinearProgressEstimator(), clock, events, database
    )
    record_print_consumption = RecordPrintConsumption(
        jobs, spools, movements, open_pending_review, clock, events, database
    )

    # Built once and shared, exactly as the composition root shares it: the interactive
    # register path and `DetectSpool`'s auto-registration run the same instance.
    register_spool = RegisterSpool(spools, movements, clock, events, database)

    return Ledger(
        use_cases=UseCases(
            register_spool=register_spool,
            reconcile_spool=ReconcileSpool(spools, movements, clock, events, database),
            discard_filament=DiscardFilament(spools, movements, clock, events, database),
            adjust_spool=AdjustSpool(spools, movements, clock, events, database),
            mount_spool=MountSpool(spools, clock, events, database),
            unmount_spool=UnmountSpool(spools, events, database),
            # `auto_mount` carries the production default. `auto_register` deliberately
            # does not: the captured Bambu fixtures these suites replay carry material
            # and colour on every occupied tray, so the production default would register
            # a spool under half the scenarios that exist to observe the *un*-registered
            # branches. The auto-register scenarios build their own `DetectSpool` with
            # the flag on, the way the auto-mount-off ones always have — the fixture
            # stays one honest wiring, not a matrix.
            detect_spool=DetectSpool(
                spools,
                events,
                database,
                clock,
                auto_mount=True,
                register_spool=register_spool,
                default_opening_weight=Grams.of(1000),
                default_core_weight=Grams.of(250),
                auto_register=False,
            ),
            edit_spool_details=EditSpoolDetails(spools, database),
            track_print_job=TrackPrintJob(
                jobs=jobs,
                open_pending_review=open_pending_review,
                record_print_consumption=record_print_consumption,
                clock=clock,
                uow=database,
            ),
            record_print_consumption=record_print_consumption,
            open_pending_review=open_pending_review,
            approve_review=ApproveReview(reviews, spools, movements, jobs, clock, events, database),
            dismiss_review=DismissReview(reviews, jobs, clock, events, database),
            reassign_movement=ReassignMovement(spools, movements, voids, clock, events, database),
            void_movement=VoidMovement(spools, movements, voids, clock, events, database),
            restore_movement=RestoreMovement(spools, movements, voids, clock, events, database),
            delete_spool=DeleteSpool(spools, clock, events, database),
            restore_spool=RestoreSpool(spools, events, database),
            queries=Queries(
                spools=spools,
                movements=movements,
                reviews=reviews,
                jobs=jobs,
                voids=voids,
                # The same clock every use case writes timestamps with, so a period
                # boundary in a test is measured against the ledger the test built.
                clock=clock,
            ),
        ),
        clock=clock,
        events=events,
        database=database,
    )


@pytest.fixture
async def ledger(tmp_path: Path) -> AsyncIterator[Ledger]:
    built = await build_ledger(tmp_path, run_inline)
    yield built
    await built.database.close()


@pytest.fixture
async def interleaved_ledger(tmp_path: Path) -> AsyncIterator[Ledger]:
    """A ledger whose executor yields before every statement, so concurrent use cases
    actually interleave. See `run_yielding`."""
    built = await build_ledger(tmp_path, run_yielding)
    yield built
    await built.database.close()
