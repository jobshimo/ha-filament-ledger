"""UC-12 across every spool: the global history read model, on real SQLite.

The per-spool history is served by `Queries.detail`; `movement_history` answers the other
question — *what has been happening?* — newest first, each row joined to its spool's
display name and colour and, when the movement carries a `job_id`, to the print job's
name. These tests drive the join through the real use cases, so every assertion about a
line is an assertion about the actual ledger.
"""

from __future__ import annotations

from datetime import datetime

from custom_components.filament_ledger.application.adjust_spool import (
    AdjustSpoolCommand,
    DiscardFilamentCommand,
    DiscardMode,
)
from custom_components.filament_ledger.application.query import GlobalHistoryLine
from custom_components.filament_ledger.application.register_spool import RegisterSpoolCommand
from custom_components.filament_ledger.application.review_queue import (
    ApproveReviewCommand,
    OpenPendingReviewCommand,
)
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.port.repositories import (
    NO_FILTERS,
    MovementFilter,
)
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    PrintJobId,
    SlotIndex,
    SpoolId,
)
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.domain.value.review import ReviewReason

from .conftest import Ledger

BLACK = Colour.parse("000000")
IVORY = Colour.parse("FFFFF0")
ORANGE = Colour.parse("FF6A13")


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


async def a_finished_print(ledger: Ledger, name: str = "vase_final.gcode.3mf") -> None:
    """A completed job through UC-04 — the path that writes a job-linked movement."""
    await ledger.use_cases.record_print_consumption.execute(
        PrintJob(
            id=PrintJobId(f"job-{name}"),
            name=name,
            state=PrintJobState.FINISHED,
            started_at=ledger.clock.now(),
            ended_at=ledger.clock.now(),
            reported_usage={SlotIndex(1): Grams.of("84.1")},
        )
    )


async def an_approved_estimate(ledger: Ledger, spool_id: SpoolId, job_name: str) -> None:
    """A cancelled print settled through UC-05 → UC-06, an hour after the last event.

    The one path that writes an entry carrying a `job_id` whose **note does not repeat the
    job's name**: UC-04's automatic deduction notes "Slot 1 of <job>", so a free-text match
    against it would prove nothing about which column was read. An approved estimate notes
    "Slot 1 of a reviewed print", and the name the user recognises lives only in the
    `print_job` row.
    """
    await ledger.use_cases.mount_spool.execute(spool_id, SlotIndex(1))
    ledger.clock.advance(hours=1)
    review_id = await ledger.use_cases.open_pending_review.execute(
        OpenPendingReviewCommand(
            job=PrintJob(
                id=PrintJobId(f"job-{job_name}"),
                name=job_name,
                state=PrintJobState.CANCELLED,
                started_at=ledger.clock.now(),
                layer_reached=71,
                total_layers=209,
                reported_usage={SlotIndex(1): Grams.of(209)},
            ),
            reason=ReviewReason.CANCELLED,
        )
    )
    await ledger.use_cases.approve_review.execute(ApproveReviewCommand(review_id=review_id))


async def an_adjustment(ledger: Ledger, spool_id: SpoolId, amount: str, reason: str) -> datetime:
    """One manual entry an hour after the last, returning the moment it happened.

    The clock is advanced by the helper so that a scenario reads as a sequence of events
    rather than as a sequence of clock calls; the returned moment is what the date-bound
    assertions are written against, so no test has to compute a timestamp.
    """
    ledger.clock.advance(hours=1)
    await ledger.use_cases.adjust_spool.execute(
        AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(amount), reason=reason)
    )
    return ledger.clock.now()


def notes(lines: list[GlobalHistoryLine]) -> list[str | None]:
    """Every note in the slice, newest first — the reason each entry was written."""
    return [line.movement.note for line in lines]


