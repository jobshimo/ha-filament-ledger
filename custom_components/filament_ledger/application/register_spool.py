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
from ..domain.value.colour import Colour
from ..domain.value.grams import Grams
from ..domain.value.identifiers import SpoolId, TagUid
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
    location: Location | None = None
    confirm_duplicate_tag: bool = False


@dataclass(frozen=True, slots=True)
class RegisterSpool:
    spools: SpoolRepository
    movements: MovementRepository
    clock: Clock
    events: EventPublisher

    async def execute(self, command: RegisterSpoolCommand) -> SpoolId:
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
        )

        await self.spools.save(spool)
        await self.movements.append(
            record(
                spool_id=spool.id,
                type=MovementType.OPENING_BALANCE,
                amount=command.opening_weight,
                source=MovementSource.USER_CONFIRMED,
                occurred_at=now,
                note="Registered",
            )
        )
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
