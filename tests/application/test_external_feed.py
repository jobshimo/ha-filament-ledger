"""The direct feed, end to end on real SQLite (docs/02 §2.3, docs/04 UC-04/UC-05, v2.8).

The failure these guard was observed live (docs/12-field-notes.md, 2026-09-06): every print
fed from the A1's external spool holder ended figureless and opened a review, because the
gateway dropped the printer's `External Spool` figure and nothing could have charged it
anyway — no spool had a way onto the holder. Each test below is one link of the repair: a
spool mounts on the holder, the figure lands on it, a figureless review lists it, a review
line for it is frozen and approved, and the stored documents round-trip both entry shapes.
"""

from __future__ import annotations

from datetime import timedelta

from custom_components.filament_ledger.application.query import describe_location
from custom_components.filament_ledger.application.register_spool import RegisterSpoolCommand
from custom_components.filament_ledger.application.review_queue import (
    ApproveReviewCommand,
    OpenPendingReviewCommand,
)
from custom_components.filament_ledger.domain.event import (
    SpoolMountedExternally,
    SpoolUnmounted,
)
from custom_components.filament_ledger.domain.model.pending_review import (
    ReviewCharge,
    ReviewLine,
    open_review,
)
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    ExternalFeed,
    Feed,
    HolderIndex,
    SpoolId,
    new_print_job_id,
)
from custom_components.filament_ledger.domain.value.location import ExternalSpool, Storage
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.percentage import Percentage
from custom_components.filament_ledger.domain.value.print_event import PrintEnded, PrintStarted
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.domain.value.review import EstimatorKind, ReviewReason
from custom_components.filament_ledger.infrastructure.persistence.print_job_repository import (
    SqlitePrintJobRepository,
)
from custom_components.filament_ledger.infrastructure.persistence.review_repository import (
    SqliteReviewRepository,
)

from .conftest import A_PRINTER, ANOTHER_PRINTER, EPOCH, Ledger, a_tray

TRAY_1 = a_tray(1)
FEED = ExternalFeed(A_PRINTER)

#: The other holder of the same machine (v2.9). A dual-nozzle printer has two, and the live
#: X2D reports this one's consumption under `External Spool 2` (docs/12, 2026-09-23).
SECOND_HOLDER = HolderIndex(2)
SECOND_FEED = ExternalFeed(A_PRINTER, SECOND_HOLDER)


async def a_spool(ledger: Ledger, **overrides: object) -> SpoolId:
    command = RegisterSpoolCommand(
        material=Material.of(MaterialKind.PLA),
        colour=Colour.parse("000000"),
        opening_weight=Grams.of(1000),
        core_weight=Grams.of(250),
        vendor="Bambu Lab",
        **overrides,  # type: ignore[arg-type]
    )
    return await ledger.use_cases.register_spool.execute(command)


async def ran_to_completion(ledger: Ledger, reported_usage: dict[Feed, Grams] | None) -> PrintJob:
    """One whole lifecycle through the seam as wired: a start, then the FINISHED ending."""
    await ledger.use_cases.track_print_job.execute(
        PrintStarted(name="badge.gcode.3mf", printer=A_PRINTER, plan=None)
    )
    ledger.clock.advance(minutes=42)
    job_id = await ledger.use_cases.track_print_job.execute(
        PrintEnded(
            outcome=PrintJobState.FINISHED,
            name="badge.gcode.3mf",
            printer=A_PRINTER,
            layer_reached=120,
            total_layers=120,
            progress=Percentage.of(100),
            reported_usage=reported_usage,
            raw_gcode_state="finish",
        )
    )
    assert job_id is not None
    job = await SqlitePrintJobRepository(ledger.database).get(job_id)
    assert job is not None
    return job


async def location_of_spool(ledger: Ledger, spool_id: SpoolId) -> object:
    return (await ledger.use_cases.queries.detail(spool_id)).summary.spool.location


async def movement_notes(ledger: Ledger) -> list[tuple[str, int, str | None]]:
    rows = await ledger.database.fetch_all(
        "SELECT spool_id, amount_mg, note FROM movement "
        "WHERE type IN ('PRINT_CONSUMPTION', 'ESTIMATED_CONSUMPTION') ORDER BY rowid"
    )
    return [(row["spool_id"], row["amount_mg"], row["note"]) for row in rows]


