"""Void, restore and reassign on real SQLite — docs/14 §14.3-§14.4, docs/adr/0007.

The claim under test is one sentence: **a correction adds history, it never subtracts
any.** So every scenario here checks two things — that the grams end up where the modal
said they would, and that the entry being corrected is byte-for-byte what it was before.
The second half is what makes the first trustworthy, and it is asserted against the
database rather than against the read model, because the read model is not where the
guarantee lives.
"""

from __future__ import annotations

import sqlite3

import pytest

from custom_components.filament_ledger.application.adjust_spool import (
    AdjustSpoolCommand,
    DiscardFilamentCommand,
    DiscardMode,
)
from custom_components.filament_ledger.application.errors import MovementNotFoundError
from custom_components.filament_ledger.application.reassign_movement import (
    ReassignMovementCommand,
)
from custom_components.filament_ledger.application.reconcile_spool import ReconcileSpoolCommand
from custom_components.filament_ledger.application.register_spool import RegisterSpoolCommand
from custom_components.filament_ledger.application.review_queue import (
    ApproveReviewCommand,
    OpenPendingReviewCommand,
)
from custom_components.filament_ledger.application.void_movement import VoidMovementCommand
from custom_components.filament_ledger.domain.error import (
    InvalidValueError,
    MovementAlreadyVoidedError,
    MovementNotReassignableError,
    MovementNotVoidableError,
    MovementNotVoidedError,
    RestitutionUnavailableError,
    SpoolDeletedError,
    SpoolDiscardedError,
    VoidNotReinstatableError,
)
from custom_components.filament_ledger.domain.event import (
    AnomalyDetected,
    MovementReassigned,
    MovementRecorded,
    MovementReinstated,
    MovementVoided,
    SpoolRestored,
)
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.confidence import Confidence
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    MovementId,
    PrintJobId,
    SlotIndex,
    SpoolId,
)
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.movement_type import MovementType
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.domain.value.review import ReviewReason
from custom_components.filament_ledger.domain.value.spool_state import SpoolState

from .conftest import EPOCH, Ledger

SLOT_1 = SlotIndex(1)

MOVEMENT_COLUMNS = (
    "id, spool_id, type, amount_mg, source, occurred_at, recorded_at, job_id, review_id, "
    "note, reassigns_movement_id, reinstates_movement_id"
)


async def a_spool(ledger: Ledger, **overrides: object) -> SpoolId:
    settings: dict[str, object] = {
        "material": Material.of(MaterialKind.PLA),
        "colour": Colour.parse("000000"),
        "opening_weight": Grams.of(1000),
        "core_weight": Grams.of(250),
        "vendor": "Bambu Lab",
    } | overrides
    return await ledger.use_cases.register_spool.execute(
        RegisterSpoolCommand(**settings)  # type: ignore[arg-type]
    )


async def a_print_charge(ledger: Ledger, spool_id: SpoolId, grams: str = "84.1") -> MovementId:
    """UC-04's automatic deduction — the row the owner's X points at most often.

    Driven through the real use case so the movement carries a real `job_id`, which is
    what the inheritance assertions are about.
    """
    await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)
    await ledger.use_cases.record_print_consumption.execute(
        PrintJob(
            id=PrintJobId("job-1"),
            name="vase_final.gcode.3mf",
            state=PrintJobState.FINISHED,
            started_at=ledger.clock.now(),
            ended_at=ledger.clock.now(),
            reported_usage={SLOT_1: Grams.of(grams)},
        )
    )
    return await newest_of_type(ledger, MovementType.PRINT_CONSUMPTION)


async def newest_of_type(ledger: Ledger, kind: MovementType) -> MovementId:
    row = await ledger.database.fetch_one(
        "SELECT id FROM movement WHERE type = ? ORDER BY rowid DESC LIMIT 1", (kind.value,)
    )
    assert row is not None, f"no {kind} movement was written"
    return MovementId(row["id"])


async def movement_row(ledger: Ledger, movement_id: MovementId) -> tuple[object, ...]:
    row = await ledger.database.fetch_one(
        f"SELECT {MOVEMENT_COLUMNS} FROM movement WHERE id = ?", (movement_id,)
    )
    assert row is not None
    return tuple(row)


async def void_rows(ledger: Ledger) -> list[dict[str, object]]:
    rows = await ledger.database.fetch_all(
        "SELECT movement_id, reason, reversal_movement_id, reinstated_at, "
        "reinstatement_movement_id FROM movement_void ORDER BY rowid"
    )
    return [dict(row) for row in rows]


async def reassignment_rows(ledger: Ledger) -> list[tuple[object, ...]]:
    rows = await ledger.database.fetch_all(
        "SELECT spool_id, amount_mg FROM movement WHERE type = 'REASSIGNMENT' ORDER BY rowid"
    )
    return [tuple(row) for row in rows]


async def balance_of(ledger: Ledger, spool_id: SpoolId) -> Grams:
    return (await ledger.use_cases.queries.detail(spool_id)).summary.balance


async def types_on(ledger: Ledger, spool_id: SpoolId) -> list[str]:
    rows = await ledger.database.fetch_all(
        "SELECT type FROM movement WHERE spool_id = ? ORDER BY occurred_at, rowid", (spool_id,)
    )
    return [str(row["type"]) for row in rows]


