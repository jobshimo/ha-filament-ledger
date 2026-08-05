"""UC-02 · MountSpool and UC-03 · UnmountSpool, manual paths.

The automatic RFID paths live in `detect_spool`, driven by the printer gateway; both paths
share `displace_and_mount`, so "at most one spool per slot" has exactly one implementation.

**Neither records a movement.** Moving a spool consumes no filament. Keeping *location
change* and *quantity change* strictly separate is how an inventory system avoids starting
to lie.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.error import DuplicateTagNotConfirmedError
from ..domain.event import EventPublisher, SpoolMounted, SpoolUnmounted
from ..domain.model.spool import Spool
from ..domain.port.clock import Clock
from ..domain.port.repositories import SpoolRepository
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.value.colour import Colour
from ..domain.value.grams import Grams
from ..domain.value.identifiers import SpoolId, TagSource, TagUid, TrayRef
from ..domain.value.location import AmsSlot
from ..domain.value.material import Material
from .errors import SpoolNotFoundError


class _Unset:
    """The third state of the `tag` parameter below.

    `None` already means *clear the tag*, so "leave it alone" needs a value of its own —
    and a module-level sentinel says so at the call site, where `UNSET` reads as the
    absence of an instruction rather than as an instruction to erase.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging affordance
        return "UNSET"


UNSET = _Unset()

# What a caller may pass for `tag`: leave unchanged, clear, or set.
TagEdit = TagUid | None | _Unset


async def displace_and_mount(
    spools: SpoolRepository, spool: Spool, tray: TrayRef
) -> SpoolId | None:
    """Put `spool` in `tray`, displacing whatever is there to storage first.

    At most one spool per tray; displacing before mounting means the partial unique index
    is never momentarily violated. Returns the displaced spool's id so the caller can
    announce it — this helper writes and publishes nothing itself, because it must run
    inside the caller's unit of work and events belong after the commit.
    """
    displaced: SpoolId | None = None
    occupant = await spools.find_by_location(AmsSlot(tray))
    if occupant is not None and occupant.id != spool.id:
        await spools.save(occupant.unmounted())
        displaced = occupant.id
    await spools.save(spool.mounted_in(tray))
    return displaced


@dataclass(frozen=True, slots=True)
class MountSpool:
    spools: SpoolRepository
    clock: Clock
    events: EventPublisher
    # Mounting is a read-then-two-writes sequence. Two concurrent mounts into the same
    # tray could interleave between the displacement and the mount, and land on the
    # partial unique index as a raw `IntegrityError` instead of a rule the user can read.
    # The unit of work serialises the whole sequence and makes it atomic besides: the
    # displaced occupant and the mounted spool commit together or not at all.
    uow: UnitOfWork

    async def execute(self, spool_id: SpoolId, tray: TrayRef) -> None:
        async with self.uow:
            spool = await self.spools.get(spool_id)
            if spool is None:
                raise SpoolNotFoundError(spool_id)
            displaced = await displace_and_mount(self.spools, spool, tray)

        # Published after the commit — never for a write that could still roll back.
        if displaced is not None:
            await self.events.publish(SpoolUnmounted(spool_id=displaced))
        await self.events.publish(SpoolMounted(spool_id=spool_id, tray=tray))


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
    decorative. The edit dialog's weight-correction section calls `ReconcileSpool` or
    `AdjustSpool` as a *second* command for exactly that reason (docs/14 §14.2).

    Every metadata field follows `Spool.with_details`: `None` means "leave unchanged". The
    tag is the exception, and the only clearable field — see `tag` below.
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
        tag: TagEdit = UNSET,
        confirm_duplicate_tag: bool = False,
    ) -> None:
        """`tag` is tri-state: `UNSET` leaves it, `None` clears it, a `TagUid` sets it.

        A tag set here is MANUAL by definition — the user typed it. A tag whose provenance
        is DETECTED refuses every one of the three, in `Spool.with_tag`.
        """
        # The unit of work keeps the read-modify-save indivisible, so two concurrent
        # edits cannot interleave and silently drop one another's fields — and the
        # duplicate-tag guard is still true when the write happens.
        async with self.uow:
            spool = await self.spools.get(spool_id)
            if spool is None:
                raise SpoolNotFoundError(spool_id)
            edited = spool.with_details(
                label=label,
                vendor=vendor,
                colour=colour,
                material=material,
                core_weight=core_weight,
            )
            if not isinstance(tag, _Unset):
                if tag is not None:
                    await self._guard_duplicate_tag(spool, tag, confirm_duplicate_tag)
                edited = edited.with_tag(tag, TagSource.MANUAL if tag is not None else None)
            await self.spools.save(edited)

    async def _guard_duplicate_tag(self, spool: Spool, tag: TagUid, confirmed: bool) -> None:
        """UC-01's rule, for the same reason and with the same wording (docs/14 §14.2).

        A Bambu tag identifies a batch, not a unit, so duplicates are legal — but they are
        deliberate or they are a bug. The spool being edited is not its own duplicate.
        """
        if confirmed:
            return
        others = [other for other in await self.spools.find_by_tag(tag) if other.id != spool.id]
        if others:
            names = ", ".join(other.display_name for other in others)
            msg = (
                f"tag {tag} already belongs to {len(others)} spool(s) ({names}). "
                f"Set confirm_duplicate_tag to attach it anyway."
            )
            raise DuplicateTagNotConfirmedError(msg)
