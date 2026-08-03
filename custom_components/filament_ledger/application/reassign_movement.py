"""UC-13 · ReassignMovement — move a charge to the spool that actually fed the print.

The wrong spool gets charged in exactly the ways UC-05/UC-06 anticipate: a review resolved
against the wrong slot assignment, a manual mount that lagged reality. The remedy is not an
edit — nothing in this ledger is edited — but a **compensating pair**: a credit on the
wrongly charged spool and an equal debit on the right one, both naming the charge they
correct (docs/adr/0007, docs/14 §14.3).

The original entry is untouched, and both legs inherit its `job_id` and `review_id`, so
per-print accounting follows the material. That inheritance is what makes cost-per-print
come out right later with no special case.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.error import (
    InvalidValueError,
    MovementAlreadyVoidedError,
    MovementNotReassignableError,
    SpoolDeletedError,
    SpoolDiscardedError,
)
from ..domain.event import (
    AnomalyDetected,
    DomainEvent,
    EventPublisher,
    MovementReassigned,
    MovementRecorded,
)
from ..domain.model.movement import record
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
from ..domain.value.identifiers import MovementId, SpoolId
from ..domain.value.movement_type import MovementSource, MovementType
from .errors import MovementNotFoundError, SpoolNotFoundError


@dataclass(frozen=True, slots=True)
class ReassignMovementCommand:
    """The note is **optional**, unlike UC-10's mandatory reason, and the difference is
    principled: an adjustment without a reason is inexplicable, but a reassignment explains
    itself structurally — the link names the entry it corrects and the pair names both
    spools (docs/14 §14.3)."""

    movement_id: MovementId
    to_spool_id: SpoolId
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ReassignMovement:
    spools: SpoolRepository
    movements: MovementRepository
    voids: MovementVoidRepository
    clock: Clock
    events: EventPublisher
    uow: UnitOfWork
    anomalies: AnomalyDetector = field(default_factory=AnomalyDetector)

    async def execute(self, command: ReassignMovementCommand) -> Grams:
        """Returns the magnitude that moved. One unit of work; events after the commit."""
        to_publish: list[DomainEvent] = []

        # Both legs land together or neither does. A crash between them would leave a
        # spool credited for grams no other spool was charged for — the ledger inventing
        # filament, which is the one failure this design exists to make impossible.
        async with self.uow:
            movement = await self.movements.get(command.movement_id)
            if movement is None:
                raise MovementNotFoundError(command.movement_id)
            self._guard_is_a_charge(movement.type, movement.amount)
            await self._guard_not_voided(command.movement_id)

            source = await self.spools.get(movement.spool_id)
            if source is None:  # pragma: no cover - the movement's foreign key backs this
                raise SpoolNotFoundError(movement.spool_id)
            target = await self.spools.get(command.to_spool_id)
            if target is None:
                raise SpoolNotFoundError(command.to_spool_id)
            # Source first, then target, and inside each one deleted before discarded —
            # the order docs/14 §14.4.3 already names when both facts are somehow set. It
            # is stated rather than left to evaluation order because the message *is* the
            # remedy: the user has to be told which spool to go and fix, and "whichever
            # branch ran first" is not an answer they can act on. Both run before either
            # leg is appended, so a refusal writes nothing.
            self._guard_source(source)
            self._guard_target(source, target)

            moved = abs(movement.amount)
            now = self.clock.now()
            # One type for both legs, distinguished by sign: the pair is one correction,
            # and splitting it into a credit type and a debit type would make every query
            # that asks "was this a reassignment?" ask twice (docs/14 §14.3).
            for spool, amount, counterpart in (
                (source, moved, target),
                (target, -moved, source),
            ):
                await self.movements.append(
                    record(
                        spool_id=spool.id,
                        type=MovementType.REASSIGNMENT,
                        amount=amount,
                        source=MovementSource.USER_CONFIRMED,
                        occurred_at=now,
                        note=self._note(amount, counterpart, command.note),
                        # Inherited, both of them: per-print accounting follows the
                        # material. Without this a reassigned charge would leave its job
                        # short and credit no other, and cost-per-print would need a
                        # special case for every correction ever made.
                        job_id=movement.job_id,
                        review_id=movement.review_id,
                        reassigns_movement_id=movement.id,
                    )
                )

            balances = {
                spool.id: balance(await self.movements.list_for_spool(spool.id))
                for spool in (source, target)
            }

        # Published after the commit — never for a write that could still roll back. The
        # legs first, in the order they were written, then the correction they compose:
        # a listener that sums `MovementRecorded` sees a balanced pair before it is told
        # what the pair was for.
        for spool, amount in ((source, moved), (target, -moved)):
            to_publish.append(
                MovementRecorded(
                    spool_id=spool.id,
                    movement_type=MovementType.REASSIGNMENT,
                    amount=amount,
                    new_balance=balances[spool.id],
                )
            )
        to_publish.append(
            MovementReassigned(
                movement_id=movement.id,
                from_spool_id=source.id,
                to_spool_id=target.id,
                amount=moved,
            )
        )
        # UC-06 steps 7-8, on both spools. Confidence needs no explicit step — it is
        # derived, and the query layer already drops open void chapters before evaluating
        # it — but a debit large enough to drive the target negative is exactly what the
        # anomaly detector exists to announce.
        for spool in (source, target):
            for anomaly in self.anomalies.inspect(
                spool_id=spool.id, balance=balances[spool.id], location=spool.location
            ):
                to_publish.append(AnomalyDetected(anomaly=anomaly))

        for event in to_publish:
            await self.events.publish(event)
        return moved

    @staticmethod
    def _guard_is_a_charge(movement_type: MovementType, amount: Grams) -> None:
        """Only a charge can move. Read off the **sign**, not off the type alone.

        For every fixed-direction type the two readings agree, because `Movement` refuses
        an amount its type does not permit. They part company on the correction types,
        which are all direction-`EITHER` — and there the sign is the only honest answer:
        docs/14 §14.3 calls a reassignment's *debit leg* reassignable again, and a rule
        that consulted `REASSIGNMENT.direction` alone would refuse the chain the same
        section calls legal and honest.
        """
        if not amount.is_negative:
            msg = (
                f"{movement_type} of {amount.as_decimal} g added filament — there is no "
                f"charge here to move to another spool"
            )
            raise MovementNotReassignableError(msg)

    async def _guard_not_voided(self, movement_id: MovementId) -> None:
        """A voided charge has already been returned; reassigning it would move grams that
        are no longer anywhere. A *closed* chapter is ordinary history again and passes."""
        chapter = await self.voids.get(movement_id)
        if chapter is not None and chapter.is_open:
            msg = (
                f"movement {movement_id} was deleted from the history — its grams were "
                f"already returned, so there is nothing left to move"
            )
            raise MovementAlreadyVoidedError(msg)

    @staticmethod
    def _guard_source(source: Spool) -> None:
        """The **credit** leg lands here, so the source has to be able to hold a movement.

        Only the target was ever asked this, and the omission is reachable rather than
        theoretical: the global History shows a discarded spool's rows by design (docs/14
        §14.4.5), so the ⇄ on one of them is a click away. Two things break if the credit
        goes unguarded. A retired spool accepts a movement, which is precisely what
        `SpoolDiscardedError` says cannot happen. And a `REASSIGNMENT` landing after a
        whole-spool `DISCARD` makes that discard no longer *the entry nothing follows* —
        the derivation `VoidMovement._is_whole_spool_discard` reads the history with
        (docs/14 §14.4.1) — so voiding it would quietly stop bringing the spool back.

        There is no honest downgrade to offer the way the X has one. A reassignment is a
        pair; half a pair is filament invented on one spool or lost on the other. So the
        only answer is the route back in, which is what these messages name.
        """
        if source.is_deleted:
            msg = (
                f"spool {source.display_name} was deleted — restore it from the trash "
                f"first, and the charge comes back with it"
            )
            raise SpoolDeletedError(msg)
        if source.is_discarded:
            msg = (
                f"spool {source.display_name} was discarded, so the grams this correction "
                f"returns would land where nobody can see them — delete its whole-spool "
                f"discard entry first, which is what brings it back"
            )
            raise SpoolDiscardedError(msg)

    @staticmethod
    def _guard_target(source: Spool, target: Spool) -> None:
        if target.is_deleted:
            msg = (
                f"spool {target.display_name} was deleted — restore it from the trash "
                f"before charging it"
            )
            raise SpoolDeletedError(msg)
        if target.is_discarded:
            msg = f"spool {target.display_name} was discarded and cannot be charged"
            raise SpoolDiscardedError(msg)
        if target.id == source.id:
            msg = (
                f"spool {target.display_name} is already the one charged; a reassignment "
                f"to itself would write two entries that cancel out and explain nothing"
            )
            raise InvalidValueError(msg)

    @staticmethod
    def _note(amount: Grams, counterpart: Spool, note: str | None) -> str:
        """Each leg names its counterpart, so a history row explains itself without a
        second query — which is also what lets the global table render the pair honestly
        while showing one row at a time."""
        # The credit leg is the positive one, and it sits on the spool the charge left.
        direction = "to" if amount.is_positive else "from"
        stated = (note or "").strip()
        headline = f"Reassigned {direction} {counterpart.display_name}"
        return f"{headline} · {stated}" if stated else headline
