"""UC-16 · DeleteSpool and UC-17 · RestoreSpool — the retraction, and taking it back.

    "The X on a spool asks what actually happened: 'Did you throw it away?' — then it's
    waste and counts as waste. 'Was it registered by mistake?' — then it was never really
    here."

The first answer is UC-09's existing `DiscardFilament`, unchanged. The second is here:
a bookkeeping retraction that sets `deleted_at`, frees the slot, and **writes no
movement** — deletion is a location-and-state change, and UC-03's strict separation of
location change from quantity change extends to it (docs/14 §14.4.3).

Visibility follows from the spool's state, not from per-movement void rows. Retracting a
registration is *one* fact about the spool; stamping forty movement voids would record it
forty times, and restoring would then have to unstamp forty. Because the rule is derived,
"restore brings the spool back — and its history comes back with it" needs no second step.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.error import InvalidValueError
from ..domain.event import EventPublisher, SpoolDeleted, SpoolRestored
from ..domain.port.clock import Clock
from ..domain.port.repositories import SpoolRepository
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.value.identifiers import SpoolId
from .errors import SpoolNotFoundError


@dataclass(frozen=True, slots=True)
class DeleteSpool:
    spools: SpoolRepository
    clock: Clock
    events: EventPublisher
    uow: UnitOfWork

    async def execute(self, spool_id: SpoolId) -> None:
        # One unit of work around read-modify-save. The state change and the freed slot
        # are one write on one row, and the unit is what keeps a concurrent mount from
        # reading an occupied slot this delete is in the middle of releasing.
        async with self.uow:
            spool = await self.spools.get(spool_id)
            if spool is None:
                raise SpoolNotFoundError(spool_id)
            # The entity owns the guards: not discarded, not already deleted, location
            # cleared. `DELETED` and `DISCARDED` stay mutually exclusive by flow.
            await self.spools.save(spool.deleted(self.clock.now()))

        # Published after the commit — never for a write that could still roll back.
        await self.events.publish(SpoolDeleted(spool_id=spool.id, display_name=spool.display_name))


@dataclass(frozen=True, slots=True)
class RestoreSpool:
    spools: SpoolRepository
    events: EventPublisher
    uow: UnitOfWork

    async def execute(self, spool_id: SpoolId) -> None:
        """Back to inventory, in storage. **The old slot is not reclaimed.**

        It was freed on delete and something else may be in it by now; taking it back
        would displace a spool the user physically loaded, on the strength of a position
        this ledger recorded before the retraction. Storage is the honest landing place.
        """
        async with self.uow:
            spool = await self.spools.get(spool_id)
            if spool is None:
                raise SpoolNotFoundError(spool_id)
            if not spool.is_deleted:
                msg = (
                    f"spool {spool.display_name} is not in the trash, so there is nothing "
                    f"to restore"
                )
                raise InvalidValueError(msg)
            await self.spools.save(spool.restored())

        # Published after the commit — never for a write that could still roll back.
        await self.events.publish(SpoolRestored(spool_id=spool.id, display_name=spool.display_name))
