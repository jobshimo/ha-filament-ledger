"""UC-14 · VoidMovement and UC-15 · RestoreMovement — the X on a history row, and its undo.

    "The X on a History row — manual or automatic — asks: 'This returns X g to [spool]'.
    Confirming deletes the entry from the history I see, and the grams come back."

Under the hood nothing is deleted. Confirming **voids** the entry: one row in
`movement_void` records the deletion, and a `VOID_REVERSAL` movement — the exact negation —
returns the grams. Restoring appends a `REINSTATEMENT` equal to the original and closes the
chapter. The `movement` table and its immutability triggers are untouched throughout; the
void row plus the reversal *are* the record (docs/adr/0007, docs/14 §14.4.1-2).

Chains are legal and honest. Void m₁ (reversal m₂) → restore (reinstatement m₃) → void m₃
(reversal m₄) → … Each step is one new movement plus one new void row keyed by a *different*
movement id, so `movement_void`'s primary key never has to bend.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.error import (
    InvalidValueError,
    MovementAlreadyVoidedError,
    MovementNotVoidableError,
    MovementNotVoidedError,
    RestitutionUnavailableError,
    SpoolDeletedError,
    SpoolDiscardedError,
    VoidNotReinstatableError,
)
from ..domain.event import (
    AnomalyDetected,
    DomainEvent,
    EventPublisher,
    MovementRecorded,
    MovementReinstated,
    MovementVoided,
    SpoolRestored,
)
from ..domain.model.movement import Movement, record
from ..domain.model.movement_void import MovementVoid
from ..domain.model.spool import Spool
from ..domain.port.clock import Clock
from ..domain.port.repositories import (
    MovementRepository,
    MovementVoidRepository,
    SpoolRepository,
)
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.service.anomaly_detector import AnomalyDetector
from ..domain.service.balance_calculator import balance
from ..domain.value.grams import Grams
from ..domain.value.identifiers import MovementId
from ..domain.value.movement_type import MovementSource, MovementType
from .errors import MovementNotFoundError, SpoolNotFoundError

# The two types that never leave the history the user sees, each for its own stated reason
# (docs/14 §14.4.1). Kept as data rather than an `if` chain so the refusal and its
# explanation cannot drift apart.
NOT_VOIDABLE: dict[MovementType, str] = {
    MovementType.OPENING_BALANCE: (
        "a spool is born with its opening entry, and a spool whose opening entry is "
        "deleted has a balance with no origin. Delete the spool instead — that retires "
        "the whole story coherently"
    ),
    MovementType.VOID_REVERSAL: (
        "a correction is corrected through its own flow: restore the entry from the "
        "trash. Deleting the correction would fork the provenance chain into two "
        "competing readings of the same grams"
    ),
}


@dataclass(frozen=True, slots=True)
class VoidMovementCommand:
    """`without_restitution` must be explicitly true for the no-return branch.

    The server refuses a restitution void on a retired spool rather than silently
    downgrading it, because a silent downgrade is a gram count that changed meaning
    without the user noticing (docs/14 §14.4).
    """

    movement_id: MovementId
    reason: str | None = None
    without_restitution: bool = False


@dataclass(frozen=True, slots=True)
class VoidMovement:
    spools: SpoolRepository
    movements: MovementRepository
    voids: MovementVoidRepository
    clock: Clock
    events: EventPublisher
    uow: UnitOfWork
    anomalies: AnomalyDetector = field(default_factory=AnomalyDetector)

    async def execute(self, command: VoidMovementCommand) -> Grams | None:
        """Returns the grams that came back, or `None` for a void without restitution."""
        to_publish: list[DomainEvent] = []

        # One unit of work: the void row and the reversal commit together or not at all.
        # Half of this pair is the worst outcome available — a chapter recorded with no
        # grams returned, or grams returned with nothing saying why.
        async with self.uow:
            movement = await self.movements.get(command.movement_id)
            if movement is None:
                raise MovementNotFoundError(command.movement_id)
            self._guard_voidable(movement)
            await self._guard_not_already_voided(command.movement_id)

            spool = await self.spools.get(movement.spool_id)
            if spool is None:  # pragma: no cover - the movement's foreign key backs this
                raise SpoolNotFoundError(movement.spool_id)

            now = self.clock.now()
            if command.without_restitution:
                await self.voids.append(
                    MovementVoid(
                        movement_id=movement.id,
                        voided_at=now,
                        # Mandatory here, and the entity enforces it: the record must say
                        # why nothing came back, because a null reversal with no
                        # explanation reads as a bug six months later.
                        reason=_required_reason(command.reason),
                        reversal_movement_id=None,
                    )
                )
                returned = None
                undiscarded = None
            else:
                # Decided **before** anything is appended, so the discriminator reads the
                # history as it stood — and before the retirement guard, because voiding
                # the whole-spool discard is precisely how a discarded spool comes back.
                # Guarding first would make the un-discard unreachable.
                undiscards = await self._is_whole_spool_discard(spool, movement)
                if not undiscards:
                    self._guard_restitution_has_somewhere_to_land(spool)
                reversal = record(
                    spool_id=movement.spool_id,
                    type=MovementType.VOID_REVERSAL,
                    # The exact negation. `EITHER` direction, because voiding a +6.2 g
                    # reconciliation must be able to produce −6.2 g.
                    amount=-movement.amount,
                    source=MovementSource.USER_CONFIRMED,
                    occurred_at=now,
                    note=_reversal_note(command.reason),
                    # Inherited so per-print accounting nets to zero: a voided print
                    # charge cancels its own cost with no special case.
                    job_id=movement.job_id,
                    review_id=movement.review_id,
                )
                await self.movements.append(reversal)
                await self.voids.append(
                    MovementVoid(
                        movement_id=movement.id,
                        voided_at=now,
                        reason=(command.reason or "").strip() or None,
                        reversal_movement_id=reversal.id,
                    )
                )
                returned = reversal.amount
                undiscarded = None
                if undiscards:
                    # The restitution returns the entire balance, and leaving the spool
                    # DISCARDED would strand those grams outside inventory. One recorded
                    # operation, one transaction (docs/14 §14.4.1).
                    undiscarded = spool.restored_from_discard()
                    await self.spools.save(undiscarded)

            new_balance = balance(await self.movements.list_for_spool(movement.spool_id))

        # Published after the commit — never for a write that could still roll back.
        if returned is not None:
            to_publish.append(
                MovementRecorded(
                    spool_id=movement.spool_id,
                    movement_type=MovementType.VOID_REVERSAL,
                    amount=returned,
                    new_balance=new_balance,
                )
            )
        to_publish.append(
            MovementVoided(movement_id=movement.id, spool_id=movement.spool_id, returned=returned)
        )
        if undiscarded is not None:
            to_publish.append(
                SpoolRestored(spool_id=undiscarded.id, display_name=undiscarded.display_name)
            )
        for anomaly in self.anomalies.inspect(
            spool_id=spool.id, balance=new_balance, location=spool.location
        ):
            to_publish.append(AnomalyDetected(anomaly=anomaly))

        for event in to_publish:
            await self.events.publish(event)
        return returned

    @staticmethod
    def _guard_voidable(movement: Movement) -> None:
        why = NOT_VOIDABLE.get(movement.type)
        if why is not None:
            raise MovementNotVoidableError(f"{movement.type} cannot be deleted: {why}")

    async def _guard_not_already_voided(self, movement_id: MovementId) -> None:
        """The primary key would refuse a second chapter anyway; checking first is what
        turns a constraint name into a sentence. A *closed* chapter still blocks: the
        re-void goes on the reinstatement, which is a different movement with its own id."""
        if await self.voids.get(movement_id) is not None:
            msg = f"movement {movement_id} has already been deleted once"
            raise MovementAlreadyVoidedError(msg)

    @staticmethod
    def _guard_restitution_has_somewhere_to_land(spool: Spool) -> None:
        """Grams only return to a spool in inventory (docs/14 §14.4.1).

        A reversal landing on a retired spool would be a balance change nobody can see —
        it is out of every default view by definition. The modal offers the two honest
        alternatives instead: restore the spool first, or delete the entry without getting
        anything back.
        """
        if spool.is_in_inventory:
            return
        route = (
            "restore it from the trash first"
            if spool.is_deleted
            else "delete its whole-spool discard entry first, which is what brings it back"
        )
        msg = (
            f"spool {spool.display_name} is out of inventory, so there is nothing to "
            f"return the grams to — {route}, or delete this entry without restitution "
            f"and say why"
        )
        raise RestitutionUnavailableError(msg)

    async def _is_whole_spool_discard(self, spool: Spool, movement: Movement) -> bool:
        """The one special case, stated rather than discovered (docs/14 §14.4.1).

        Voiding a **whole-spool** `DISCARD` returns the entire balance, and leaving the
        spool `DISCARDED` would strand those grams outside inventory. So the void of the
        discard *is* the restore — one recorded operation, one transaction. Voiding a
        *partial* discard changes no spool state.

        **Nothing stores which kind a `DISCARD` was**, so the discriminator is derived, and
        this is the derivation: a whole-spool discard is the entry that retired the spool,
        and after it no use case appends to that spool again — every one of them refuses a
        discarded spool. So it is a `DISCARD`, on a discarded spool, with nothing after it.
        A partial discard *followed* by the whole-spool one is therefore not last and is
        correctly left alone; the reverse order cannot occur.

        (A whole-spool discard executed at zero balance wrote no movement at all —
        `adjust_spool.py` returns early — so there is nothing to void and such a spool
        cannot come back this way. Known, accepted, rare: the spool held nothing.)
        """
        if movement.type is not MovementType.DISCARD or not spool.is_discarded:
            return False
        history = await self.movements.list_for_spool(spool.id)
        return bool(history) and history[-1].id == movement.id


@dataclass(frozen=True, slots=True)
class RestoreMovement:
    """UC-15 — the symmetric question: *deduct X g from [spool] again?*"""

    spools: SpoolRepository
    movements: MovementRepository
    voids: MovementVoidRepository
    clock: Clock
    events: EventPublisher
    uow: UnitOfWork
    anomalies: AnomalyDetector = field(default_factory=AnomalyDetector)

    async def execute(self, movement_id: MovementId) -> Grams:
        """Returns the grams deducted again. One unit of work; events after the commit."""
        to_publish: list[DomainEvent] = []

        async with self.uow:
            chapter = await self.voids.get(movement_id)
            if chapter is None:
                msg = f"movement {movement_id} was never deleted, so there is nothing to restore"
                raise MovementNotVoidedError(msg)
            if not chapter.is_open:
                msg = f"movement {movement_id} has already been restored"
                raise MovementNotVoidedError(msg)
            if not chapter.had_restitution:
                msg = (
                    f"movement {movement_id} was deleted without restitution: nothing was "
                    f"returned, so deducting it again would charge the same grams twice. "
                    f"Record an adjustment instead — the deletion's reason says why"
                )
                raise VoidNotReinstatableError(msg)

            movement = await self.movements.get(movement_id)
            if movement is None:  # pragma: no cover - the void row's foreign key backs this
                raise MovementNotFoundError(movement_id)
            spool = await self.spools.get(movement.spool_id)
            if spool is None:  # pragma: no cover - the movement's foreign key backs this
                raise SpoolNotFoundError(movement.spool_id)
            _guard_in_inventory(spool)

            now = self.clock.now()
            reinstatement = record(
                spool_id=movement.spool_id,
                type=MovementType.REINSTATEMENT,
                # Equal to the original: same sign, same magnitude. Restoring a deletion
                # puts back exactly what the deletion took out.
                amount=movement.amount,
                source=MovementSource.USER_CONFIRMED,
                occurred_at=now,
                note="Restored from the trash",
                job_id=movement.job_id,
                review_id=movement.review_id,
                reinstates_movement_id=movement.id,
            )
            await self.movements.append(reinstatement)
            await self.voids.record_reinstatement(movement.id, reinstatement.id, now)
            new_balance = balance(await self.movements.list_for_spool(movement.spool_id))

        # Published after the commit — never for a write that could still roll back.
        to_publish.append(
            MovementRecorded(
                spool_id=movement.spool_id,
                movement_type=MovementType.REINSTATEMENT,
                amount=movement.amount,
                new_balance=new_balance,
            )
        )
        to_publish.append(
            MovementReinstated(
                movement_id=movement.id, spool_id=movement.spool_id, deducted=movement.amount
            )
        )
        for anomaly in self.anomalies.inspect(
            spool_id=spool.id, balance=new_balance, location=spool.location
        ):
            to_publish.append(AnomalyDetected(anomaly=anomaly))

        for event in to_publish:
            await self.events.publish(event)
        return movement.amount


def _guard_in_inventory(spool: Spool) -> None:
    """The symmetric rule to voiding: an entry only comes back to a spool that is here."""
    if spool.is_deleted:
        msg = (
            f"spool {spool.display_name} was deleted — restore the spool first, and its "
            f"history comes back with it"
        )
        raise SpoolDeletedError(msg)
    if spool.is_discarded:
        msg = (
            f"spool {spool.display_name} was discarded, so deducting from it again would "
            f"change a balance nobody can see"
        )
        raise SpoolDiscardedError(msg)


def _required_reason(reason: str | None) -> str:
    stated = (reason or "").strip()
    if not stated:
        msg = (
            "deleting an entry without restitution needs a reason: the record has to say "
            "why nothing came back, or it reads as a bug six months later"
        )
        raise InvalidValueError(msg)
    return stated


def _reversal_note(reason: str | None) -> str:
    stated = (reason or "").strip()
    headline = "Deleted from history"
    return f"{headline} · {stated}" if stated else headline
