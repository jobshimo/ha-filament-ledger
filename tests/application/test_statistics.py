"""What the ledger adds up to — the statistics read model, on real SQLite.

Every scenario here is built through the real use cases, so an assertion about a figure is
an assertion about the actual ledger rather than about a fixture that agrees with the code.

The suite exists mostly to hold one line: **statistics obey the visibility law of
docs/14 §14.4.5, exactly as every other surface does.** A deleted spool contributes to
nothing, an open void chapter drops out with its reversal, a discard is waste rather than
printing, and a *discarded* spool's prints stay counted because waste is history. Those are
the four rules a statistics page gets wrong by default, and each one has a test below.
"""

from __future__ import annotations

from datetime import timedelta

from custom_components.filament_ledger.application.adjust_spool import (
    DiscardFilamentCommand,
    DiscardMode,
)
from custom_components.filament_ledger.application.query import StatisticsPeriod
from custom_components.filament_ledger.application.reassign_movement import (
    ReassignMovementCommand,
)
from custom_components.filament_ledger.application.register_spool import RegisterSpoolCommand
from custom_components.filament_ledger.application.review_queue import (
    ApproveReviewCommand,
    DismissReviewCommand,
    OpenPendingReviewCommand,
)
from custom_components.filament_ledger.application.void_movement import VoidMovementCommand
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    PrintJobId,
    ReviewId,
    SlotIndex,
    SpoolId,
)
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.domain.value.review import ReviewReason

from .conftest import Ledger

ALL_TIME = StatisticsPeriod.ALL_TIME


async def a_spool(ledger: Ledger, **overrides: object) -> SpoolId:
    settings: dict[str, object] = {
        "material": Material.of(MaterialKind.PLA),
        "colour": Colour.parse("000000"),
        "opening_weight": Grams.of(1000),
        "core_weight": Grams.of(250),
        "vendor": "Bambu Lab",
    } | overrides
    command = RegisterSpoolCommand(**settings)  # type: ignore[arg-type]
    return await ledger.use_cases.register_spool.execute(command)


async def a_finished_print(
    ledger: Ledger,
    *,
    name: str = "vase.gcode.3mf",
    slot: int = 1,
    used: str = "100",
    minutes: int = 60,
) -> PrintJobId:
    """A completed job through UC-04 — the path that writes a job-linked consumption.

    The clock advances by the print's own duration, which is what makes `started_at` and
    `ended_at` differ in the way a real print makes them differ.
    """
    started = ledger.clock.now()
    ledger.clock.advance(minutes=minutes)
    job = PrintJob(
        id=PrintJobId(f"job-{name}-{started.isoformat()}"),
        name=name,
        state=PrintJobState.FINISHED,
        started_at=started,
        ended_at=ledger.clock.now(),
        reported_usage={SlotIndex(slot): Grams.of(used)},
    )
    await ledger.use_cases.record_print_consumption.execute(job)
    return job.id


async def an_interrupted_print(
    ledger: Ledger,
    *,
    outcome: PrintJobState,
    name: str = "bracket.gcode.3mf",
    slot: int = 1,
    used: str = "40",
    minutes: int = 30,
) -> ReviewId:
    """A cancelled or failed job through UC-05: the job row plus the review it demands."""
    started = ledger.clock.now()
    ledger.clock.advance(minutes=minutes)
    job = PrintJob(
        id=PrintJobId(f"job-{name}-{started.isoformat()}"),
        name=name,
        state=outcome,
        started_at=started,
        ended_at=ledger.clock.now(),
        reported_usage={SlotIndex(slot): Grams.of(used)},
    )
    reason = ReviewReason.CANCELLED if outcome is PrintJobState.CANCELLED else ReviewReason.FAILED
    return await ledger.use_cases.open_pending_review.execute(
        OpenPendingReviewCommand(job=job, reason=reason)
    )