class TestMountingOnTheDirectFeed:
    async def test_a_spool_mounts_on_the_holder_and_the_holder_takes_one(
        self, ledger: Ledger
    ) -> None:
        """One spool per direct feed, per printer: mounting a second sends the first to
        storage, the same displacement a tray performs, and both facts are announced."""
        first = await a_spool(ledger)
        second = await a_spool(ledger)

        await ledger.use_cases.mount_spool_externally.execute(first, A_PRINTER)
        await ledger.use_cases.mount_spool_externally.execute(second, A_PRINTER)

        assert await location_of_spool(ledger, first) == Storage()
        assert await location_of_spool(ledger, second) == ExternalSpool(A_PRINTER)
        assert ledger.events.of(SpoolMountedExternally) == [
            SpoolMountedExternally(spool_id=first, printer=A_PRINTER),
            SpoolMountedExternally(spool_id=second, printer=A_PRINTER),
        ]
        assert ledger.events.of(SpoolUnmounted) == [SpoolUnmounted(spool_id=first)]

    async def test_two_printers_have_two_holders(self, ledger: Ledger) -> None:
        first = await a_spool(ledger)
        second = await a_spool(ledger)

        await ledger.use_cases.mount_spool_externally.execute(first, A_PRINTER)
        await ledger.use_cases.mount_spool_externally.execute(second, ANOTHER_PRINTER)

        assert await location_of_spool(ledger, first) == ExternalSpool(A_PRINTER)
        assert await location_of_spool(ledger, second) == ExternalSpool(ANOTHER_PRINTER)

    async def test_mounting_on_the_second_holder_leaves_the_first_one_alone(
        self, ledger: Ledger
    ) -> None:
        """A dual-nozzle machine holds a reel on each feed at once, so displacement has to
        name the holder: unmounting the first one's reel would move a spool nobody touched
        and then charge the next print through it."""
        first = await a_spool(ledger)
        second = await a_spool(ledger)

        await ledger.use_cases.mount_spool_externally.execute(first, A_PRINTER)
        await ledger.use_cases.mount_spool_externally.execute(second, A_PRINTER, SECOND_HOLDER)

        assert await location_of_spool(ledger, first) == ExternalSpool(A_PRINTER)
        assert await location_of_spool(ledger, second) == ExternalSpool(A_PRINTER, SECOND_HOLDER)
        assert ledger.events.of(SpoolUnmounted) == []
        assert ledger.events.of(SpoolMountedExternally) == [
            SpoolMountedExternally(spool_id=first, printer=A_PRINTER),
            SpoolMountedExternally(spool_id=second, printer=A_PRINTER, holder=SECOND_HOLDER),
        ]

    async def test_the_second_holder_takes_one_reel_like_the_first(self, ledger: Ledger) -> None:
        first = await a_spool(ledger)
        second = await a_spool(ledger)

        await ledger.use_cases.mount_spool_externally.execute(first, A_PRINTER, SECOND_HOLDER)
        await ledger.use_cases.mount_spool_externally.execute(second, A_PRINTER, SECOND_HOLDER)

        assert await location_of_spool(ledger, first) == Storage()
        assert await location_of_spool(ledger, second) == ExternalSpool(A_PRINTER, SECOND_HOLDER)
        assert ledger.events.of(SpoolUnmounted) == [SpoolUnmounted(spool_id=first)]

    async def test_each_holder_is_described_to_the_wire_by_its_own_number(
        self, ledger: Ledger
    ) -> None:
        """What the panel mounts back through, and the sentence a reader sees beside it."""
        first = await a_spool(ledger)
        second = await a_spool(ledger)
        await ledger.use_cases.mount_spool_externally.execute(first, A_PRINTER)
        await ledger.use_cases.mount_spool_externally.execute(second, A_PRINTER, SECOND_HOLDER)

        described = {
            spool_id: describe_location(
                (await ledger.use_cases.queries.detail(spool_id)).summary.spool.location
            )
            for spool_id in (first, second)
        }

        assert described[first] == {
            "kind": "EXTERNAL_SPOOL",
            "printer": A_PRINTER.value,
            "ams": None,
            "slot": None,
            "holder": 1,
            "label": "External spool",
        }
        assert described[second]["holder"] == 2
        assert described[second]["label"] == "External spool 2"