class TestVoidingAnEntry:
    async def test_it_returns_the_grams_and_leaves_the_original_untouched(
        self, ledger: Ledger
    ) -> None:
        """Criterion 1. The whole design in one scenario: nothing is edited, nothing is
        deleted, and the balance is back where it was because a *new* row says so."""
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)
        before = await movement_row(ledger, charge)
        assert await balance_of(ledger, spool_id) == Grams.of("915.9")

        returned = await ledger.use_cases.void_movement.execute(
            VoidMovementCommand(movement_id=charge, reason="wrong spool was loaded")
        )

        assert returned == Grams.of("84.1")
        assert await balance_of(ledger, spool_id) == Grams.of(1000)
        # Byte-identical, read straight out of SQLite rather than through the model.
        assert await movement_row(ledger, charge) == before

        reversal_id = await newest_of_type(ledger, MovementType.VOID_REVERSAL)
        reversal = await ledger.database.fetch_one(
            "SELECT amount_mg, source, job_id FROM movement WHERE id = ?", (reversal_id,)
        )
        assert reversal is not None
        # The exact negation, user-confirmed, and inheriting the job so per-print
        # accounting nets to zero with no special case.
        assert reversal["amount_mg"] == 84_100
        assert reversal["source"] == "USER_CONFIRMED"
        assert reversal["job_id"] == "job-1"

        assert await void_rows(ledger) == [
            {
                "movement_id": charge,
                "reason": "wrong spool was loaded",
                "reversal_movement_id": reversal_id,
                "reinstated_at": None,
                "reinstatement_movement_id": None,
            }
        ]

    async def test_voiding_an_increase_removes_those_grams_again(self, ledger: Ledger) -> None:
        """`VOID_REVERSAL` is direction-`EITHER` for exactly this: a +6.2 g reconciliation
        voided must produce −6.2 g, or the reversal would not be a reversal."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.reconcile_spool.execute(
            ReconcileSpoolCommand(spool_id=spool_id, measured=Grams.of("1256.2"))
        )
        assert await balance_of(ledger, spool_id) == Grams.of("1006.2")

        returned = await ledger.use_cases.void_movement.execute(
            VoidMovementCommand(
                movement_id=await newest_of_type(ledger, MovementType.RECONCILIATION)
            )
        )

        assert returned == Grams.of("-6.2")
        assert await balance_of(ledger, spool_id) == Grams.of(1000)

    async def test_the_events_land_after_the_commit(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)
        ledger.events.published.clear()

        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=charge))

        recorded = ledger.events.of(MovementRecorded)
        assert [(e.movement_type, e.amount, e.new_balance) for e in recorded] == [
            (MovementType.VOID_REVERSAL, Grams.of("84.1"), Grams.of(1000))
        ]
        (voided,) = ledger.events.of(MovementVoided)
        assert (voided.movement_id, voided.spool_id, voided.returned) == (
            charge,
            spool_id,
            Grams.of("84.1"),
        )

    @pytest.mark.parametrize(
        ("kind", "fragment"),
        [
            (MovementType.OPENING_BALANCE, "balance with no origin"),
            (MovementType.VOID_REVERSAL, "restore the entry from the trash"),
        ],
    )
    async def test_two_types_refuse_each_for_its_own_reason(
        self, ledger: Ledger, kind: MovementType, fragment: str
    ) -> None:
        """Criterion 5. The two refusals are not one rule twice: an opening balance is the
        origin of a number, and a reversal is corrected by its own flow."""
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)
        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=charge))

        target = await newest_of_type(ledger, kind)
        with pytest.raises(MovementNotVoidableError, match=fragment):
            await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=target))

    async def test_an_entry_is_voided_at_most_once(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)
        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=charge))

        with pytest.raises(MovementAlreadyVoidedError, match="already been deleted once"):
            await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=charge))

    async def test_an_unknown_id_is_an_orchestration_failure(self, ledger: Ledger) -> None:
        with pytest.raises(MovementNotFoundError):
            await ledger.use_cases.void_movement.execute(
                VoidMovementCommand(movement_id=MovementId("nobody"))
            )


class TestVoidingWithoutRestitution:
    async def test_a_retired_spool_refuses_restitution_and_offers_the_route_back(
        self, ledger: Ledger
    ) -> None:
        """Criterion 6, first half. Grams only return to a spool that is in inventory; a
        reversal landing on a deleted one would be a balance change nobody can see."""
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)
        await ledger.use_cases.unmount_spool.execute(spool_id)
        await ledger.use_cases.delete_spool.execute(spool_id)

        with pytest.raises(RestitutionUnavailableError, match="restore it from the trash first"):
            await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=charge))

    async def test_it_writes_a_null_reversal_and_leaves_the_balance_alone(
        self, ledger: Ledger
    ) -> None:
        """Criterion 6, second half. The entry still sums into its spool's balance —
        nothing reversed it; it merely leaves the default views."""
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)
        await ledger.use_cases.unmount_spool.execute(spool_id)
        await ledger.use_cases.delete_spool.execute(spool_id)

        returned = await ledger.use_cases.void_movement.execute(
            VoidMovementCommand(
                movement_id=charge,
                reason="the spool never existed; the print ran off another reel",
                without_restitution=True,
            )
        )

        assert returned is None
        assert await balance_of(ledger, spool_id) == Grams.of("915.9")
        assert await types_on(ledger, spool_id) == ["OPENING_BALANCE", "PRINT_CONSUMPTION"]
        (chapter,) = await void_rows(ledger)
        assert chapter["reversal_movement_id"] is None
        assert chapter["reason"] == "the spool never existed; the print ran off another reel"

    async def test_the_reason_is_mandatory(self, ledger: Ledger) -> None:
        """A null reversal with no explanation reads as a bug six months later."""
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)
        await ledger.use_cases.unmount_spool.execute(spool_id)
        await ledger.use_cases.delete_spool.execute(spool_id)

        with pytest.raises(InvalidValueError, match="needs a reason"):
            await ledger.use_cases.void_movement.execute(
                VoidMovementCommand(movement_id=charge, reason="   ", without_restitution=True)
            )
        assert await void_rows(ledger) == []

    async def test_it_is_terminal(self, ledger: Ledger) -> None:
        """Criterion 7. Nothing came back, so "deduct it again" would double-charge."""
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)
        await ledger.use_cases.unmount_spool.execute(spool_id)
        await ledger.use_cases.delete_spool.execute(spool_id)
        await ledger.use_cases.void_movement.execute(
            VoidMovementCommand(
                movement_id=charge, reason="ran off another reel", without_restitution=True
            )
        )
        await ledger.use_cases.restore_spool.execute(spool_id)

        with pytest.raises(VoidNotReinstatableError, match="charge the same grams twice"):
            await ledger.use_cases.restore_movement.execute(charge)


class TestRestoringAnEntry:
    async def test_it_deducts_again_and_closes_the_chapter(self, ledger: Ledger) -> None:
        """Criterion 3. The entry comes back as one more row, not as a row that flickered
        in and out of existence."""
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)
        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=charge))
        ledger.events.published.clear()

        deducted = await ledger.use_cases.restore_movement.execute(charge)

        assert deducted == Grams.of("-84.1")
        assert await balance_of(ledger, spool_id) == Grams.of("915.9")
        reinstatement = await newest_of_type(ledger, MovementType.REINSTATEMENT)
        row = await ledger.database.fetch_one(
            "SELECT amount_mg, job_id, reinstates_movement_id FROM movement WHERE id = ?",
            (reinstatement,),
        )
        assert row is not None
        assert (row["amount_mg"], row["job_id"], row["reinstates_movement_id"]) == (
            -84_100,
            "job-1",
            charge,
        )

        (chapter,) = await void_rows(ledger)
        assert chapter["reinstatement_movement_id"] == reinstatement
        assert chapter["reinstated_at"] is not None

        (event,) = ledger.events.of(MovementReinstated)
        assert (event.movement_id, event.deducted) == (charge, Grams.of("-84.1"))

    async def test_void_restore_void_again_opens_a_second_independent_chapter(
        self, ledger: Ledger
    ) -> None:
        """Criterion 4. Chains re-void the *reinstatement*, never the original, so
        `movement_void`'s primary key never has to bend."""
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)
        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=charge))
        await ledger.use_cases.restore_movement.execute(charge)
        reinstatement = await newest_of_type(ledger, MovementType.REINSTATEMENT)

        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=reinstatement))

        chapters = await void_rows(ledger)
        assert [c["movement_id"] for c in chapters] == [charge, reinstatement]
        # The first chapter is closed, the second is open — two facts, not one flag
        # rewritten twice.
        assert chapters[0]["reinstatement_movement_id"] == reinstatement
        assert chapters[1]["reinstatement_movement_id"] is None
        assert await balance_of(ledger, spool_id) == Grams.of(1000)
        assert await types_on(ledger, spool_id) == [
            "OPENING_BALANCE",
            "PRINT_CONSUMPTION",
            "VOID_REVERSAL",
            "REINSTATEMENT",
            "VOID_REVERSAL",
        ]

    async def test_an_entry_that_was_never_voided_cannot_be_restored(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)

        with pytest.raises(MovementNotVoidedError, match="never deleted"):
            await ledger.use_cases.restore_movement.execute(charge)

    async def test_restoring_twice_is_refused(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)
        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=charge))
        await ledger.use_cases.restore_movement.execute(charge)

        with pytest.raises(MovementNotVoidedError, match="already been restored"):
            await ledger.use_cases.restore_movement.execute(charge)

    async def test_a_deleted_spool_must_be_restored_first(self, ledger: Ledger) -> None:
        """The symmetric rule to voiding: an entry only comes back to a spool that is
        here."""
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)
        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=charge))
        await ledger.use_cases.unmount_spool.execute(spool_id)
        await ledger.use_cases.delete_spool.execute(spool_id)

        with pytest.raises(SpoolDeletedError, match="restore the spool first"):
            await ledger.use_cases.restore_movement.execute(charge)

        await ledger.use_cases.restore_spool.execute(spool_id)
        assert await ledger.use_cases.restore_movement.execute(charge) == Grams.of("-84.1")