class TestPeriod:
    async def test_the_default_window_is_the_last_thirty_days(self, ledger: Ledger) -> None:
        """A print from six weeks ago is history, not this month's consumption."""
        spool = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool, SlotIndex(1))
        await a_finished_print(ledger, used="100")
        ledger.clock.advance(days=40)

        assert (await ledger.use_cases.queries.statistics()).consumed == Grams.zero()

    async def test_a_wider_window_reaches_further_back(self, ledger: Ledger) -> None:
        spool = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool, SlotIndex(1))
        await a_finished_print(ledger, used="100")
        ledger.clock.advance(days=40)

        queries = ledger.use_cases.queries

        assert (await queries.statistics(StatisticsPeriod.LAST_90_DAYS)).consumed == Grams.of(100)
        assert (await queries.statistics(ALL_TIME)).consumed == Grams.of(100)

    async def test_the_cut_off_is_reported_beside_the_figures(self, ledger: Ledger) -> None:
        """The panel prints no date itself; the view says what window it answered for."""
        now = ledger.clock.now()

        view = await ledger.use_cases.queries.statistics(StatisticsPeriod.LAST_30_DAYS)

        assert view.period is StatisticsPeriod.LAST_30_DAYS
        assert view.since == now - timedelta(days=30)

    async def test_all_time_has_no_cut_off_at_all(self, ledger: Ledger) -> None:
        view = await ledger.use_cases.queries.statistics(ALL_TIME)

        assert view.since is None


class TestVisibilityLaw:
    """docs/14 §14.4.5, applied to every figure on the page."""

    async def test_a_deleted_spools_consumption_counts_in_nothing(self, ledger: Ledger) -> None:
        """Registered by mistake means it never happened — not even in a chart."""
        kept = await a_spool(ledger, label="kept", colour=Colour.parse("112233"))
        mistake = await a_spool(ledger, label="mistake", colour=Colour.parse("445566"))
        await ledger.use_cases.mount_spool.execute(kept, SlotIndex(1))
        await ledger.use_cases.mount_spool.execute(mistake, SlotIndex(2))
        await a_finished_print(ledger, name="kept.3mf", slot=1, used="100")
        await a_finished_print(ledger, name="mistake.3mf", slot=2, used="250")
        await ledger.use_cases.delete_spool.execute(mistake)

        view = await ledger.use_cases.queries.statistics(ALL_TIME)

        assert view.consumed == Grams.of(100)
        assert [entry.colour for entry in view.by_colour] == [Colour.parse("112233")]
        assert [entry.name for entry in view.top_prints] == ["kept.3mf"]

    async def test_an_open_void_chapter_counts_in_nothing(self, ledger: Ledger) -> None:
        """The voided entry and the reversal that returned its grams drop out together,
        which is arithmetically neutral: they sum to zero."""
        spool = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool, SlotIndex(1))
        await a_finished_print(ledger, name="real.3mf", used="100")
        await a_finished_print(ledger, name="wrong.3mf", used="250")
        history = await ledger.use_cases.queries.detail(spool)
        wrong = next(
            line.movement for line in history.lines if line.movement.note == "Slot 1 of wrong.3mf"
        )
        await ledger.use_cases.void_movement.execute(
            VoidMovementCommand(movement_id=wrong.id, reason="charged to the wrong spool")
        )

        view = await ledger.use_cases.queries.statistics(ALL_TIME)

        assert view.consumed == Grams.of(100)
        assert [entry.name for entry in view.top_prints] == ["real.3mf"]

    async def test_a_reassigned_charge_counts_once_where_it_was_consumed(
        self, ledger: Ledger
    ) -> None:
        """The compensating pair moves the balance, not the chart. Consumption stays
        attributed to the spool the consumption entry names, and neither REASSIGNMENT leg
        is counted anywhere — the pair nets to zero by design (docs/14 §14.3), so a leg
        landing inside the window while its sibling falls outside must never bend a bar."""
        charged = await a_spool(ledger, label="charged", colour=Colour.parse("112233"))
        correct = await a_spool(ledger, label="correct", colour=Colour.parse("445566"))
        await ledger.use_cases.mount_spool.execute(charged, SlotIndex(1))
        await a_finished_print(ledger, name="moved.3mf", slot=1, used="100")
        history = await ledger.use_cases.queries.detail(charged)
        charge = next(
            line.movement for line in history.lines if line.movement.note == "Slot 1 of moved.3mf"
        )

        await ledger.use_cases.reassign_movement.execute(
            ReassignMovementCommand(movement_id=charge.id, to_spool_id=correct)
        )

        view = await ledger.use_cases.queries.statistics(ALL_TIME)

        assert view.consumed == Grams.of(100)
        assert [entry.colour for entry in view.by_colour] == [Colour.parse("112233")]
        assert [entry.name for entry in view.top_prints] == ["moved.3mf"]

    async def test_a_discard_is_waste_and_never_consumption(self, ledger: Ledger) -> None:
        spool = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool, SlotIndex(1))
        await a_finished_print(ledger, used="100")
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=spool,
                mode=DiscardMode.PARTIAL,
                reason="tangled on the reel",
                amount=Grams.of(30),
            )
        )

        view = await ledger.use_cases.queries.statistics(ALL_TIME)

        assert view.consumed == Grams.of(100)
        assert view.wasted == Grams.of(30)
        assert [entry.grams for entry in view.by_colour] == [Grams.of(100)]

    async def test_a_discarded_spools_prints_still_count(self, ledger: Ledger) -> None:
        """Waste is history. Throwing the reel away does not un-print what it printed."""
        spool = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool, SlotIndex(1))
        await a_finished_print(ledger, used="100")
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=spool, mode=DiscardMode.WHOLE_SPOOL, reason="ran out and binned it"
            )
        )

        view = await ledger.use_cases.queries.statistics(ALL_TIME)

        assert view.consumed == Grams.of(100)
        # Everything the whole-spool discard wrote off is waste — the remaining balance.
        assert view.wasted == Grams.of(900)


