"""UC-12 across every spool: the global history read model, on real SQLite.

The per-spool history is served by `Queries.detail`; `movement_history` answers the other
question — *what has been happening?* — newest first, each row joined to its spool's
display name and colour and, when the movement carries a `job_id`, to the print job's
name. These tests drive the join through the real use cases, so every assertion about a
line is an assertion about the actual ledger.
"""

from __future__ import annotations

from custom_components.filament_ledger.application.adjust_spool import (
    AdjustSpoolCommand,
    DiscardFilamentCommand,
    DiscardMode,
)
from custom_components.filament_ledger.application.register_spool import RegisterSpoolCommand
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    PrintJobId,
    SlotIndex,
    SpoolId,
)
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState

from .conftest import Ledger


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