class TestVoidingADiscard:
    async def test_voiding_a_whole_spool_discard_is_the_un_discard(self, ledger: Ledger) -> None:
        """Criterion 8, first half. The restitution returns the entire balance, and
        leaving the spool DISCARDED would strand those grams outside inventory — so the
        void of the discard *is* the restore, in one transaction."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=spool_id, mode=DiscardMode.WHOLE_SPOOL, reason="water damage"
            )
        )
        assert (await ledger.use_cases.queries.detail(spool_id)).summary.state is (
            SpoolState.DISCARDED
        )
        ledger.events.published.clear()

        await ledger.use_cases.void_movement.execute(
            VoidMovementCommand(movement_id=await newest_of_type(ledger, MovementType.DISCARD))
        )

        summary = (await ledger.use_cases.queries.detail(spool_id)).summary
        assert summary.balance == Grams.of(1000)
        # Back in inventory, and ACTIVE rather than SEALED: three entries happened to it,
        # and the history says so. A spool that came back does not come back untouched.
        assert summary.state is SpoolState.ACTIVE
        (restored,) = ledger.events.of(SpoolRestored)
        assert restored.spool_id == spool_id

    async def test_voiding_a_partial_discard_changes_no_spool_state(self, ledger: Ledger) -> None:
        """Criterion 8, second half."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=spool_id,
                mode=DiscardMode.PARTIAL,
                amount=Grams.of(40),
                reason="tangled section",
            )
        )

        await ledger.use_cases.void_movement.execute(
            VoidMovementCommand(movement_id=await newest_of_type(ledger, MovementType.DISCARD))
        )

        summary = (await ledger.use_cases.queries.detail(spool_id)).summary
        assert summary.balance == Grams.of(1000)
        assert summary.state is SpoolState.ACTIVE
        assert ledger.events.of(SpoolRestored) == []

    async def test_a_partial_discard_before_a_whole_one_is_left_alone(self, ledger: Ledger) -> None:
        """The discriminator is "the DISCARD nothing follows", and this is the case that
        would break a naive one: two DISCARD rows, only the last of which retired the
        spool."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=spool_id, mode=DiscardMode.PARTIAL, amount=Grams.of(40), reason="tangle"
            )
        )
        partial = await newest_of_type(ledger, MovementType.DISCARD)
        ledger.clock.advance(hours=1)
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=spool_id, mode=DiscardMode.WHOLE_SPOOL, reason="water damage"
            )
        )

        # Voiding the *partial* one on a discarded spool has nowhere to return the grams.
        with pytest.raises(RestitutionUnavailableError, match="whole-spool discard entry"):
            await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=partial))

        summary = (await ledger.use_cases.queries.detail(spool_id)).summary
        assert summary.state is SpoolState.DISCARDED

    async def test_no_reassignment_can_get_behind_the_whole_spool_discard(
        self, ledger: Ledger
    ) -> None:
        """The derivation is "the DISCARD nothing follows", and a reassignment's *credit*
        leg was the one entry that could still have landed behind it — the global History
        shows a discarded spool's rows by design, so the ⇄ was a click away. This is the
        inference itself under test: the source guard refuses, nothing follows the
        DISCARD, and voiding it still brings the spool back."""
        discarded = await a_spool(ledger, label="Water damaged")
        other = await a_spool(ledger, label="Still here")
        charge = await a_print_charge(ledger, discarded)
        await ledger.use_cases.unmount_spool.execute(discarded)
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=discarded, mode=DiscardMode.WHOLE_SPOOL, reason="water damage"
            )
        )
        discard = await newest_of_type(ledger, MovementType.DISCARD)

        with pytest.raises(SpoolDiscardedError):
            await ledger.use_cases.reassign_movement.execute(
                ReassignMovementCommand(movement_id=charge, to_spool_id=other)
            )

        assert await types_on(ledger, discarded) == [
            "OPENING_BALANCE",
            "PRINT_CONSUMPTION",
            "DISCARD",
        ]
        ledger.events.published.clear()

        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=discard))

        summary = (await ledger.use_cases.queries.detail(discarded)).summary
        assert summary.state is SpoolState.ACTIVE
        assert summary.balance == Grams.of("915.9")
        (restored,) = ledger.events.of(SpoolRestored)
        assert restored.spool_id == discarded


class TestReassigningACharge:
    async def test_the_compensating_pair_moves_the_charge_and_the_job_with_it(
        self, ledger: Ledger
    ) -> None:
        """Criteria 1-3. The credit and the debit are one correction, and both carry the
        job so cost-per-print follows the material."""
        wrong = await a_spool(ledger, label="Wrongly charged")
        right = await a_spool(ledger, label="Actually printed")
        bystander = await a_spool(ledger, label="Untouched")
        charge = await a_print_charge(ledger, wrong)
        before = await movement_row(ledger, charge)

        moved = await ledger.use_cases.reassign_movement.execute(
            ReassignMovementCommand(movement_id=charge, to_spool_id=right)
        )

        assert moved == Grams.of("84.1")
        assert await balance_of(ledger, wrong) == Grams.of(1000)
        assert await balance_of(ledger, right) == Grams.of("915.9")
        assert await balance_of(ledger, bystander) == Grams.of(1000)
        # Criterion 2, by direct SQL comparison.
        assert await movement_row(ledger, charge) == before

        legs = await ledger.database.fetch_all(
            "SELECT spool_id, amount_mg, job_id, reassigns_movement_id FROM movement "
            "WHERE type = 'REASSIGNMENT' ORDER BY rowid"
        )
        assert [tuple(row) for row in legs] == [
            (wrong, 84_100, "job-1", charge),
            (right, -84_100, "job-1", charge),
        ]

    async def test_a_named_amount_moves_only_that_part_and_leaves_the_rest(
        self, ledger: Ledger
    ) -> None:
        """The review queue's split, reached after the charge has landed: the spool that
        fed the first half keeps what it really gave, and the rest moves."""
        emptied = await a_spool(ledger, label="Emptied mid-print")
        replacement = await a_spool(ledger, label="Loaded in its place")
        charge = await a_print_charge(ledger, emptied)

        moved = await ledger.use_cases.reassign_movement.execute(
            ReassignMovementCommand(
                movement_id=charge, to_spool_id=replacement, amount=Grams.of(60)
            )
        )

        assert moved == Grams.of(60)
        # 84.1 charged, 60 moved: the source is left carrying the 24.1 g it really gave.
        assert await balance_of(ledger, emptied) == Grams.of("975.9")
        assert await balance_of(ledger, replacement) == Grams.of(940)

        legs = await ledger.database.fetch_all(
            "SELECT spool_id, amount_mg, job_id, review_id, reassigns_movement_id FROM movement "
            "WHERE type = 'REASSIGNMENT' ORDER BY rowid"
        )
        # Every link inherited exactly as the whole-charge path inherits them.
        assert [tuple(row) for row in legs] == [
            (emptied, 60_000, "job-1", None, charge),
            (replacement, -60_000, "job-1", None, charge),
        ]

    async def test_the_whole_charge_may_be_named_explicitly(self, ledger: Ledger) -> None:
        """The boundary is inclusive: naming all of it is the ordinary reassignment, and
        the panel sends the whole figure whenever the user leaves the field alone."""
        wrong = await a_spool(ledger)
        right = await a_spool(ledger)
        charge = await a_print_charge(ledger, wrong)

        moved = await ledger.use_cases.reassign_movement.execute(
            ReassignMovementCommand(movement_id=charge, to_spool_id=right, amount=Grams.of("84.1"))
        )

        assert moved == Grams.of("84.1")
        assert await balance_of(ledger, wrong) == Grams.of(1000)

    async def test_more_than_the_charge_holds_is_refused_and_writes_nothing(
        self, ledger: Ledger
    ) -> None:
        """Moving grams the charge never held debits the target for material it never
        received — the ledger inventing filament, which is the one impossible failure."""
        wrong = await a_spool(ledger)
        right = await a_spool(ledger)
        charge = await a_print_charge(ledger, wrong)

        with pytest.raises(InvalidValueError, match="cannot give up"):
            await ledger.use_cases.reassign_movement.execute(
                ReassignMovementCommand(
                    movement_id=charge, to_spool_id=right, amount=Grams.of("84.2")
                )
            )

        assert await reassignment_rows(ledger) == []
        assert await balance_of(ledger, wrong) == Grams.of("915.9")
        assert await balance_of(ledger, right) == Grams.of(1000)

    @pytest.mark.parametrize("amount", [Grams.zero(), Grams.of(-5)])
    async def test_a_magnitude_of_nothing_or_less_is_refused(
        self, ledger: Ledger, amount: Grams
    ) -> None:
        """A pair that cancels out explains nothing — the same emptiness a reassignment
        to the source itself is refused for."""
        wrong = await a_spool(ledger)
        right = await a_spool(ledger)
        charge = await a_print_charge(ledger, wrong)

        with pytest.raises(InvalidValueError, match="moves nothing"):
            await ledger.use_cases.reassign_movement.execute(
                ReassignMovementCommand(movement_id=charge, to_spool_id=right, amount=amount)
            )

        assert await reassignment_rows(ledger) == []

    async def test_a_partial_leg_is_reassignable_again_for_part_of_itself(
        self, ledger: Ledger
    ) -> None:
        """Three spools in one tray is unusual and not impossible, and nothing about the
        chain the specification calls legal changes when the magnitudes are partial."""
        first = await a_spool(ledger, label="First")
        second = await a_spool(ledger, label="Second")
        third = await a_spool(ledger, label="Third")
        charge = await a_print_charge(ledger, first)
        await ledger.use_cases.reassign_movement.execute(
            ReassignMovementCommand(movement_id=charge, to_spool_id=second, amount=Grams.of(60))
        )
        debit = await newest_of_type(ledger, MovementType.REASSIGNMENT)

        await ledger.use_cases.reassign_movement.execute(
            ReassignMovementCommand(movement_id=debit, to_spool_id=third, amount=Grams.of(20))
        )

        assert await balance_of(ledger, first) == Grams.of("975.9")
        assert await balance_of(ledger, second) == Grams.of(960)
        assert await balance_of(ledger, third) == Grams.of(980)

    async def test_it_announces_both_legs_and_then_the_correction(self, ledger: Ledger) -> None:
        wrong = await a_spool(ledger)
        right = await a_spool(ledger)
        charge = await a_print_charge(ledger, wrong)
        ledger.events.published.clear()

        await ledger.use_cases.reassign_movement.execute(
            ReassignMovementCommand(movement_id=charge, to_spool_id=right, note="slot 2 really")
        )

        assert [(e.spool_id, e.amount) for e in ledger.events.of(MovementRecorded)] == [
            (wrong, Grams.of("84.1")),
            (right, Grams.of("-84.1")),
        ]
        (reassigned,) = ledger.events.of(MovementReassigned)
        assert (
            reassigned.movement_id,
            reassigned.from_spool_id,
            reassigned.to_spool_id,
            reassigned.amount,
        ) == (charge, wrong, right, Grams.of("84.1"))

    async def test_each_leg_names_its_counterpart(self, ledger: Ledger) -> None:
        """A history row has to explain itself without a second query."""
        wrong = await a_spool(ledger, label="Wrongly charged")
        right = await a_spool(ledger, label="Actually printed")
        charge = await a_print_charge(ledger, wrong)

        await ledger.use_cases.reassign_movement.execute(
            ReassignMovementCommand(movement_id=charge, to_spool_id=right, note="slot 2 really")
        )

        notes = await ledger.database.fetch_all(
            "SELECT note FROM movement WHERE type = 'REASSIGNMENT' ORDER BY rowid"
        )
        assert [row["note"] for row in notes] == [
            "Reassigned to Actually printed · slot 2 really",
            "Reassigned from Wrongly charged · slot 2 really",
        ]

    async def test_a_debit_leg_is_reassignable_again(self, ledger: Ledger) -> None:
        """Criterion 6. `REASSIGNMENT` is direction-`EITHER`, so the rule reads the
        entry's sign — and the chain the specification calls legal stays legal."""
        first = await a_spool(ledger, label="First")
        second = await a_spool(ledger, label="Second")
        third = await a_spool(ledger, label="Third")
        charge = await a_print_charge(ledger, first)
        await ledger.use_cases.reassign_movement.execute(
            ReassignMovementCommand(movement_id=charge, to_spool_id=second)
        )
        debit = MovementId(
            str(
                (
                    await ledger.database.fetch_one(
                        "SELECT id FROM movement WHERE type = 'REASSIGNMENT' AND amount_mg < 0 "
                        "ORDER BY rowid DESC LIMIT 1"
                    )
                )["id"]  # type: ignore[index]
            )
        )

        await ledger.use_cases.reassign_movement.execute(
            ReassignMovementCommand(movement_id=debit, to_spool_id=third)
        )

        assert await balance_of(ledger, first) == Grams.of(1000)
        assert await balance_of(ledger, second) == Grams.of(1000)
        assert await balance_of(ledger, third) == Grams.of("915.9")
        # The chain is recorded link by link: the second pair names the debit leg it
        # corrects, not the print charge two steps back.
        chained = await ledger.database.fetch_all(
            "SELECT reassigns_movement_id FROM movement WHERE type = 'REASSIGNMENT' ORDER BY rowid"
        )
        assert [row["reassigns_movement_id"] for row in chained] == [charge, charge, debit, debit]

    async def test_an_increase_has_no_charge_to_move(self, ledger: Ledger) -> None:
        """Criterion 4, first of three distinct errors."""
        spool_id = await a_spool(ledger)
        other = await a_spool(ledger)
        opening = await newest_of_type(ledger, MovementType.OPENING_BALANCE)

        with pytest.raises(MovementNotReassignableError, match="no charge here to move"):
            await ledger.use_cases.reassign_movement.execute(
                ReassignMovementCommand(movement_id=opening, to_spool_id=spool_id)
            )
        assert await balance_of(ledger, other) == Grams.of(1000)

    async def test_a_voided_charge_has_already_been_returned(self, ledger: Ledger) -> None:
        """Criterion 4, second. Its grams are no longer anywhere to move."""
        wrong = await a_spool(ledger)
        right = await a_spool(ledger)
        charge = await a_print_charge(ledger, wrong)
        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=charge))

        with pytest.raises(MovementAlreadyVoidedError, match="nothing left to move"):
            await ledger.use_cases.reassign_movement.execute(
                ReassignMovementCommand(movement_id=charge, to_spool_id=right)
            )

    async def test_a_restored_charge_is_reassignable_again(self, ledger: Ledger) -> None:
        """A *closed* chapter is ordinary history: the entry is back, so the charge is
        back, so it can be moved."""
        wrong = await a_spool(ledger)
        right = await a_spool(ledger)
        charge = await a_print_charge(ledger, wrong)
        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=charge))
        await ledger.use_cases.restore_movement.execute(charge)

        assert await ledger.use_cases.reassign_movement.execute(
            ReassignMovementCommand(movement_id=charge, to_spool_id=right)
        ) == Grams.of("84.1")

    async def test_a_deleted_target_is_refused(self, ledger: Ledger) -> None:
        """Criterion 4, third."""
        wrong = await a_spool(ledger)
        right = await a_spool(ledger)
        charge = await a_print_charge(ledger, wrong)
        await ledger.use_cases.delete_spool.execute(right)

        with pytest.raises(SpoolDeletedError, match="restore it from the trash"):
            await ledger.use_cases.reassign_movement.execute(
                ReassignMovementCommand(movement_id=charge, to_spool_id=right)
            )

    async def test_a_discarded_target_is_refused(self, ledger: Ledger) -> None:
        wrong = await a_spool(ledger)
        right = await a_spool(ledger)
        charge = await a_print_charge(ledger, wrong)
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=right, mode=DiscardMode.WHOLE_SPOOL, reason="water damage"
            )
        )

        with pytest.raises(SpoolDiscardedError, match="cannot be charged"):
            await ledger.use_cases.reassign_movement.execute(
                ReassignMovementCommand(movement_id=charge, to_spool_id=right)
            )

    async def test_a_discarded_source_is_refused_and_writes_nothing(self, ledger: Ledger) -> None:
        """The credit leg lands on the *source*, so the source is held to the rule the
        target has always been held to. Reachable, not theoretical: the global History
        shows a discarded spool's rows by design (docs/14 §14.4.5), so its ⇄ is a click
        away."""
        discarded = await a_spool(ledger, label="Water damaged")
        right = await a_spool(ledger, label="Actually printed")
        charge = await a_print_charge(ledger, discarded)
        await ledger.use_cases.unmount_spool.execute(discarded)
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=discarded, mode=DiscardMode.WHOLE_SPOOL, reason="water damage"
            )
        )

        with pytest.raises(SpoolDiscardedError, match="whole-spool discard entry first"):
            await ledger.use_cases.reassign_movement.execute(
                ReassignMovementCommand(movement_id=charge, to_spool_id=right)
            )

        # Neither leg landed. A pair that wrote only its debit would be filament invented.
        assert await types_on(ledger, discarded) == [
            "OPENING_BALANCE",
            "PRINT_CONSUMPTION",
            "DISCARD",
        ]
        assert await types_on(ledger, right) == ["OPENING_BALANCE"]
        assert await balance_of(ledger, right) == Grams.of(1000)

    async def test_a_deleted_source_is_refused_and_writes_nothing(self, ledger: Ledger) -> None:
        """The other retirement, and the other route back — a deletion is undone from the
        Trash, so that is what the message names (docs/14 §14.4.3)."""
        retracted = await a_spool(ledger, label="Never really here")
        right = await a_spool(ledger, label="Actually printed")
        charge = await a_print_charge(ledger, retracted)
        await ledger.use_cases.unmount_spool.execute(retracted)
        await ledger.use_cases.delete_spool.execute(retracted)

        with pytest.raises(SpoolDeletedError, match="restore it from the trash first"):
            await ledger.use_cases.reassign_movement.execute(
                ReassignMovementCommand(movement_id=charge, to_spool_id=right)
            )

        assert await types_on(ledger, retracted) == ["OPENING_BALANCE", "PRINT_CONSUMPTION"]
        assert await types_on(ledger, right) == ["OPENING_BALANCE"]

        # And the refusal is a route, not a wall: restored, the charge moves.
        await ledger.use_cases.restore_spool.execute(retracted)
        assert await ledger.use_cases.reassign_movement.execute(
            ReassignMovementCommand(movement_id=charge, to_spool_id=right)
        ) == Grams.of("84.1")

    async def test_reassigning_to_itself_explains_nothing(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)

        with pytest.raises(InvalidValueError, match="already the one charged"):
            await ledger.use_cases.reassign_movement.execute(
                ReassignMovementCommand(movement_id=charge, to_spool_id=spool_id)
            )

    async def test_a_debit_that_drives_the_target_negative_raises_an_anomaly(
        self, ledger: Ledger
    ) -> None:
        """Criterion 5. A reassignment onto a nearly empty spool is exactly the physical
        implausibility the detector exists to announce — and it is announced for *both*
        spools, so the check runs on the pair rather than on the charged one."""
        wrong = await a_spool(ledger, label="Full")
        nearly_empty = await a_spool(ledger, label="Nearly empty", opening_weight=Grams.of(50))
        charge = await a_print_charge(ledger, wrong)
        ledger.events.published.clear()

        await ledger.use_cases.reassign_movement.execute(
            ReassignMovementCommand(movement_id=charge, to_spool_id=nearly_empty)
        )

        anomalies = ledger.events.of(AnomalyDetected)
        assert [event.anomaly.spool_id for event in anomalies] == [nearly_empty]


