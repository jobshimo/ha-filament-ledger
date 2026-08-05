"""Deleting a spool, restoring it, and the visibility table — docs/14 §14.4.3-§14.4.5.

    "Did you throw it away?" — then it's waste and counts as waste.
    "Was it registered by mistake?" — then it was never really here.

Two answers, two different facts about the world, and the tests below are mostly about
keeping them apart. A discard is a real event: it counts as waste, its movements stay in
the history, and the spool never comes back on its own. A deletion counts as *nothing*,
anywhere — and comes back whole, with its history, because visibility was derived from the
spool's state all along rather than stamped onto forty movement rows.
"""

from __future__ import annotations

import pytest

from custom_components.filament_ledger.application.adjust_spool import (
    AdjustSpoolCommand,
    DiscardFilamentCommand,
    DiscardMode,
)
from custom_components.filament_ledger.application.errors import SpoolNotFoundError
from custom_components.filament_ledger.application.register_spool import RegisterSpoolCommand
from custom_components.filament_ledger.application.review_queue import (
    ApproveReviewCommand,
    OpenPendingReviewCommand,
)
from custom_components.filament_ledger.application.void_movement import VoidMovementCommand
from custom_components.filament_ledger.domain.error import (
    InvalidValueError,
    SpoolDeletedError,
)
from custom_components.filament_ledger.domain.event import SpoolDeleted, SpoolRestored
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.port.repositories import SpoolFilter
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    PrintJobId,
    SpoolId,
    TagUid,
)
from custom_components.filament_ledger.domain.value.location import AmsSlot
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.movement_type import MovementType
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.domain.value.review import ReviewReason
from custom_components.filament_ledger.domain.value.spool_state import SpoolState
from custom_components.filament_ledger.infrastructure.persistence.spool_repository import (
    SqliteSpoolRepository,
)

from .conftest import Ledger, a_tray

TRAY_1 = a_tray(1)


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


async def movement_count(ledger: Ledger) -> int:
    row = await ledger.database.fetch_one("SELECT COUNT(*) AS n FROM movement")
    assert row is not None
    return int(row["n"])


async def stored(ledger: Ledger, spool_id: SpoolId) -> dict[str, object]:
    row = await ledger.database.fetch_one(
        "SELECT location_kind, location_slot, discarded_at, deleted_at FROM spool WHERE id = ?",
        (spool_id,),
    )
    assert row is not None
    return dict(row)


class TestDeletingASpool:
    async def test_it_frees_the_slot_and_writes_no_movement(self, ledger: Ledger) -> None:
        """Criterion 9. Deletion is a location-and-state change; UC-03's separation of
        location change from quantity change extends to it."""
        deleted = await a_spool(ledger, label="Registered twice by mistake")
        replacement = await a_spool(ledger, label="The real one")
        await ledger.use_cases.mount_spool.execute(deleted, TRAY_1)
        before = await movement_count(ledger)

        await ledger.use_cases.delete_spool.execute(deleted)

        assert await movement_count(ledger) == before
        row = await stored(ledger, deleted)
        assert row["deleted_at"] is not None
        assert row["discarded_at"] is None
        assert (row["location_kind"], row["location_slot"]) == ("STORAGE", None)
        # The slot is free *immediately*: the partial unique index learned to ignore
        # deleted spools, so this mount neither displaces a ghost nor collides with one.
        await ledger.use_cases.mount_spool.execute(replacement, TRAY_1)
        assert (await stored(ledger, replacement))["location_slot"] == 1

    async def test_it_announces_itself_after_the_commit(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger, label="Phantom")
        ledger.events.published.clear()

        await ledger.use_cases.delete_spool.execute(spool_id)

        (event,) = ledger.events.of(SpoolDeleted)
        assert (event.spool_id, event.display_name) == (spool_id, "Phantom")

    async def test_a_discarded_spool_cannot_also_be_deleted(self, ledger: Ledger) -> None:
        """The two are mutually exclusive by flow — the intent modal is the only entry
        point and a discarded spool never offers the X — and the entity says so anyway."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=spool_id, mode=DiscardMode.WHOLE_SPOOL, reason="water damage"
            )
        )

        with pytest.raises(Exception, match="discarded"):
            await ledger.use_cases.delete_spool.execute(spool_id)

    async def test_deleting_twice_is_refused(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await ledger.use_cases.delete_spool.execute(spool_id)

        with pytest.raises(SpoolDeletedError, match="restore it from the trash first"):
            await ledger.use_cases.delete_spool.execute(spool_id)

    async def test_an_unknown_spool_is_an_orchestration_failure(self, ledger: Ledger) -> None:
        with pytest.raises(SpoolNotFoundError):
            await ledger.use_cases.delete_spool.execute(SpoolId("nobody"))

    async def test_a_deleted_spool_accepts_no_ordinary_transition(self, ledger: Ledger) -> None:
        """One guard short is the whole bug: a deleted spool mounted into a slot would sit
        there alongside whatever the (now blind) partial index still permits."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.delete_spool.execute(spool_id)

        with pytest.raises(SpoolDeletedError):
            await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        with pytest.raises(SpoolDeletedError):
            await ledger.use_cases.edit_spool_details.execute(spool_id, label="renamed")
        with pytest.raises(SpoolDeletedError):
            await ledger.use_cases.discard_filament.execute(
                DiscardFilamentCommand(
                    spool_id=spool_id, mode=DiscardMode.WHOLE_SPOOL, reason="changed my mind"
                )
            )


