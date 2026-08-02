"""UC-09 · DiscardFilament and UC-10 · AdjustSpool.

Both take a mandatory reason. An unexplained adjustment in a ledger is indistinguishable
from a bug, and six months later nobody will remember either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..domain.error import InvalidValueError, SpoolDiscardedError
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


class DiscardMode(StrEnum):
    WHOLE_SPOOL = "whole_spool"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class DiscardFilamentCommand:
    spool_id: SpoolId
    mode: DiscardMode
    reason: str
    amount: Grams | None = None


@dataclass(frozen=True, slots=True)
class AdjustSpoolCommand:
    spool_id: SpoolId
    amount: Grams
    reason: str


@dataclass(frozen=True, slots=True)
class DiscardFilament:
    spools: SpoolRepository
    movements: MovementRepository
    clock: Clock
    events: EventPublisher
    anomalies: AnomalyDetector = field(default_factory=AnomalyDetector)

    async def execute(self, command: DiscardFilamentCommand) -> Grams:
        if not command.reason.strip():
            msg = "a discard needs a reason"
            raise InvalidValueError(msg)

        spool = await self.spools.get(command.spool_id)
        if spool is None:
            raise SpoolNotFoundError(command.spool_id)
        if spool.is_discarded:
            msg = f"spool {spool.display_name} was already discarded"
            raise SpoolDiscardedError(msg)

        history = await self.movements.list_for_spool(spool.id)
        current = balance(history)
        now = self.clock.now()

        if command.mode is DiscardMode.WHOLE_SPOOL:
            amount = current
            if not amount.is_positive:
                # Nothing left to write off, but the spool still leaves the inventory.
                await self.spools.save(spool.discarded(now))
                return Grams.zero()
        else:
            if command.amount is None or not command.amount.is_positive:
                msg = "a partial discard needs a positive amount"
                raise InvalidValueError(msg)
            amount = command.amount

        await self.movements.append(
            record(
                spool_id=spool.id,
                type=MovementType.DISCARD,
                amount=-amount,
                source=MovementSource.USER_CONFIRMED,
                occurred_at=now,
                note=command.reason,
            )
        )

        new_balance = current - amount
        if command.mode is DiscardMode.WHOLE_SPOOL:
            await self.spools.save(spool.discarded(now))

        await self.events.publish(
            MovementRecorded(
                spool_id=spool.id,
                movement_type=MovementType.DISCARD,
                amount=-amount,
                new_balance=new_balance,
            )
        )

        # Discarding more than the balance is *permitted*. The physical event happened; the
        # ledger records reality and flags the inconsistency rather than refusing the truth.
        for anomaly in self.anomalies.inspect(
            spool_id=spool.id, balance=new_balance, location=spool.location
        ):
            await self.events.publish(AnomalyDetected(anomaly=anomaly))

        if not new_balance.is_positive:
            await self.events.publish(
                SpoolDepleted(spool_id=spool.id, display_name=spool.display_name)
            )

        return new_balance


@dataclass(frozen=True, slots=True)
class AdjustSpool:
    spools: SpoolRepository
    movements: MovementRepository
    clock: Clock
    events: EventPublisher
    anomalies: AnomalyDetector = field(default_factory=AnomalyDetector)

    async def execute(self, command: AdjustSpoolCommand) -> Grams:
        if not command.reason.strip():
            msg = "an adjustment needs a reason"
            raise InvalidValueError(msg)
        if command.amount.is_zero:
            msg = "a zero adjustment records nothing"
            raise InvalidValueError(msg)

        spool = await self.spools.get(command.spool_id)
        if spool is None:
            raise SpoolNotFoundError(command.spool_id)
        if spool.is_discarded:
            msg = f"spool {spool.display_name} was discarded"
            raise SpoolDiscardedError(msg)

        history = await self.movements.list_for_spool(spool.id)
        now = self.clock.now()

        await self.movements.append(
            record(
                spool_id=spool.id,
                type=MovementType.MANUAL_ADJUSTMENT,
                amount=command.amount,
                source=MovementSource.USER_CONFIRMED,
                occurred_at=now,
                note=command.reason,
            )
        )

        new_balance = balance(history) + command.amount
        await self.events.publish(
            MovementRecorded(
                spool_id=spool.id,
                movement_type=MovementType.MANUAL_ADJUSTMENT,
                amount=command.amount,
                new_balance=new_balance,
            )
        )
        for anomaly in self.anomalies.inspect(
            spool_id=spool.id, balance=new_balance, location=spool.location
        ):
            await self.events.publish(AnomalyDetected(anomaly=anomaly))

        return new_balance
