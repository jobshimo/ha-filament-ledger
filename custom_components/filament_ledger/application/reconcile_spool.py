"""UC-08 · ReconcileSpool. The system's ground truth.

The delta is not an embarrassment to be hidden. It is the system's error signal — the only
honest measure of how wrong the estimates have been.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.error import NothingToRecordError, SpoolDiscardedError
from ..domain.event import AnomalyDetected, EventPublisher, MovementRecorded, SpoolDepleted
from ..domain.model.movement import record
from ..domain.port.clock import Clock
from ..domain.port.repositories import MovementRepository, SpoolRepository
from ..domain.service.anomaly_detector import AnomalyDetector
from ..domain.service.balance_calculator import balance
from ..domain.value.grams import Grams
from ..domain.value.identifiers import SpoolId
from ..domain.value.movement_type import MovementSource, MovementType
from .errors import SpoolNotFoundError


@dataclass(frozen=True, slots=True)
class ReconcileSpoolCommand:
    spool_id: SpoolId
    measured: Grams
    includes_core: bool = True
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    delta: Grams
    new_balance: Grams


@dataclass(frozen=True, slots=True)
class ReconcileSpool:
    spools: SpoolRepository
    movements: MovementRepository
    clock: Clock
    events: EventPublisher
    anomalies: AnomalyDetector = field(default_factory=AnomalyDetector)

    async def execute(self, command: ReconcileSpoolCommand) -> ReconcileResult:
        spool = await self.spools.get(command.spool_id)
        if spool is None:
            raise SpoolNotFoundError(command.spool_id)
        if spool.is_discarded:
            msg = f"spool {spool.display_name} was discarded"
            raise SpoolDiscardedError(msg)

        history = await self.movements.list_for_spool(spool.id)
        current = balance(history)

        measured_net = (
            spool.net_from_gross(command.measured) if command.includes_core else command.measured
        )
        delta = measured_net - current

        if delta.is_zero:
            # A zero movement records nothing and only adds noise.
            msg = f"the scale agrees with the ledger at {current}"
            raise NothingToRecordError(msg)

        now = self.clock.now()
        await self.movements.append(
            record(
                spool_id=spool.id,
                type=MovementType.RECONCILIATION,
                amount=delta,
                source=MovementSource.USER_CONFIRMED,
                occurred_at=now,
                note=command.note
                or f"Weighed {command.measured}{' including core' if command.includes_core else ''}",
            )
        )

        await self.events.publish(
            MovementRecorded(
                spool_id=spool.id,
                movement_type=MovementType.RECONCILIATION,
                amount=delta,
                new_balance=measured_net,
            )
        )

        anomaly = self.anomalies.inspect_reconciliation(
            spool_id=spool.id, delta=delta, opening_weight=spool.opening_weight
        )
        if anomaly is not None:
            await self.events.publish(AnomalyDetected(anomaly=anomaly))

        if not measured_net.is_positive:
            await self.events.publish(
                SpoolDepleted(spool_id=spool.id, display_name=spool.display_name)
            )

        return ReconcileResult(delta=delta, new_balance=measured_net)