class TestBreakdowns:
    async def test_consumption_groups_by_colour_biggest_first(self, ledger: Ledger) -> None:
        """Two spools of the same colour are one bar: the chart answers *which colour goes
        fastest*, and that question does not care how many reels it took."""
        first = await a_spool(ledger, label="purple A", colour=Colour.parse("8323FF"))
        second = await a_spool(ledger, label="purple B", colour=Colour.parse("8323FF"))
        ivory = await a_spool(ledger, label="ivory", colour=Colour.parse("FFFFF0"))
        await ledger.use_cases.mount_spool.execute(first, SlotIndex(1))
        await ledger.use_cases.mount_spool.execute(ivory, SlotIndex(2))
        await a_finished_print(ledger, name="a.3mf", slot=1, used="60")
        await a_finished_print(ledger, name="b.3mf", slot=2, used="140")
        await ledger.use_cases.unmount_spool.execute(first)
        await ledger.use_cases.mount_spool.execute(second, SlotIndex(1))
        await a_finished_print(ledger, name="c.3mf", slot=1, used="120")

        view = await ledger.use_cases.queries.statistics(ALL_TIME)

        assert [(entry.colour.display_hex, entry.grams) for entry in view.by_colour] == [
            ("#8323FF", Grams.of(180)),
            ("#FFFFF0", Grams.of(140)),
        ]

    async def test_consumption_groups_by_material_biggest_first(self, ledger: Ledger) -> None:
        pla = await a_spool(ledger, material=Material.of(MaterialKind.PLA))
        petg = await a_spool(ledger, material=Material.of(MaterialKind.PETG))
        await ledger.use_cases.mount_spool.execute(pla, SlotIndex(1))
        await ledger.use_cases.mount_spool.execute(petg, SlotIndex(2))
        await a_finished_print(ledger, name="a.3mf", slot=1, used="40")
        await a_finished_print(ledger, name="b.3mf", slot=2, used="90")

        view = await ledger.use_cases.queries.statistics(ALL_TIME)

        assert [(entry.material, entry.grams) for entry in view.by_material] == [
            ("PETG", Grams.of(90)),
            ("PLA", Grams.of(40)),
        ]

    async def test_the_biggest_five_prints_are_named_heaviest_first(self, ledger: Ledger) -> None:
        """Five, not six: a top-consumers table is a glance, and the sixth row is where it
        stops being one."""
        spool = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool, SlotIndex(1))
        for index, used in enumerate((10, 60, 30, 50, 20, 40)):
            await a_finished_print(ledger, name=f"print-{index}.3mf", used=str(used))

        view = await ledger.use_cases.queries.statistics(ALL_TIME)

        assert [(entry.name, entry.grams) for entry in view.top_prints] == [
            ("print-1.3mf", Grams.of(60)),
            ("print-3.3mf", Grams.of(50)),
            ("print-5.3mf", Grams.of(40)),
            ("print-2.3mf", Grams.of(30)),
            ("print-4.3mf", Grams.of(20)),
        ]

    async def test_a_print_across_two_spools_is_one_row(self, ledger: Ledger) -> None:
        """UC-04 writes one movement per slot; the table is about the *print*, so its two
        charges are added rather than listed twice."""
        first = await a_spool(ledger, colour=Colour.parse("112233"))
        second = await a_spool(ledger, colour=Colour.parse("445566"))
        await ledger.use_cases.mount_spool.execute(first, SlotIndex(1))
        await ledger.use_cases.mount_spool.execute(second, SlotIndex(2))
        started = ledger.clock.now()
        ledger.clock.advance(minutes=90)
        await ledger.use_cases.record_print_consumption.execute(
            PrintJob(
                id=PrintJobId("job-two-slots"),
                name="two_colours.3mf",
                state=PrintJobState.FINISHED,
                started_at=started,
                ended_at=ledger.clock.now(),
                reported_usage={SlotIndex(1): Grams.of(30), SlotIndex(2): Grams.of(70)},
            )
        )

        view = await ledger.use_cases.queries.statistics(ALL_TIME)

        assert [(entry.name, entry.grams) for entry in view.top_prints] == [
            ("two_colours.3mf", Grams.of(100))
        ]