class TestConfidenceIgnoresOpenChapters:
    async def test_a_voided_estimate_stops_holding_a_spool_at_low(self, ledger: Ledger) -> None:
        """Criterion 13. A voided estimate no longer bears on the balance, so it must not
        go on bearing on how much that balance can be trusted (docs/14 §14.4.5)."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)
        review_id = await ledger.use_cases.open_pending_review.execute(
            OpenPendingReviewCommand(
                job=PrintJob(
                    id=PrintJobId("job-cancelled"),
                    name="bracket_v3.gcode.3mf",
                    state=PrintJobState.CANCELLED,
                    started_at=ledger.clock.now(),
                ),
                reason=ReviewReason.CANCELLED,
                amounts={SLOT_1: Grams.of(70)},
            )
        )
        await ledger.use_cases.approve_review.execute(ApproveReviewCommand(review_id=review_id))
        assert (await ledger.use_cases.queries.detail(spool_id)).summary.confidence is (
            Confidence.LOW
        )

        await ledger.use_cases.void_movement.execute(
            VoidMovementCommand(
                movement_id=await newest_of_type(ledger, MovementType.ESTIMATED_CONSUMPTION)
            )
        )

        summary = (await ledger.use_cases.queries.detail(spool_id)).summary
        assert summary.confidence is Confidence.HIGH
        assert summary.balance == Grams.of(1000)

    async def test_restoring_the_estimate_brings_the_doubt_back(self, ledger: Ledger) -> None:
        """The other direction, because a filter that only ever hides is a filter nobody
        can trust to stop hiding."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SLOT_1)
        review_id = await ledger.use_cases.open_pending_review.execute(
            OpenPendingReviewCommand(
                job=PrintJob(
                    id=PrintJobId("job-cancelled"),
                    name="bracket_v3.gcode.3mf",
                    state=PrintJobState.CANCELLED,
                    started_at=ledger.clock.now(),
                ),
                reason=ReviewReason.CANCELLED,
                amounts={SLOT_1: Grams.of(70)},
            )
        )
        await ledger.use_cases.approve_review.execute(ApproveReviewCommand(review_id=review_id))
        estimate = await newest_of_type(ledger, MovementType.ESTIMATED_CONSUMPTION)
        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=estimate))

        await ledger.use_cases.restore_movement.execute(estimate)

        assert (await ledger.use_cases.queries.detail(spool_id)).summary.confidence is (
            Confidence.LOW
        )