class TestMovementHistory:
    async def test_newest_first_across_every_spool(self, ledger: Ledger) -> None:
        """Two spools interleaved in time come back as one stream, newest first — the
        order `list_recent` serves, not an order the query re-derives."""
        black = await a_spool(ledger, label="Black")
        ledger.clock.advance(hours=1)
        await a_spool(ledger, label="Ivory", colour=Colour.parse("FFFFF0"))
        ledger.clock.advance(hours=1)
        await ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=black, amount=Grams.of(-100), reason="lamp shade")
        )

        lines = await ledger.use_cases.queries.movement_history()

        assert [(line.spool_name, line.movement.type.value) for line in lines] == [
            ("Black", "MANUAL_ADJUSTMENT"),
            ("Ivory", "OPENING_BALANCE"),
            ("Black", "OPENING_BALANCE"),
        ]

    async def test_each_line_carries_its_spools_name_and_colour(self, ledger: Ledger) -> None:
        """The join the panel renders a swatch from: name and colour come off the spool
        row, not off anything the movement stores."""
        await a_spool(ledger, label="Galaxy Purple", colour=Colour.parse("8323FF"))

        (line,) = await ledger.use_cases.queries.movement_history()

        assert line.spool_name == "Galaxy Purple"
        assert line.spool_colour == Colour.parse("8323FF")

    async def test_a_job_linked_movement_carries_the_jobs_name(self, ledger: Ledger) -> None:
        """UC-04's automatic deduction stores a `job_id`; the read model resolves it to
        the name the user recognises. The opening balance on the same spool carries no
        job, and its `job_name` is honestly null rather than an empty string."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SlotIndex(1))
        ledger.clock.advance(hours=1)
        await a_finished_print(ledger, name="vase_final.gcode.3mf")

        lines = await ledger.use_cases.queries.movement_history()

        assert [line.movement.type.value for line in lines] == [
            "PRINT_CONSUMPTION",
            "OPENING_BALANCE",
        ]
        assert lines[0].job_name == "vase_final.gcode.3mf"
        assert lines[0].movement.job_id is not None
        assert lines[1].job_name is None

    async def test_the_limit_takes_the_newest_entries_not_the_oldest(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        for reason in ("first", "second", "third"):
            ledger.clock.advance(hours=1)
            await ledger.use_cases.adjust_spool.execute(
                AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(-1), reason=reason)
            )

        lines = await ledger.use_cases.queries.movement_history(limit=2)

        assert [line.movement.note for line in lines] == ["third", "second"]

    async def test_an_empty_ledger_is_an_empty_history(self, ledger: Ledger) -> None:
        assert await ledger.use_cases.queries.movement_history() == []

    async def test_a_discarded_spools_movements_stay_in_the_history(self, ledger: Ledger) -> None:
        """Discarding removes a spool from inventory, never from history — the ledger
        would stop reconciling the day its rows started disappearing with their spools."""
        spool_id = await a_spool(ledger, label="Water damaged")
        ledger.clock.advance(hours=1)
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=spool_id, mode=DiscardMode.WHOLE_SPOOL, reason="water damage"
            )
        )

        lines = await ledger.use_cases.queries.movement_history()

        assert [line.movement.type.value for line in lines] == ["DISCARD", "OPENING_BALANCE"]
        assert all(line.spool_name == "Water damaged" for line in lines)


class TestHistoryFilters:
    """Narrowing the history — a date, a colour, a weight, a word (FEATURE-REQUESTS §5).

    Every criterion below reaches SQLite as a `WHERE` clause. None of it is applied to a
    list this layer has already fetched, and the last test in the class is the one that
    says why: the limit has to take the newest of what *matched*.
    """

    async def test_the_empty_filter_returns_exactly_what_the_unfiltered_read_returns(
        self, ledger: Ledger
    ) -> None:
        """*Clear every filter* is not a control with a behaviour of its own. It is the
        value object with nothing set, it builds no clause at all, and it therefore has to
        answer with the history this read has served since before there were filters."""
        spool_id = await a_spool(ledger, label="Black")
        await an_adjustment(ledger, spool_id, "-100", "lamp shade")

        unfiltered = await ledger.use_cases.queries.movement_history()

        assert notes(unfiltered) == ["lamp shade", "Registered"]
        assert await ledger.use_cases.queries.movement_history(criteria=NO_FILTERS) == unfiltered
        assert (
            await ledger.use_cases.queries.movement_history(criteria=MovementFilter()) == unfiltered
        )

    async def test_a_from_date_keeps_the_entries_at_or_after_it(self, ledger: Ledger) -> None:
        """Inclusive, because a user who picks a day means that day."""
        spool_id = await a_spool(ledger)
        await an_adjustment(ledger, spool_id, "-1", "before")
        boundary = await an_adjustment(ledger, spool_id, "-2", "on the boundary")
        await an_adjustment(ledger, spool_id, "-3", "after")

        lines = await ledger.use_cases.queries.movement_history(
            criteria=MovementFilter(since=boundary)
        )

        assert notes(lines) == ["after", "on the boundary"]

    async def test_an_until_date_keeps_the_entries_at_or_before_it(self, ledger: Ledger) -> None:
        """The other bound, and independent of the first: asking only for *up to here*
        reaches back to the opening balance, which is what the whole history means."""
        spool_id = await a_spool(ledger)
        await an_adjustment(ledger, spool_id, "-1", "before")
        boundary = await an_adjustment(ledger, spool_id, "-2", "on the boundary")
        await an_adjustment(ledger, spool_id, "-3", "after")

        lines = await ledger.use_cases.queries.movement_history(
            criteria=MovementFilter(until=boundary)
        )

        assert notes(lines) == ["on the boundary", "before", "Registered"]

    async def test_the_two_dates_together_are_a_window(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await an_adjustment(ledger, spool_id, "-1", "too early")
        opens = await an_adjustment(ledger, spool_id, "-2", "first inside")
        closes = await an_adjustment(ledger, spool_id, "-3", "last inside")
        await an_adjustment(ledger, spool_id, "-4", "too late")

        lines = await ledger.use_cases.queries.movement_history(
            criteria=MovementFilter(since=opens, until=closes)
        )

        assert notes(lines) == ["last inside", "first inside"]

    async def test_a_colour_filter_matches_the_colour_of_the_spool_an_entry_belongs_to(
        self, ledger: Ledger
    ) -> None:
        """A movement carries no colour of its own, so the filter is a join — and the
        colour the user points at is the spool's, which is what the History table paints
        the swatch from."""
        black = await a_spool(ledger, label="Black", colour=BLACK)
        ledger.clock.advance(hours=1)
        await a_spool(ledger, label="Ivory", colour=IVORY)
        await an_adjustment(ledger, black, "-100", "lamp shade")

        lines = await ledger.use_cases.queries.movement_history(
            criteria=MovementFilter(colours=frozenset({BLACK}))
        )

        assert notes(lines) == ["lamp shade", "Registered"]
        assert {line.spool_name for line in lines} == {"Black"}

    async def test_several_colours_are_one_question_rather_than_two(self, ledger: Ledger) -> None:
        """*Show me the blacks and the ivories* is one thing a user asks, so the filter
        takes a set. A single-valued one would make it two searches and no way to see them
        interleaved in time."""
        await a_spool(ledger, label="Black", colour=BLACK)
        ledger.clock.advance(hours=1)
        await a_spool(ledger, label="Ivory", colour=IVORY)
        ledger.clock.advance(hours=1)
        await a_spool(ledger, label="Orange", colour=ORANGE)

        lines = await ledger.use_cases.queries.movement_history(
            criteria=MovementFilter(colours=frozenset({BLACK, IVORY}))
        )

        assert [line.spool_name for line in lines] == ["Ivory", "Black"]

    async def test_a_minimum_weight_is_a_magnitude_so_a_print_consumption_matches_it(
        self, ledger: Ledger
    ) -> None:
        """**The trap a future refactor will spring.**

        Amounts are stored signed — a print consumption is −84.1 g — and a user asking for
        entries over 50 g is asking how much filament moved, not which way it went.
        Comparing the stored value instead of its magnitude would answer with every
        increase in the ledger and with no print at all: the wrong answer, in the one view
        that exists to show prints, and a plausible enough reading that nobody would look
        at it twice.
        """
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SlotIndex(1))
        ledger.clock.advance(hours=1)
        await a_finished_print(ledger)
        await an_adjustment(ledger, spool_id, "10", "found a spare length")
        await an_adjustment(ledger, spool_id, "-5", "spilled")

        lines = await ledger.use_cases.queries.movement_history(
            criteria=MovementFilter(min_magnitude=Grams.of(50))
        )

        assert [(line.movement.type.value, line.movement.amount) for line in lines] == [
            ("PRINT_CONSUMPTION", Grams.of("-84.1")),
            ("OPENING_BALANCE", Grams.of(1000)),
        ]

    async def test_a_maximum_weight_is_a_magnitude_too(self, ledger: Ledger) -> None:
        """The symmetric half of the same rule: a −5 g correction is *smaller* than a
        +10 g one, and both are smaller than the print between them."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SlotIndex(1))
        ledger.clock.advance(hours=1)
        await a_finished_print(ledger)
        await an_adjustment(ledger, spool_id, "10", "found a spare length")
        await an_adjustment(ledger, spool_id, "-5", "spilled")

        lines = await ledger.use_cases.queries.movement_history(
            criteria=MovementFilter(max_magnitude=Grams.of(50))
        )

        assert [line.movement.amount for line in lines] == [Grams.of(-5), Grams.of(10)]

    async def test_the_two_weights_together_are_a_band(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, SlotIndex(1))
        ledger.clock.advance(hours=1)
        await a_finished_print(ledger)
        await an_adjustment(ledger, spool_id, "10", "found a spare length")
        await an_adjustment(ledger, spool_id, "-5", "spilled")

        lines = await ledger.use_cases.queries.movement_history(
            criteria=MovementFilter(min_magnitude=Grams.of(6), max_magnitude=Grams.of(100))
        )

        assert [line.movement.amount for line in lines] == [Grams.of(10), Grams.of("-84.1")]

    async def test_free_text_finds_the_note_the_user_typed(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await an_adjustment(ledger, spool_id, "-100", "lamp shade")
        await an_adjustment(ledger, spool_id, "-2", "purge tower")

        lines = await ledger.use_cases.queries.movement_history(
            criteria=MovementFilter(search="lamp")
        )

        assert notes(lines) == ["lamp shade"]

    async def test_free_text_finds_the_name_of_the_job_an_entry_belongs_to(
        self, ledger: Ledger
    ) -> None:
        """The searchable text is **not one column**. The History table's entry cell
        renders the job name beside the note, so the filter reads both — and this entry's
        note says nothing about the print, which is what makes the match provable."""
        spool_id = await a_spool(ledger)
        await an_approved_estimate(ledger, spool_id, "bracket_v3.gcode.3mf")

        lines = await ledger.use_cases.queries.movement_history(
            criteria=MovementFilter(search="bracket")
        )

        assert [line.job_name for line in lines] == ["bracket_v3.gcode.3mf"]
        assert notes(lines) == ["Slot 1 of a reviewed print"]

    async def test_free_text_ignores_case(self, ledger: Ledger) -> None:
        """Nobody types a search the way they typed the note six weeks ago."""
        spool_id = await a_spool(ledger)
        await an_adjustment(ledger, spool_id, "-100", "Lamp shade")

        lines = await ledger.use_cases.queries.movement_history(
            criteria=MovementFilter(search="LAMP")
        )

        assert notes(lines) == ["Lamp shade"]

    async def test_free_text_does_not_reach_the_spools_own_name(self, ledger: Ledger) -> None:
        """The spool is a column of its own beside the entry, with a colour filter of its
        own to narrow it. Folding its name into the entry's would make a search for a
        spool return every entry that spool ever had — the colour filter in disguise, and
        a second way to ask one question."""
        spool_id = await a_spool(ledger, label="Galaxy Purple")
        await an_adjustment(ledger, spool_id, "-100", "lamp shade")

        lines = await ledger.use_cases.queries.movement_history(
            criteria=MovementFilter(search="galaxy")
        )

        assert lines == []

    async def test_the_filters_combine_with_and(self, ledger: Ledger) -> None:
        """Four criteria at once, each of which alone would admit more. Every entry below
        fails exactly one of them, which is what makes the survivor mean something."""
        black = await a_spool(ledger, label="Black", colour=BLACK)
        ledger.clock.advance(hours=1)
        ivory = await a_spool(ledger, label="Ivory", colour=IVORY)
        opens = await an_adjustment(ledger, black, "-120", "printed the lamp shade")
        await an_adjustment(ledger, ivory, "-130", "printed the lamp base")
        await an_adjustment(ledger, black, "-2", "purged the lamp nozzle")
        await an_adjustment(ledger, black, "-140", "sanded the bracket")

        lines = await ledger.use_cases.queries.movement_history(
            criteria=MovementFilter(
                since=opens,
                colours=frozenset({BLACK}),
                min_magnitude=Grams.of(50),
                search="lamp",
            )
        )

        assert notes(lines) == ["printed the lamp shade"]

    async def test_a_filter_nothing_matches_is_an_empty_history(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await an_adjustment(ledger, spool_id, "-100", "lamp shade")

        lines = await ledger.use_cases.queries.movement_history(
            criteria=MovementFilter(search="a phrase nobody wrote")
        )

        assert lines == []

    async def test_the_limit_takes_the_newest_of_what_matched_not_of_the_ledger(
        self, ledger: Ledger
    ) -> None:
        """Why the filters are SQL and not Python, in one scenario.

        Three later entries match nothing. A read that took the newest few and filtered
        afterwards would answer with an empty history over a ledger that plainly contains
        what was asked for — and it would do it silently, more often the longer the ledger
        got.
        """
        spool_id = await a_spool(ledger)
        for reason in ("keep first", "keep second", "keep third"):
            await an_adjustment(ledger, spool_id, "-1", reason)
        for reason in ("drop one", "drop two", "drop three"):
            await an_adjustment(ledger, spool_id, "-1", reason)

        lines = await ledger.use_cases.queries.movement_history(
            limit=2, criteria=MovementFilter(search="keep")
        )

        assert notes(lines) == ["keep third", "keep second"]
