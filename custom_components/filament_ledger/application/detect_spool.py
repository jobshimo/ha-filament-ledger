"""UC-02 · MountSpool and UC-03 · UnmountSpool, automatic paths.

The printer gateway reports what one tray looks like now; this use case reconciles the
ledger with that observation. It refuses in two directions on purpose (docs/04-use-cases.md
UC-02): an unknown tag never creates a spool, because a guessed opening weight is a
fabricated number, and an ambiguous tag never picks a candidate, because choosing wrong
means every subsequent print deducts from a spool sitting on a shelf.

**Nothing here records a movement.** Both paths only move spools; moving a spool consumes
no filament.

An occupied tray whose tag is unreadable changes nothing. The use cases in
docs/04-use-cases.md authorise automatic action on a tag appearing (UC-02) or the tray
emptying (UC-03) — an unreadable tag is neither. It identifies no spool to mount, and the
spool the ledger has in that slot may well be the untagged one the user mounted by hand;
unmounting it on silence would fight every manual mount of a third-party spool.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.event import (
    AmbiguousTagDetected,
    DomainEvent,
    EventPublisher,
    SpoolDetected,
    SpoolMounted,
    SpoolUnmounted,
    UnknownSpoolDetected,
)
from ..domain.port.repositories import SpoolRepository
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.value.identifiers import SlotIndex, TagUid
from ..domain.value.location import AmsSlot
from ..domain.value.tray_reading import TrayReading
from .move_spool import displace_and_mount


@dataclass(frozen=True, slots=True)
class DetectSpool:
    """One tray change, resolved against the inventory.

    Idempotent by design: the gateway replays `current_trays()` on startup, so the same
    reading arrives more than once and must change nothing the second time.
    """

    spools: SpoolRepository
    events: EventPublisher
    uow: UnitOfWork
    # A plain value, not a callable: changing options reloads the config entry (see
    # `_reload_on_options_change` in the package root), which rebuilds every use case with
    # fresh settings — so this can never go stale.
    auto_mount: bool

    async def execute(self, reading: TrayReading) -> None:
        if reading.empty:
            await self._tray_emptied(reading.slot)
            return
        if reading.tag is None:
            return  # unreadable tag: nothing automatic is possible — see the module docstring
        if not self.auto_mount:
            # Informational, and deliberately unresolved: the user asked the system not to
            # move spools, so it reports the sighting and the AMS view offers a manual
            # [ Mount ] button instead.
            await self.events.publish(SpoolDetected(tag_uid=reading.tag, slot=reading.slot))
            return
        await self._tag_appeared(reading.tag, reading.slot)

    async def _tray_emptied(self, slot: SlotIndex) -> None:
        """UC-03, automatic: the spool left the machine, so it is in storage now.

        No occupant, no work — the replayed reading of an empty tray must not invent an
        unmount for a spool that was never there.
        """
        async with self.uow:
            occupant = await self.spools.find_by_location(AmsSlot(slot))
            if occupant is not None:
                await self.spools.save(occupant.unmounted())
        # Published after the commit — never for a write that could still roll back.
        if occupant is not None:
            await self.events.publish(SpoolUnmounted(spool_id=occupant.id))

    async def _tag_appeared(self, tag: TagUid, slot: SlotIndex) -> None:
        """UC-02, automatic: resolve the tag, then mount — or ask.

        The whole read-compute-write runs inside one unit of work: resolution and
        displacement must see the same inventory, or a concurrent mount could interleave
        between the lookup and the write.
        """
        to_publish: list[DomainEvent] = []
        async with self.uow:
            # `find_by_tag` already excludes discarded spools: a discarded spool is out of
            # inventory, and a tag matching only discarded spools is an unknown tag.
            candidates = await self.spools.find_by_tag(tag)
            if not candidates:
                to_publish.append(UnknownSpoolDetected(tag_uid=tag, slot=slot))
            elif len(candidates) > 1:
                to_publish.append(
                    AmbiguousTagDetected(
                        tag_uid=tag,
                        slot=slot,
                        candidates=tuple(spool.id for spool in candidates),
                    )
                )
            elif (spool := candidates[0]).location != AmsSlot(slot):
                displaced = await displace_and_mount(self.spools, spool, slot)
                if displaced is not None:
                    to_publish.append(SpoolUnmounted(spool_id=displaced))
                to_publish.append(SpoolMounted(spool_id=spool.id, slot=slot))
            # Already mounted here: the replayed reading confirms the ledger. No write,
            # no event — announcing a mount that did not happen would be a lie.
        for event in to_publish:
            await self.events.publish(event)