class TestTheTriggersNeverBend:
    async def test_a_voided_row_is_still_immutable(self, ledger: Ledger) -> None:
        """Criterion 14. The whole argument for expressing corrections as new records is
        that the two enforcements of immutability never need an exception — so voiding
        must leave both exactly as strict as they were."""
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)
        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=charge))

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            await ledger.database.execute(
                "UPDATE movement SET note = 'edited' WHERE id = ?", (charge,)
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            await ledger.database.execute("DELETE FROM movement WHERE id = ?", (charge,))

    async def test_the_void_table_refuses_a_second_chapter_for_one_entry(
        self, ledger: Ledger
    ) -> None:
        """The primary key is the rule; the use case only makes it readable."""
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)
        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=charge))

        with pytest.raises(sqlite3.IntegrityError):
            await ledger.database.execute(
                "INSERT INTO movement_void (movement_id, voided_at, reason) VALUES (?, ?, ?)",
                (charge, EPOCH.isoformat(), "by hand"),
            )

    async def test_a_null_reversal_can_never_be_reinstated_at_the_schema_level(
        self, ledger: Ledger
    ) -> None:
        """Migration 0003's second CHECK, exercised: the terminality of a
        without-restitution void is not merely a use-case rule."""
        spool_id = await a_spool(ledger)
        charge = await a_print_charge(ledger, spool_id)

        with pytest.raises(sqlite3.IntegrityError):
            await ledger.database.execute(
                "INSERT INTO movement_void (movement_id, voided_at, reason, "
                "reversal_movement_id, reinstated_at, reinstatement_movement_id) "
                "VALUES (?, ?, ?, NULL, ?, ?)",
                (charge, EPOCH.isoformat(), "nothing came back", EPOCH.isoformat(), charge),
            )


