"""Persist crossing suppression with the whole quantity transaction, publish after commit."""

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from hashlib import sha256

from ..domain.event import EventPublisher, LowStockCrossed
from ..domain.port.clock import Clock
from ..domain.port.repositories import MovementRepository, SpoolRepository
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.service.low_stock import crossing
from ..domain.value.grams import Grams
from ..domain.value.identifiers import SpoolId
from .query import Queries, SpoolSummary


@dataclass(frozen=True, slots=True)
class LowStock:
    spools: SpoolRepository
    movements: MovementRepository
    queries: Queries
    clock: Clock
    events: EventPublisher

    async def baseline(self, spool_id: SpoolId) -> None:
        spool = await self.spools.get(spool_id)
        if spool is None:
            return
        summary = await self.queries.summarise(spool)
        updated = replace(
            spool,
            low_stock_baseline_threshold_mg=summary.effective_low_stock_threshold_mg,
            low_stock_armed=spool.is_in_inventory
            and summary.balance.milligrams >= summary.effective_low_stock_threshold_mg,
        )
        if updated != spool:
            await self.spools.save(updated)

    @asynccontextmanager
    async def transaction(
        self, uow: UnitOfWork, spool_ids: Iterable[SpoolId] | None = None
    ) -> AsyncIterator[None]:
        occurrences: list[LowStockCrossed] = []
        async with uow:
            if spool_ids is None:
                before = {
                    s.spool.id: s for s in await self.queries.overview(include_discarded=True)
                }
            else:
                before = {}
                for spool_id in set(spool_ids):
                    spool = await self.spools.get(spool_id)
                    if spool is not None:
                        before[spool_id] = await self.queries.summarise(spool)
            yield
            for previous in before.values():
                occurrence = await self._finish(previous)
                if occurrence is not None:
                    occurrences.append(occurrence)
        # shortcut: a crash after commit and before bus publication loses that occurrence; add an outbox only if guaranteed replay becomes required.
        for occurrence in occurrences:
            await self.events.publish(occurrence)

    async def _finish(self, previous: SpoolSummary) -> LowStockCrossed | None:
        spool = await self.spools.get(previous.spool.id)
        if spool is None:
            return None
        current = await self.queries.summarise(spool)
        if current.movement_count == previous.movement_count:
            return None
        now = self.clock.now()
        if spool.opened_at is None and spool.is_in_inventory and previous.movement_count <= 1:
            spool = spool.opened(now)
        threshold = current.effective_low_stock_threshold_mg
        armed = spool.low_stock_armed
        if spool.low_stock_baseline_threshold_mg != threshold:
            armed = previous.balance.milligrams >= threshold
        # A sealed spool can cross on its first recorded use. Eligibility is final-state.
        final_state = spool.state(balance=current.balance, movement_count=current.movement_count)
        emit, next_armed = crossing(
            previous.balance.milligrams,
            current.balance.milligrams,
            threshold,
            armed=armed,
            eligible=spool.is_in_inventory and final_state.value in ("ACTIVE", "DEPLETED"),
        )
        occurrence_id = spool.last_low_stock_occurrence_id
        if emit:
            history = await self.movements.list_for_spool(spool.id)
            new_ids = sorted(str(m.id) for m in history)
            occurrence_id = sha256((str(spool.id) + ":" + ":".join(new_ids)).encode()).hexdigest()
        await self.spools.save(
            replace(
                spool,
                low_stock_armed=next_armed,
                low_stock_baseline_threshold_mg=threshold,
                last_low_stock_occurrence_id=occurrence_id,
            )
        )
        if not emit or occurrence_id is None:
            return None
        return LowStockCrossed(
            occurrence_id,
            spool.id,
            spool.display_name,
            spool.vendor,
            spool.material.display_name,
            "#" + spool.colour.hex8 if spool.colour_known else None,
            current.balance,
            Grams(threshold),
            current.confidence,
            current.opening_basis,
            current.confidence_basis.anchor.value
            if current.confidence_basis.anchor
            else "UNSPECIFIED",
            spool.location,
            now,
        )
