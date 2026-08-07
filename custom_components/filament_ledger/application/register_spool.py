"""UC-01 · RegisterSpool.

A spool is born with a ledger entry. There is no such thing as a balance without a movement
that explains it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.error import DuplicateTagNotConfirmedError
from ..domain.event import EventPublisher, SpoolRegistered
from ..domain.model.movement import record
from ..domain.model.spool import Spool, register
from ..domain.port.clock import Clock
from ..domain.port.repositories import MovementRepository, SpoolRepository
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.value.colour import Colour
from ..domain.value.grams import Grams
from ..domain.value.identifiers import SpoolId, TagSource, TagUid
from ..domain.value.location import Location, Storage
from ..domain.value.material import Material
from ..domain.value.movement_type import MovementSource, MovementType


@dataclass(frozen=True, slots=True)
class RegisterSpoolCommand:
    material: Material
    colour: Colour
    opening_weight: Grams
    core_weight: Grams
    vendor: str | None = None
    label: str | None = None
    tag_uid: TagUid | None = None
    # Defaults MANUAL: a tag typed into the register form is the user's. Only the
    # register-from-sync path says DETECTED, because the serial it forwards came off the
    # tray reading rather than off the keyboard (docs/14 §14.2).
    tag_source: TagSource = TagSource.MANUAL
    location: Location | None = None
    confirm_duplicate_tag: bool = False
    # Provenance of the opening balance, for the history's *automatic* / *confirmed by
    # you* label (docs/02 §2.3). Defaults USER_CONFIRMED because on every interactive
    # path the user typed the number; only auto-registration from a tray reading passes
    # AUTOMATIC, because there the figure is a configured default nobody confirmed today.
    movement_source: MovementSource = MovementSource.USER_CONFIRMED


@dataclass(frozen=True, slots=True)
class RegisterSpool:
    spools: SpoolRepository
    movements: MovementRepository
    clock: Clock
    events: EventPublisher
    uow: UnitOfWork

    async def execute(self, command: RegisterSpoolCommand) -> SpoolId:
        # One unit of work from the duplicate-tag read to the movement append: the guard
        # is still true when the writes happen, and a crash between the two writes cannot
        # leave a spool behind with no movement that explains its balance.
        async with self.uow:
            await self._guard_duplicate_tag(command)

            now = self.clock.now()
            spool: Spool = register(
                material=command.material,
                colour=command.colour,
                opening_weight=command.opening_weight,
                core_weight=command.core_weight,
                registered_at=now,
                location=command.location if command.location is not None else Storage(),
                vendor=command.vendor,
                label=command.label,
                tag_uid=command.tag_uid,
                # Provenance describes a tag, so an untagged spool carries none — the
                # command's default would otherwise fail the entity's pairing check.
                tag_source=command.tag_source if command.tag_uid is not None else None,
            )

            await self.spools.save(spool)
            await self.movements.append(
                record(
                    spool_id=spool.id,
                    type=MovementType.OPENING_BALANCE,
                    amount=command.opening_weight,
                    source=command.movement_source,
                    occurred_at=now,
                    note="Registered",
                )
            )

        # Published after the commit — an event for a write that could still roll back
        # would announce a spool that never existed.
        await self.events.publish(
            SpoolRegistered(spool_id=spool.id, display_name=spool.display_name)
        )
        return spool.id

    async def _guard_duplicate_tag(self, command: RegisterSpoolCommand) -> None:
        """Duplicates are legal — a Bambu tag identifies a batch, not a unit — but they must
        be deliberate. The caller sees the conflicting spool and says yes."""
        if command.tag_uid is None or command.confirm_duplicate_tag:
            return
        existing = await self.spools.find_by_tag(command.tag_uid)
        if existing:
            names = ", ".join(spool.display_name for spool in existing)
            msg = (
                f"tag {command.tag_uid} already belongs to {len(existing)} spool(s) "
                f"({names}). Set confirm_duplicate_tag to register anyway."
            )
            raise DuplicateTagNotConfirmedError(msg)