class TestAdjustmentsAreCorrectableToo:
    async def test_a_manual_adjustment_that_removed_filament_is_a_charge(
        self, ledger: Ledger
    ) -> None:
        """`MANUAL_ADJUSTMENT` is direction-`EITHER`, so only its sign can answer whether
        there is a charge to move — and a −100 g adjustment plainly is one."""
        wrong = await a_spool(ledger)
        right = await a_spool(ledger)
        await ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=wrong, amount=Grams.of(-100), reason="lamp shade")
        )
        adjustment = await newest_of_type(ledger, MovementType.MANUAL_ADJUSTMENT)

        await ledger.use_cases.reassign_movement.execute(
            ReassignMovementCommand(movement_id=adjustment, to_spool_id=right)
        )

        assert await balance_of(ledger, wrong) == Grams.of(1000)
        assert await balance_of(ledger, right) == Grams.of(900)

    async def test_a_positive_adjustment_has_nothing_to_reassign(self, ledger: Ledger) -> None:
        wrong = await a_spool(ledger)
        right = await a_spool(ledger)
        await ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=wrong, amount=Grams.of(25), reason="found a coil")
        )
        adjustment = await newest_of_type(ledger, MovementType.MANUAL_ADJUSTMENT)

        with pytest.raises(MovementNotReassignableError, match="added filament"):
            await ledger.use_cases.reassign_movement.execute(
                ReassignMovementCommand(movement_id=adjustment, to_spool_id=right)
            )
