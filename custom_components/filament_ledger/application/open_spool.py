"""Opening a package changes lifecycle metadata, never its ledger quantity."""

from dataclasses import dataclass

from ..domain.port.clock import Clock
from ..domain.port.repositories import SpoolRepository
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.value.identifiers import SpoolId
from .errors import SpoolNotFoundError


@dataclass(frozen=True, slots=True)
class OpenSpool:
    spools: SpoolRepository
    clock: Clock
    uow: UnitOfWork

    async def execute(self, spool_id: SpoolId) -> None:
        async with self.uow:
            spool = await self.spools.get(spool_id)
            if spool is None:
                raise SpoolNotFoundError(spool_id)
            await self.spools.save(spool.opened(self.clock.now()))