class TestOutcomes:
    async def test_prints_are_counted_by_how_they_ended(self, ledger: Ledger) -> None:
        spool = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool, SlotIndex(1))
        await a_finished_print(ledger, name="one.3mf")
        await a_finished_print(ledger, name="two.3mf")
        await an_interrupted_print(ledger, outcome=PrintJobState.CANCELLED, name="three.3mf")
        await an_interrupted_print(ledger, outcome=PrintJobState.FAILED, name="four.3mf")

        prints = (await ledger.use_cases.queries.statistics(ALL_TIME)).prints

        assert (prints.finished, prints.cancelled, prints.failed) == (2, 1, 1)
        assert prints.total == 4

    async def test_a_running_job_has_no_outcome_to_count(self, ledger: Ledger) -> None:
        """It has not ended, so it ended in nothing. Counting it anywhere would report a
        result that does not exist yet."""
        await ledger.use_cases.track_print_job.jobs.save(
            PrintJob(
                id=PrintJobId("job-running"),
                name="still_going.3mf",
                state=PrintJobState.RUNNING,
                started_at=ledger.clock.now(),
            )
        )

        prints = (await ledger.use_cases.queries.statistics(ALL_TIME)).prints

        assert prints.total == 0

    async def test_reviews_are_counted_by_how_they_were_decided(self, ledger: Ledger) -> None:
        """Neither number is derivable from the movements: a dismissal writes none."""
        spool = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool, SlotIndex(1))
        approved = await an_interrupted_print(
            ledger, outcome=PrintJobState.CANCELLED, name="approved.3mf"
        )
        dismissed = await an_interrupted_print(
            ledger, outcome=PrintJobState.FAILED, name="dismissed.3mf"
        )
        await ledger.use_cases.approve_review.execute(ApproveReviewCommand(review_id=approved))
        await ledger.use_cases.dismiss_review.execute(DismissReviewCommand(review_id=dismissed))

        reviews = (await ledger.use_cases.queries.statistics(ALL_TIME)).reviews

        assert (reviews.approved, reviews.dismissed) == (1, 1)

    async def test_a_pending_review_is_not_a_decision(self, ledger: Ledger) -> None:
        spool = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool, SlotIndex(1))
        await an_interrupted_print(ledger, outcome=PrintJobState.CANCELLED)

        reviews = (await ledger.use_cases.queries.statistics(ALL_TIME)).reviews

        assert (reviews.approved, reviews.dismissed, reviews.total) == (0, 0, 0)

    async def test_an_approved_estimate_counts_as_consumption(self, ledger: Ledger) -> None:
        """The one kind of estimate that reaches the ledger is one a person approved, and
        the grams it wrote are as real as any other. The amount is supplied at approval —
        the user weighing the half-printed part and typing what it came to, which is
        UC-06's whole point."""
        spool = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool, SlotIndex(1))
        review = await an_interrupted_print(
            ledger, outcome=PrintJobState.CANCELLED, name="half.3mf", used="40"
        )
        await ledger.use_cases.approve_review.execute(
            ApproveReviewCommand(review_id=review, amounts={SlotIndex(1): Grams.of(40)})
        )

        view = await ledger.use_cases.queries.statistics(ALL_TIME)

        assert view.consumed == Grams.of(40)
        assert [entry.name for entry in view.top_prints] == ["half.3mf"]


