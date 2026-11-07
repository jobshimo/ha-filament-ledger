from dataclasses import replace

from custom_components.filament_ledger.domain.value.grams import Grams

from .conftest import Ledger
from .test_registration_experience import command


async def test_and_filters_and_stable_ties_keep_low_stock_separate(ledger: Ledger) -> None:
    first = await ledger.use_cases.register_spool.execute(
        replace(command(), request_id=None, actor_id=None, payload_fingerprint=None)
    )
    second = await ledger.use_cases.register_spool.execute(
        replace(
            command(),
            request_id=None,
            actor_id=None,
            payload_fingerprint=None,
            vendor="Other",
            opening_basis="DECLARED_NET",
            gross_weight=None,
            opening_weight=Grams.of(80),
        )
    )
    queries = ledger.use_cases.queries
    assert [
        s.spool.id
        for s in await queries.inventory(search="CUSTOM", material="PLA", location="STORAGE")
    ] == [first]
    assert [s.spool.id for s in await queries.inventory(low_stock_only=True)] == [second]
    assert [s.spool.id for s in await queries.inventory(sort="remaining")] == [second, first]
    assert await queries.inventory(search="Custom", low_stock_only=True) == []
    assert len(await queries.overview()) == 2


async def test_hundred_spool_fixture_and_combined_query_budget(ledger: Ledger) -> None:
    import json
    from pathlib import Path
    from time import perf_counter

    from custom_components.filament_ledger.domain.value.colour import Colour
    from custom_components.filament_ledger.domain.value.material import Material, MaterialKind

    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/filament_experience.json").read_text()
    )
    for row in fixture["spools"]:
        await ledger.use_cases.register_spool.execute(
            replace(
                command(),
                request_id=None,
                actor_id=None,
                payload_fingerprint=None,
                label=row["label"],
                vendor=row["vendor"],
                material=Material.of(MaterialKind(row["material"])),
                colour=Colour.parse(row["colour"] or "000000"),
                colour_known=row["colour"] is not None,
                opening_weight=Grams.of(row["opening_g"]),
                opening_basis="DECLARED_NET",
                gross_weight=None,
            )
        )
    started = perf_counter()
    selected = await ledger.use_cases.queries.inventory(
        search="Workshop 01",
        material="PLA",
        location="STORAGE",
        low_stock_only=True,
        sort="remaining",
    )
    elapsed = (perf_counter() - started) * 1000
    assert len(selected) == 1
    assert selected[0].balance == Grams.of(60)
    assert len(await ledger.use_cases.queries.overview()) == 100
    print(
        f"100-spool combined read: {elapsed:.2f} ms (server only; excludes browser debounce/network)"
    )


async def test_unknown_and_real_black_remain_separate_consumption_groups(ledger: Ledger) -> None:
    from custom_components.filament_ledger.application.query import StatisticsPeriod
    from custom_components.filament_ledger.domain.model.movement import record
    from custom_components.filament_ledger.domain.value.colour import Colour
    from custom_components.filament_ledger.domain.value.movement_type import (
        MovementSource,
        MovementType,
    )
    from custom_components.filament_ledger.infrastructure.persistence.movement_repository import (
        SqliteMovementRepository,
    )

    for known in (True, False):
        spool_id = await ledger.use_cases.register_spool.execute(
            replace(
                command(),
                request_id=None,
                actor_id=None,
                payload_fingerprint=None,
                colour=Colour.parse("000000"),
                colour_known=known,
            )
        )
        await SqliteMovementRepository(ledger.database).append(
            record(
                spool_id=spool_id,
                type=MovementType.PRINT_CONSUMPTION,
                amount=Grams.of(-10),
                source=MovementSource.AUTOMATIC,
                occurred_at=ledger.clock.now(),
            )
        )
    result = await ledger.use_cases.queries.statistics(StatisticsPeriod.ALL_TIME)
    assert len(result.by_colour) == 2
    assert {group.colour_known for group in result.by_colour} == {True, False}
