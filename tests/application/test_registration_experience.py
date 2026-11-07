"""Registration retries use durable intent, not the defaults at retry time."""

from dataclasses import replace

import pytest

from custom_components.filament_ledger.application.errors import IdempotencyConflictError
from custom_components.filament_ledger.application.register_spool import RegisterSpoolCommand
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.spool_state import SpoolState

from .conftest import Ledger


def command() -> RegisterSpoolCommand:
    return RegisterSpoolCommand(
        material=Material(MaterialKind.PLA),
        colour=Colour.parse("123456"),
        opening_weight=Grams.of(420),
        core_weight=Grams.of(250),
        opening_basis="MEASURED_GROSS",
        gross_weight=Grams.of(670),
        opening_condition="partly_used",
        colour_known=False,
        actor_id="household-user",
        request_id="df31239e-e068-49df-a478-5c52ef12fe38",
        payload_fingerprint="a" * 64,
        vendor="Custom brand",
    )


async def test_preview_is_read_only_and_matches_committed_provenance(ledger: Ledger) -> None:
    before = await ledger.use_cases.queries.overview()
    preview = ledger.use_cases.preview_registration.execute(
        "MEASURED_GROSS", None, Grams.of(670), Grams.of(250)
    )
    assert await ledger.use_cases.queries.overview() == before
    assert ledger.events.published == []
    spool_id = await ledger.use_cases.register_spool.execute(command())
    detail = await ledger.use_cases.queries.detail(spool_id)
    assert detail.summary.balance == preview
    assert detail.summary.state is SpoolState.ACTIVE
    assert detail.summary.opening_basis == "MEASURED_GROSS"
    assert detail.summary.gross_weight_mg == 670000
    assert not detail.summary.spool.colour_known


async def test_replay_does_not_append_or_publish(ledger: Ledger) -> None:
    first = await ledger.use_cases.register_spool.execute(command())
    events = list(ledger.events.published)
    assert await ledger.use_cases.register_spool.execute(command()) == first
    assert ledger.events.published == events
    detail = await ledger.use_cases.queries.detail(first)
    assert detail.summary.movement_count == 1
    with pytest.raises(IdempotencyConflictError, match="different registration"):
        await ledger.use_cases.register_spool.execute(
            replace(command(), payload_fingerprint="b" * 64)
        )


async def test_receipt_actor_scope_and_default_change(ledger: Ledger) -> None:
    original = replace(command(), opening_basis="CONFIGURED_DEFAULT", gross_weight=None)
    first = await ledger.use_cases.register_spool.execute(original)
    assert (
        await ledger.use_cases.register_spool.execute(
            replace(original, opening_weight=Grams.of(1000))
        )
        == first
    )
    second = await ledger.use_cases.register_spool.execute(
        replace(original, actor_id="another-user")
    )
    assert second != first


async def test_mark_opened_never_adds_movement(ledger: Ledger) -> None:
    first = await ledger.use_cases.register_spool.execute(
        replace(command(), opening_condition="new")
    )
    assert ledger.use_cases.open_spool is not None
    await ledger.use_cases.open_spool.execute(first)
    await ledger.use_cases.open_spool.execute(first)
    detail = await ledger.use_cases.queries.detail(first)
    assert detail.summary.state is SpoolState.ACTIVE
    assert detail.summary.movement_count == 1


async def test_replay_after_retirement_never_restores_a_spool(ledger: Ledger) -> None:
    first = await ledger.use_cases.register_spool.execute(command())
    await ledger.use_cases.delete_spool.execute(first)
    assert await ledger.use_cases.register_spool.execute(command()) == first
    detail = await ledger.use_cases.queries.detail(first)
    assert detail.summary.state is SpoolState.DELETED
    assert detail.summary.movement_count == 1


async def test_failed_opening_write_rolls_back_spool_and_receipt(ledger: Ledger) -> None:
    from custom_components.filament_ledger.domain.model.movement import Movement
    from custom_components.filament_ledger.infrastructure.persistence.movement_repository import (
        SqliteMovementRepository,
    )

    class FailingMovements(SqliteMovementRepository):
        async def append(self, movement: Movement) -> None:
            raise RuntimeError("Opening append failed")

    failing = replace(ledger.use_cases.register_spool, movements=FailingMovements(ledger.database))
    with pytest.raises(RuntimeError, match="Opening append"):
        await failing.execute(command())
    assert await ledger.use_cases.queries.overview() == []
    assert await ledger.database.fetch_one("SELECT * FROM registration_request") is None
    assert ledger.events.published == []


async def test_backdated_consumption_cannot_replace_the_opening_basis(ledger: Ledger) -> None:
    from datetime import timedelta

    from custom_components.filament_ledger.domain.model.movement import record
    from custom_components.filament_ledger.domain.value.movement_type import (
        MovementSource,
        MovementType,
    )
    from custom_components.filament_ledger.infrastructure.persistence.movement_repository import (
        SqliteMovementRepository,
    )

    spool_id = await ledger.use_cases.register_spool.execute(command())
    await SqliteMovementRepository(ledger.database).append(
        record(
            spool_id=spool_id,
            type=MovementType.PRINT_CONSUMPTION,
            amount=Grams.of(-1),
            source=MovementSource.AUTOMATIC,
            occurred_at=ledger.clock.now() - timedelta(days=1),
            recorded_at=ledger.clock.now(),
        )
    )
    summary = (await ledger.use_cases.queries.detail(spool_id)).summary
    assert summary.opening_basis == "MEASURED_GROSS"
    assert summary.gross_weight_mg == 670000