class TestAutomaticDeduction:
    async def test_the_external_figure_is_deducted_from_the_spool_on_the_holder(
        self, ledger: Ledger
    ) -> None:
        """The live case: 49.59 g reported under `External Spool`, a spool on the holder,
        no review — the same automatic path a tray's figure takes."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool_externally.execute(spool_id, A_PRINTER)

        job = await ran_to_completion(ledger, {FEED: Grams.of("49.59")})

        assert await movement_notes(ledger) == [
            (spool_id, -49590, "External spool of badge.gcode.3mf")
        ]
        assert job.consumption_recorded is True
        assert await SqliteReviewRepository(ledger.database).list_pending() == []

    async def test_trays_and_the_holder_are_charged_side_by_side(self, ledger: Ledger) -> None:
        in_tray = await a_spool(ledger)
        on_holder = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(in_tray, TRAY_1)
        await ledger.use_cases.mount_spool_externally.execute(on_holder, A_PRINTER)

        await ran_to_completion(ledger, {TRAY_1: Grams.of("10"), FEED: Grams.of("5")})

        assert await movement_notes(ledger) == [
            (in_tray, -10000, "Slot 1 of badge.gcode.3mf"),
            (on_holder, -5000, "External spool of badge.gcode.3mf"),
        ]

    async def test_each_holder_is_charged_through_its_own_spool(self, ledger: Ledger) -> None:
        """The dual-nozzle case. The printer keys the two figures apart — `External Spool`
        and `External Spool 2` — and so does the deduction: a holder's grams must never
        reach the reel hanging off the other nozzle."""
        on_first = await a_spool(ledger)
        on_second = await a_spool(ledger)
        await ledger.use_cases.mount_spool_externally.execute(on_first, A_PRINTER)
        await ledger.use_cases.mount_spool_externally.execute(on_second, A_PRINTER, SECOND_HOLDER)

        await ran_to_completion(ledger, {FEED: Grams.of("12"), SECOND_FEED: Grams.of("7.5")})

        assert await movement_notes(ledger) == [
            (on_first, -12000, "External spool of badge.gcode.3mf"),
            (on_second, -7500, "External spool 2 of badge.gcode.3mf"),
        ]

    async def test_the_second_holders_figure_leaves_the_first_holders_spool_untouched(
        self, ledger: Ledger
    ) -> None:
        """The print drew from the second nozzle only, so the first holder's reel is not a
        participant — and a figure that landed on it would be silent, permanent theft."""
        on_first = await a_spool(ledger)
        on_second = await a_spool(ledger)
        await ledger.use_cases.mount_spool_externally.execute(on_first, A_PRINTER)
        await ledger.use_cases.mount_spool_externally.execute(on_second, A_PRINTER, SECOND_HOLDER)

        await ran_to_completion(ledger, {SECOND_FEED: Grams.of("7.5")})

        assert await movement_notes(ledger) == [
            (on_second, -7500, "External spool 2 of badge.gcode.3mf")
        ]
        first = await ledger.use_cases.queries.detail(on_first)
        assert first.summary.balance == Grams.of(1000)

    async def test_an_external_figure_with_nothing_on_the_holder_goes_to_review(
        self, ledger: Ledger
    ) -> None:
        """Collected, not guessed: the figure waits in a review whose line names the direct
        feed and carries no charge, exactly as an unmapped tray's would."""
        await ran_to_completion(ledger, {FEED: Grams.of("49.59")})

        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.reason is ReviewReason.UNMAPPED_USAGE
        assert review.lines == (ReviewLine(tray=FEED, estimated=Grams.of("49.59")),)
        assert await movement_notes(ledger) == []

    async def test_a_figureless_review_lists_the_holder_among_its_placeholders(
        self, ledger: Ledger
    ) -> None:
        """The card must have a row for the position the print could have drawn from, and
        since v2.8 the holder is one of them — at zero, frozen to the spool mounted there."""
        in_tray = await a_spool(ledger)
        on_holder = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(in_tray, TRAY_1)
        await ledger.use_cases.mount_spool_externally.execute(on_holder, A_PRINTER)

        await ran_to_completion(ledger, None)

        [review] = await SqliteReviewRepository(ledger.database).list_pending()
        assert review.estimator_used is EstimatorKind.NONE
        assert review.lines == (
            ReviewLine(
                tray=TRAY_1,
                estimated=Grams.zero(),
                charges=(ReviewCharge(spool_id=in_tray, amount=Grams.zero()),),
            ),
            ReviewLine(
                tray=FEED,
                estimated=Grams.zero(),
                charges=(ReviewCharge(spool_id=on_holder, amount=Grams.zero()),),
            ),
        )


