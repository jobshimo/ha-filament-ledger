"""UC-02 · MountSpool and UC-03 · UnmountSpool, manual paths only.

The automatic RFID paths arrive in Phase 2 with the printer gateway. These are the manual
variants, which is all the "manual inventory only" mode needs.

**Neither records a movement.** Moving a spool consumes no filament. Keeping *location
change* and *quantity change* strictly separate is how an inventory system avoids starting
to lie.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ..domain.event import EventPublisher, SpoolMounted, SpoolUnmounted
from ..domain.port.clock import Clock
from ..domain.port.repositories import SpoolRepository
from ..domain.value.colour import Colour
from ..domain.value.grams import Grams
from ..domain.value.identifiers import SlotIndex, SpoolId
from ..domain.value.location import AmsSlot
from ..domain.value.material import Material
from .errors import SpoolNotFoundError


@dataclass(frozen=True, slots=True)
class MountSpool:
    spools: SpoolRepository
    clock: Clock
    events: EventPublisher
    # Mounting is a read-then-two-writes sequence, and the database serialises each write
    # individually rather than the sequence. Two concurrent mounts into the same slot could
    # therefore interleave between the displacement and the mount, and land on the partial
    # unique index as a raw `IntegrityError` instead of a rule the user can read.
    #
    # A single lock is the whole fix. Mounting is a human action a few times a week; there
    # is nothing to gain from letting two of them overlap.
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def execute(self, spool_id: SpoolId, slot: SlotIndex) -> None:
        async with self._lock:
            spool = await self.spools.get(spool_id)
            if spool is None:
                raise SpoolNotFoundError(spool_id)

            # At most one spool per slot. Whatever is there is displaced to storage first,
            # so the unique index is never momentarily violated.
            occupant = await self.spools.find_by_location(AmsSlot(slot))
            if occupant is not None and occupant.id != spool_id:
                await self.spools.save(occupant.unmounted())
                await self.events.publish(SpoolUnmounted(spool_id=occupant.id))

            await self.spools.save(spool.mounted_in(slot))

        await self.events.publish(SpoolMounted(spool_id=spool_id, slot=slot))


@dataclass(frozen=True, slots=True)
class UnmountSpool:
    spools: SpoolRepository
    events: EventPublisher

    async def execute(self, spool_id: SpoolId) -> None:
        spool = await self.spools.get(spool_id)
        if spool is None:
            raise SpoolNotFoundError(spool_id)
        await self.spools.save(spool.unmounted())
        await self.events.publish(SpoolUnmounted(spool_id=spool_id))


@dataclass(frozen=True, slots=True)
class EditSpoolDetails:
    """Metadata only. **Never the balance.**

    There is no endpoint that sets a balance directly. Changing one requires a movement, and
    that is the whole design — an API that could set a balance would make the ledger
    decorative.
    """

    spools: SpoolRepository

    async def execute(
        self,
        spool_id: SpoolId,
        *,
        label: str | None = None,
        vendor: str | None = None,
        colour: Colour | None = None,
        material: Material | None = None,
        core_weight: Grams | None = None,
    ) -> None:
        spool = await self.spools.get(spool_id)
        if spool is None:
            raise SpoolNotFoundError(spool_id)
        await self.spools.save(
            spool.with_details(
                label=label,
                vendor=vendor,
                colour=colour,
                material=material,
                core_weight=core_weight,
            )
        )