class TestPrintTime:
    async def test_print_time_is_measured_from_the_jobs_own_timestamps(
        self, ledger: Ledger
    ) -> None:
        spool = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool, SlotIndex(1))
        await a_finished_print(ledger, name="one.3mf", minutes=60)
        await a_finished_print(ledger, name="two.3mf", minutes=120)

        measured = (await ledger.use_cases.queries.statistics(ALL_TIME)).print_time

        assert measured is not None
        assert measured.total == timedelta(minutes=180)
        assert measured.average == timedelta(minutes=90)
        assert measured.prints == 2

    async def test_a_job_whose_start_was_lost_is_not_a_zero_length_print(
        self, ledger: Ledger
    ) -> None:
        """`TrackPrintJob` writes `started_at == ended_at` when a restart swallowed the
        start. That row's duration is zero, and zero is not how long a print took — so it
        is left out of the measurement rather than dragging the average down."""
        spool = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool, SlotIndex(1))
        await a_finished_print(ledger, name="real.3mf", minutes=60)
        moment = ledger.clock.now()
        await ledger.use_cases.record_print_consumption.execute(
            PrintJob(
                id=PrintJobId("job-restart"),
                name="recovered.3mf",
                state=PrintJobState.FINISHED,
                started_at=moment,
                ended_at=moment,
                reported_usage={SlotIndex(1): Grams.of(10)},
            )
        )

        measured = (await ledger.use_cases.queries.statistics(ALL_TIME)).print_time

        assert measured is not None
        assert measured.prints == 1
        assert measured.total == timedelta(minutes=60)

    async def test_nothing_measurable_reports_no_print_time_at_all(self, ledger: Ledger) -> None:
        """A card of dashes teaches nothing. `None` is how the read model declines to
        invent a metric it cannot support."""
        await a_spool(ledger)

        assert (await ledger.use_cases.queries.statistics(ALL_TIME)).print_time is None


class TestEmptiness:
    async def test_a_fresh_ledger_has_nothing_to_count(self, ledger: Ledger) -> None:
        view = await ledger.use_cases.queries.statistics(ALL_TIME)

        assert view.is_empty
        assert view.consumed == Grams.zero()
        assert view.wasted == Grams.zero()
        assert view.by_colour == []
        assert view.by_material == []
        assert view.top_prints == []

    async def test_a_registration_alone_is_still_nothing_to_count(self, ledger: Ledger) -> None:
        """An opening balance is filament arriving, not filament used. A page reporting a
        kilogram *printed* on the day a spool was registered would be a lie in the first
        figure the user ever reads."""
        await a_spool(ledger)

        assert (await ledger.use_cases.queries.statistics(ALL_TIME)).is_empty

    async def test_a_period_with_one_gram_in_it_is_not_empty(self, ledger: Ledger) -> None:
        """`is_empty` is decided on the exact grams, not on the rounded ones."""
        spool = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool, SlotIndex(1))
        await a_finished_print(ledger, used="0.4")

        view = await ledger.use_cases.queries.statistics(ALL_TIME)

        assert not view.is_empty
        assert view.consumed == Grams.of("0.4")
