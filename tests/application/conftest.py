"""A whole ledger, wired against a real SQLite file and nothing else.

Deliberately not a fake database. The point of these tests is to verify the mapping and the
constraints, and a fake verifies neither — the triggers, the partial unique indexes and the
`CHECK` clauses are half the design.

Home Assistant is absent. That is the architecture's central claim, and this fixture is what
makes it observable: the entire product below the adapter layer runs on `run_inline`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from custom_components.filament_ledger.application.adjust_spool import (
    AdjustSpool,
    DiscardFilament,
)
from custom_components.filament_ledger.application.move_spool import (
    EditSpoolDetails,
    MountSpool,
    UnmountSpool,
)
from custom_components.filament_ledger.application.query import Queries
from custom_components.filament_ledger.application.reconcile_spool import ReconcileSpool
from custom_components.filament_ledger.application.register_spool import RegisterSpool
from custom_components.filament_ledger.application.use_cases import UseCases
from custom_components.filament_ledger.domain.event import DomainEvent
from custom_components.filament_ledger.infrastructure.persistence.database import (
    Database,
    run_inline,
)
from custom_components.filament_ledger.infrastructure.persistence.movement_repository import (
    SqliteMovementRepository,
)
from custom_components.filament_ledger.infrastructure.persistence.spool_repository import (
    SqliteSpoolRepository,
)

EPOCH = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


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

    def of(self, kind: type[DomainEvent]) -> list[DomainEvent]:
        return [event for event in self.published if isinstance(event, kind)]


@dataclass
class Ledger:
    use_cases: UseCases
    clock: FakeClock
    events: RecordingEventBus
    database: Database


@pytest.fixture
async def ledger(tmp_path: Path) -> AsyncIterator[Ledger]:
    database = await Database.open(tmp_path / "ledger.db", run_inline)
    await database.migrate()

    spools = SqliteSpoolRepository(database)
    movements = SqliteMovementRepository(database)
    clock = FakeClock()
    events = RecordingEventBus()

    yield Ledger(
        use_cases=UseCases(
            register_spool=RegisterSpool(spools, movements, clock, events),
            reconcile_spool=ReconcileSpool(spools, movements, clock, events),
            discard_filament=DiscardFilament(spools, movements, clock, events),
            adjust_spool=AdjustSpool(spools, movements, clock, events),
            mount_spool=MountSpool(spools, clock, events),
            unmount_spool=UnmountSpool(spools, events),
            edit_spool_details=EditSpoolDetails(spools),
            queries=Queries(spools=spools, movements=movements),
        ),
        clock=clock,
        events=events,
        database=database,
    )

    await database.close()
