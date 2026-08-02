"""UC-02 · MountSpool and UC-03 · UnmountSpool, manual paths.

The automatic RFID paths live in `detect_spool`, driven by the printer gateway; both paths
share `displace_and_mount`, so "at most one spool per slot" has exactly one implementation.

**Neither records a movement.** Moving a spool consumes no filament. Keeping *location
change* and *quantity change* strictly separate is how an inventory system avoids starting
to lie.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.event import EventPublisher, SpoolMounted, SpoolUnmounted
from ..domain.model.spool import Spool
from ..domain.port.clock import Clock
from ..domain.port.repositories import SpoolRepository
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.value.colour import Colour
from ..domain.value.grams import Grams
from ..domain.value.identifiers import SlotIndex, SpoolId
from ..domain.value.location import AmsSlot
from ..domain.value.material import Material
from .errors import SpoolNotFoundError


async def displace_and_mount(
    spools: SpoolRepository, spool: Spool, slot: SlotIndex
) -> SpoolId | None:
    """Put `spool` in `slot`, displacing whatever is there to storage first.

    At most one spool per slot; displacing before mounting means the partial unique index
    is never momentarily violated. Returns the displaced spool's id so the caller can
    announce it — this helper writes and publishes nothing itself, because it must run
    inside the caller's unit of work and events belong after the commit.
    """
    displaced: SpoolId | None = None
    occupant = await spools.find_by_location(AmsSlot(slot))
    if occupant is not None and occupant.id != spool.id:
        await spools.save(occupant.unmounted())
        displaced = occupant.id
    await spools.save(spool.mounted_in(slot))
    return displaced


@dataclass(frozen=True, slots=True)
class MountSpool:
    spools: SpoolRepository
    clock: Clock
    events: EventPublisher
    # Mounting is a read-then-two-writes sequence. Two concurrent mounts into the same
    # slot could interleave between the displacement and the mount, and land on the
    # partial unique index as a raw `IntegrityError` instead of a rule the user can read.
    # The unit of work serialises the whole sequence and makes it atomic besides: the
    # displaced occupant and the mounted spool commit together or not at all.
    uow: UnitOfWork

    async def execute(self, spool_id: SpoolId, slot: SlotIndex) -> None:
        async with self.uow:
            spool = await self.spools.get(spool_id)
            if spool is None:
                raise SpoolNotFoundError(spool_id)
            displaced = await displace_and_mount(self.spools, spool, slot)

        # Published after the commit — never for a write that could still roll back.
        if displaced is not None:
            await self.events.publish(SpoolUnmounted(spool_id=displaced))
        await self.events.publish(SpoolMounted(spool_id=spool_id, slot=slot))


@dataclass(frozen=True, slots=True)
class UnmountSpool:
    spools: SpoolRepository
    events: EventPublisher
    uow: UnitOfWork

    async def execute(self, spool_id: SpoolId) -> None:
        async with self.uow:
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
    uow: UnitOfWork

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
        # The unit of work keeps the read-modify-save indivisible, so two concurrent
        # edits cannot interleave and silently drop one another's fields.
        async with self.uow:
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