class TestRestoringASpool:
    async def test_it_returns_to_storage_without_reclaiming_its_slot(self, ledger: Ledger) -> None:
        """Criterion 11. The slot was freed on delete and something else may be in it;
        taking it back would displace a spool the user physically loaded."""
        deleted = await a_spool(ledger, label="Mistake")
        replacement = await a_spool(ledger, label="The real one")
        await ledger.use_cases.mount_spool.execute(deleted, TRAY_1)
        await ledger.use_cases.delete_spool.execute(deleted)
        await ledger.use_cases.mount_spool.execute(replacement, TRAY_1)
        ledger.events.published.clear()

        await ledger.use_cases.restore_spool.execute(deleted)

        row = await stored(ledger, deleted)
        assert row["deleted_at"] is None
        assert (row["location_kind"], row["location_slot"]) == ("STORAGE", None)
        # The occupant is untouched.
        occupant = await SqliteSpoolRepository(ledger.database).find_by_location(AmsSlot(TRAY_1))
        assert occupant is not None
        assert occupant.id == replacement
        (event,) = ledger.events.of(SpoolRestored)
        assert (event.spool_id, event.display_name) == (deleted, "Mistake")

    async def test_a_spool_that_is_not_in_the_trash_has_nothing_to_restore(
        self, ledger: Ledger
    ) -> None:
        spool_id = await a_spool(ledger)

        with pytest.raises(InvalidValueError, match="not in the trash"):
            await ledger.use_cases.restore_spool.execute(spool_id)

    async def test_its_history_comes_back_with_it(self, ledger: Ledger) -> None:
        """Criterion 11's second half — and it needs no second step, because visibility
        was derived from the spool's state all along."""
        spool_id = await a_spool(ledger, label="Mistake")
        await ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(-100), reason="lamp shade")
        )
        await ledger.use_cases.delete_spool.execute(spool_id)
        assert await ledger.use_cases.queries.movement_history() == []

        await ledger.use_cases.restore_spool.execute(spool_id)

        assert [
            line.movement.type.value for line in await ledger.use_cases.queries.movement_history()
        ] == ["MANUAL_ADJUSTMENT", "OPENING_BALANCE"]