class TestReviewingTheDirectFeed:
    async def test_a_cancelled_print_freezes_the_holder_s_spool_and_approves_onto_it(
        self, ledger: Ledger
    ) -> None:
        on_holder = await a_spool(ledger)
        await ledger.use_cases.mount_spool_externally.execute(on_holder, A_PRINTER)
        job = PrintJob(
            id=new_print_job_id(),
            name="badge.gcode.3mf",
            state=PrintJobState.CANCELLED,
            started_at=EPOCH,
            ended_at=EPOCH + timedelta(minutes=20),
            printer=A_PRINTER,
        )

        review_id = await ledger.use_cases.open_pending_review.execute(
            OpenPendingReviewCommand(
                job=job, reason=ReviewReason.CANCELLED, amounts={FEED: Grams.of("12.5")}
            )
        )
        review = await SqliteReviewRepository(ledger.database).get(review_id)
        assert review is not None
        assert review.lines == (
            ReviewLine(
                tray=FEED,
                estimated=Grams.of("12.5"),
                charges=(ReviewCharge(spool_id=on_holder, amount=Grams.of("12.5")),),
            ),
        )

        await ledger.use_cases.approve_review.execute(ApproveReviewCommand(review_id=review_id))

        assert await movement_notes(ledger) == [
            (on_holder, -12500, "External spool of a reviewed print")
        ]

    async def test_the_user_can_assign_the_holder_s_line_to_a_spool(self, ledger: Ledger) -> None:
        """The commonest answer the queue asks for, for the position that had nobody."""
        spool_id = await a_spool(ledger)
        job = PrintJob(
            id=new_print_job_id(),
            name="badge.gcode.3mf",
            state=PrintJobState.FAILED,
            started_at=EPOCH,
            ended_at=EPOCH + timedelta(minutes=20),
            printer=A_PRINTER,
        )
        review_id = await ledger.use_cases.open_pending_review.execute(
            OpenPendingReviewCommand(
                job=job, reason=ReviewReason.FAILED, amounts={FEED: Grams.of("3")}
            )
        )

        await ledger.use_cases.approve_review.execute(
            ApproveReviewCommand(review_id=review_id, assignments={FEED: spool_id})
        )

        assert await movement_notes(ledger) == [
            (spool_id, -3000, "External spool of a reviewed print")
        ]


class TestStoredDocuments:
    async def test_a_job_s_usage_round_trips_both_entry_shapes(self, ledger: Ledger) -> None:
        repository = SqlitePrintJobRepository(ledger.database)
        job = PrintJob(
            id=new_print_job_id(),
            name="badge.gcode.3mf",
            state=PrintJobState.FINISHED,
            started_at=EPOCH,
            printer=A_PRINTER,
            reported_usage={FEED: Grams.of("1.2"), TRAY_1: Grams.of("28.4")},
        )
        async with ledger.database:
            await repository.save(job)

        stored = await repository.get(job.id)
        assert stored is not None
        assert stored.reported_usage == {TRAY_1: Grams.of("28.4"), FEED: Grams.of("1.2")}
        [row] = await ledger.database.fetch_all(
            "SELECT reported_usage FROM print_job WHERE id = ?", (job.id,)
        )
        assert row["reported_usage"] == (
            '[{"printer": "00000000TESTSER", "ams": 1, "slot": 1, "mg": 28400}, '
            '{"printer": "00000000TESTSER", "external": true, "mg": 1200}]'
        )

    async def test_the_second_holder_writes_its_number_and_the_first_writes_none(
        self, ledger: Ledger
    ) -> None:
        """One spelling per fact: absence *is* the first holder, so writing `holder: 1`
        would put two spellings of it in one column for ever (docs/08 §8.2)."""
        repository = SqlitePrintJobRepository(ledger.database)
        job = PrintJob(
            id=new_print_job_id(),
            name="both.gcode.3mf",
            state=PrintJobState.FINISHED,
            started_at=EPOCH,
            printer=A_PRINTER,
            reported_usage={FEED: Grams.of("1.2"), SECOND_FEED: Grams.of("3.4")},
        )
        async with ledger.database:
            await repository.save(job)

        stored = await repository.get(job.id)
        assert stored is not None
        assert stored.reported_usage == {FEED: Grams.of("1.2"), SECOND_FEED: Grams.of("3.4")}
        [row] = await ledger.database.fetch_all(
            "SELECT reported_usage FROM print_job WHERE id = ?", (job.id,)
        )
        assert row["reported_usage"] == (
            '[{"printer": "00000000TESTSER", "external": true, "mg": 1200}, '
            '{"printer": "00000000TESTSER", "external": true, "holder": 2, "mg": 3400}]'
        )

    async def test_a_document_written_before_the_second_holder_reads_as_the_first(
        self, ledger: Ledger
    ) -> None:
        """No migration accompanies the second holder either: an entry that names no holder
        was written while a machine could hold one reel directly, so that is the holder it
        means."""
        repository = SqlitePrintJobRepository(ledger.database)
        job = PrintJob(
            id=new_print_job_id(),
            name="old-feed.gcode.3mf",
            state=PrintJobState.FINISHED,
            started_at=EPOCH,
            printer=A_PRINTER,
        )
        async with ledger.database:
            await repository.save(job)
            await ledger.database.execute(
                "UPDATE print_job SET reported_usage = ? WHERE id = ?",
                ('[{"printer": "00000000TESTSER", "external": true, "mg": 49590}]', job.id),
            )

        stored = await repository.get(job.id)
        assert stored is not None
        assert stored.reported_usage == {FEED: Grams.of("49.59")}

    async def test_a_document_written_before_the_direct_feed_still_reads_as_trays(
        self, ledger: Ledger
    ) -> None:
        """No migration accompanies the new shape: a tray entry never carried the key that
        marks the direct feed, so the old documents keep reading exactly as they did."""
        repository = SqlitePrintJobRepository(ledger.database)
        job = PrintJob(
            id=new_print_job_id(),
            name="old.gcode.3mf",
            state=PrintJobState.FINISHED,
            started_at=EPOCH,
            printer=A_PRINTER,
        )
        async with ledger.database:
            await repository.save(job)
            await ledger.database.execute(
                "UPDATE print_job SET reported_usage = ? WHERE id = ?",
                ('[{"printer": "00000000TESTSER", "ams": 1, "slot": 4, "mg": 296560}]', job.id),
            )

        stored = await repository.get(job.id)
        assert stored is not None
        assert stored.reported_usage == {a_tray(4): Grams.of("296.56")}

    async def test_a_review_s_lines_round_trip_the_direct_feed(self, ledger: Ledger) -> None:
        jobs = SqlitePrintJobRepository(ledger.database)
        reviews = SqliteReviewRepository(ledger.database)
        job = PrintJob(
            id=new_print_job_id(),
            name="badge.gcode.3mf",
            state=PrintJobState.CANCELLED,
            started_at=EPOCH,
            ended_at=EPOCH + timedelta(minutes=20),
            printer=A_PRINTER,
        )
        review = open_review(
            job_id=job.id,
            reason=ReviewReason.CANCELLED,
            lines=(
                ReviewLine(
                    tray=FEED,
                    estimated=Grams.of("12.5"),
                    charges=(ReviewCharge(spool_id=SpoolId("s-1"), amount=Grams.of("12.5")),),
                ),
                ReviewLine(tray=TRAY_1, estimated=Grams.of("2")),
            ),
            estimator_used=EstimatorKind.NONE,
            opened_at=EPOCH,
        )
        async with ledger.database:
            await jobs.save(job)
            await reviews.save(review)

        stored = await reviews.get(review.id)
        assert stored is not None
        assert stored.lines == (
            ReviewLine(tray=TRAY_1, estimated=Grams.of("2")),
            ReviewLine(
                tray=FEED,
                estimated=Grams.of("12.5"),
                charges=(ReviewCharge(spool_id=SpoolId("s-1"), amount=Grams.of("12.5")),),
            ),
        )