class TestVisibilityAndStatistics:
    """docs/14 §14.4.5, the table — asserted row by row rather than trusted."""

    async def test_a_deleted_spool_leaves_every_default_view(self, ledger: Ledger) -> None:
        """Criterion 10. Absent from inventory, stock, needs-weighing and the global
        history; present in the Trash; its detail reachable and complete."""
        kept = await a_spool(ledger, label="Kept")
        gone = await a_spool(ledger, label="Gone", opening_weight=Grams.of(500))

        await ledger.use_cases.delete_spool.execute(gone)

        assert [s.spool.id for s in await ledger.use_cases.queries.overview()] == [kept]
        stock = await ledger.use_cases.queries.stock()
        # The deleted spool's 500 g are in neither the total nor the count nor the
        # per-material split: it counts as nothing, everywhere (criterion 12).
        assert (stock.total, stock.spool_count) == (Grams.of(1000), 1)
        assert stock.per_material == {"PLA": Grams.of(1000)}
        assert [s.spool.id for s in (await ledger.use_cases.queries.trash()).spools] == [gone]

    async def test_the_global_history_keeps_the_other_spools_rows(self, ledger: Ledger) -> None:
        kept = await a_spool(ledger, label="Kept")
        gone = await a_spool(ledger, label="Gone")

        await ledger.use_cases.delete_spool.execute(gone)

        lines = await ledger.use_cases.queries.movement_history()
        assert [line.spool_name for line in lines] == ["Kept"]
        assert lines[0].movement.spool_id == kept

    async def test_a_discarded_spools_movements_stay_but_a_deleted_ones_do_not(
        self, ledger: Ledger
    ) -> None:
        """The one row of the table where the two retirements disagree: waste is history,
        a retraction is not."""
        discarded = await a_spool(ledger, label="Water damaged")
        deleted = await a_spool(ledger, label="Never existed")
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=discarded, mode=DiscardMode.WHOLE_SPOOL, reason="water damage"
            )
        )
        await ledger.use_cases.delete_spool.execute(deleted)

        names = {line.spool_name for line in await ledger.use_cases.queries.movement_history()}
        assert names == {"Water damaged"}

    async def test_a_deleted_spools_detail_stays_reachable_and_whole(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger, label="Mistake")
        await ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(-100), reason="lamp shade")
        )
        await ledger.use_cases.delete_spool.execute(spool_id)

        detail = await ledger.use_cases.queries.detail(spool_id)

        assert detail.summary.state is SpoolState.DELETED
        assert detail.summary.balance == Grams.of(900)
        assert [line.movement.type for line in detail.lines] == [
            MovementType.MANUAL_ADJUSTMENT,
            MovementType.OPENING_BALANCE,
        ]

    async def test_needs_weighing_ignores_deleted_spools(self, ledger: Ledger) -> None:
        """A spool that counts in nothing must not go on asking to be weighed.

        `needs_weighing` is LOW confidence, and LOW comes from an approved estimate — so
        the setup runs the review queue rather than an adjustment, which only reaches
        MEDIUM.
        """
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        review_id = await ledger.use_cases.open_pending_review.execute(
            OpenPendingReviewCommand(
                job=PrintJob(
                    id=PrintJobId("job-cancelled"),
                    name="bracket_v3.gcode.3mf",
                    state=PrintJobState.CANCELLED,
                    started_at=ledger.clock.now(),
                ),
                reason=ReviewReason.CANCELLED,
                amounts={TRAY_1: Grams.of(70)},
            )
        )
        await ledger.use_cases.approve_review.execute(ApproveReviewCommand(review_id=review_id))
        assert (await ledger.use_cases.queries.stock()).needs_weighing == 1

        await ledger.use_cases.unmount_spool.execute(spool_id)
        await ledger.use_cases.delete_spool.execute(spool_id)

        assert (await ledger.use_cases.queries.stock()).needs_weighing == 0

    async def test_the_trash_lists_deleted_spools_and_open_chapters(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger, label="Kept")
        gone = await a_spool(ledger, label="Gone")
        await ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(-100), reason="lamp shade")
        )
        adjustment = (await ledger.use_cases.queries.detail(spool_id)).lines[0].movement.id
        await ledger.use_cases.delete_spool.execute(gone)
        await ledger.use_cases.void_movement.execute(
            VoidMovementCommand(movement_id=adjustment, reason="wrong spool")
        )

        trash = await ledger.use_cases.queries.trash()

        assert [s.spool.id for s in trash.spools] == [gone]
        assert [m.movement.id for m in trash.movements] == [adjustment]
        entry = trash.movements[0]
        assert entry.restorable
        assert entry.void.reason == "wrong spool"
        assert entry.spool.display_name == "Kept"

    async def test_a_closed_chapter_leaves_the_trash(self, ledger: Ledger) -> None:
        """The Trash lists what is currently out, not everything that ever was."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(-100), reason="lamp shade")
        )
        adjustment = (await ledger.use_cases.queries.detail(spool_id)).lines[0].movement.id
        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=adjustment))
        assert len((await ledger.use_cases.queries.trash()).movements) == 1

        await ledger.use_cases.restore_movement.execute(adjustment)

        assert (await ledger.use_cases.queries.trash()).movements == []

    async def test_a_without_restitution_chapter_says_so_instead_of_offering_restore(
        self, ledger: Ledger
    ) -> None:
        """Criterion 7's Trash half."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(-100), reason="lamp shade")
        )
        adjustment = (await ledger.use_cases.queries.detail(spool_id)).lines[0].movement.id
        await ledger.use_cases.delete_spool.execute(spool_id)
        await ledger.use_cases.void_movement.execute(
            VoidMovementCommand(
                movement_id=adjustment,
                reason="this spool never existed",
                without_restitution=True,
            )
        )

        (entry,) = (await ledger.use_cases.queries.trash()).movements
        assert not entry.void.had_restitution
        assert not entry.restorable

    async def test_an_open_chapter_hides_both_of_its_rows_from_the_global_history(
        self, ledger: Ledger
    ) -> None:
        """Criterion 2's first half — and the pair drops out together, which is what makes
        the hiding arithmetically neutral."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(-100), reason="lamp shade")
        )
        adjustment = (await ledger.use_cases.queries.detail(spool_id)).lines[0].movement.id

        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=adjustment))

        assert [
            line.movement.type.value for line in await ledger.use_cases.queries.movement_history()
        ] == ["OPENING_BALANCE"]

    async def test_a_closed_chapter_shows_all_three_rows(self, ledger: Ledger) -> None:
        """Criterion 3's last clause: the net is honest and the story is complete."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(-100), reason="lamp shade")
        )
        adjustment = (await ledger.use_cases.queries.detail(spool_id)).lines[0].movement.id
        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=adjustment))

        await ledger.use_cases.restore_movement.execute(adjustment)

        assert [
            line.movement.type.value for line in await ledger.use_cases.queries.movement_history()
        ] == ["REINSTATEMENT", "VOID_REVERSAL", "MANUAL_ADJUSTMENT", "OPENING_BALANCE"]

    async def test_the_spool_detail_hides_nothing_and_still_closes(self, ledger: Ledger) -> None:
        """Criterion 2's second half. The detail view is the derivation surface: hiding a
        row there would break the closed sum in the very view that exists to prove it."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(-100), reason="lamp shade")
        )
        adjustment = (await ledger.use_cases.queries.detail(spool_id)).lines[0].movement.id
        await ledger.use_cases.void_movement.execute(VoidMovementCommand(movement_id=adjustment))

        detail = await ledger.use_cases.queries.detail(spool_id)

        assert [(line.movement.type.value, line.voided) for line in detail.lines] == [
            ("VOID_REVERSAL", True),
            ("MANUAL_ADJUSTMENT", True),
            ("OPENING_BALANCE", False),
        ]
        # Newest first in the view; the running balances still telescope to the header.
        assert [line.balance_after for line in detail.lines] == [
            Grams.of(1000),
            Grams.of(900),
            Grams.of(1000),
        ]
        assert detail.summary.balance == Grams.of(1000)


class TestTheRepositoryStopsSeeingDeletedSpools:
    """The forward obligation from the migration: a deleted spool must vanish from every
    read that means "in inventory", or it goes on blocking its tag and answering RFID
    reads for a reel that is out of the ledger (docs/14 §14.4)."""

    async def test_a_deleted_spool_stops_holding_its_tag(self, ledger: Ledger) -> None:
        tag = TagUid("3C45C3DB00000100")
        spool_id = await a_spool(ledger, tag_uid=tag)
        spools = SqliteSpoolRepository(ledger.database)
        assert [s.id for s in await spools.find_by_tag(tag)] == [spool_id]

        await ledger.use_cases.delete_spool.execute(spool_id)

        assert await spools.find_by_tag(tag) == []
        # And so the same reel can be registered again without a duplicate confirmation
        # about a spool the user cannot see anywhere.
        assert await a_spool(ledger, tag_uid=tag, label="The real one") is not None

    async def test_a_deleted_spool_holds_no_position(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        spools = SqliteSpoolRepository(ledger.database)

        await ledger.use_cases.delete_spool.execute(spool_id)

        assert await spools.find_by_location(AmsSlot(TRAY_1)) is None

    async def test_list_excludes_deleted_by_default_and_the_trash_asks_for_them(
        self, ledger: Ledger
    ) -> None:
        kept = await a_spool(ledger, label="Kept")
        gone = await a_spool(ledger, label="Gone")
        await ledger.use_cases.delete_spool.execute(gone)
        spools = SqliteSpoolRepository(ledger.database)

        assert [s.id for s in await spools.list(SpoolFilter())] == [kept]
        assert [s.id for s in await spools.list(SpoolFilter(include_deleted=True))] == [kept, gone]
        assert [s.id for s in await spools.list(SpoolFilter(deleted_only=True))] == [gone]

    async def test_include_discarded_does_not_quietly_include_deleted(self, ledger: Ledger) -> None:
        """The two flags are separate because the two states are separate facts. A caller
        asking to see waste is not asking to see retractions."""
        discarded = await a_spool(ledger, label="Water damaged")
        deleted = await a_spool(ledger, label="Never existed")
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=discarded, mode=DiscardMode.WHOLE_SPOOL, reason="water damage"
            )
        )
        await ledger.use_cases.delete_spool.execute(deleted)
        spools = SqliteSpoolRepository(ledger.database)

        wider = await spools.list(SpoolFilter(include_discarded=True))

        assert [s.id for s in wider] == [discarded]

    async def test_get_still_finds_a_deleted_spool(self, ledger: Ledger) -> None:
        """The one read that never filters — the Trash reaches the detail through it."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.delete_spool.execute(spool_id)

        found = await SqliteSpoolRepository(ledger.database).get(spool_id)

        assert found is not None
        assert found.is_deleted
