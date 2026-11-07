from dataclasses import replace
from pathlib import Path

from custom_components.filament_ledger.application.adjust_spool import AdjustSpoolCommand
from custom_components.filament_ledger.domain.event import LowStockCrossed
from custom_components.filament_ledger.domain.value.grams import Grams

from .conftest import Ledger
from .test_registration_experience import command


async def test_once_per_crossing_and_exact_boundary_rearms(ledger: Ledger) -> None:
    spool_id = await ledger.use_cases.register_spool.execute(
        replace(
            command(), opening_weight=Grams.of(100), opening_basis="DECLARED_NET", gross_weight=None
        )
    )
    assert ledger.events.of(LowStockCrossed) == []

    async def change(grams: int) -> None:
        await ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id, Grams.of(grams), "Measured correction")
        )

    await change(-1)
    assert len(ledger.events.of(LowStockCrossed)) == 1
    for _ in range(10):
        await change(-1)
    assert len(ledger.events.of(LowStockCrossed)) == 1
    await change(11)
    await change(-1)
    events = ledger.events.of(LowStockCrossed)
    assert len(events) == 2
    assert events[0].occurrence_id != events[1].occurrence_id
    detail = await ledger.use_cases.queries.detail(spool_id)
    assert detail.summary.spool.last_low_stock_occurrence_id == events[-1].occurrence_id
    assert not detail.summary.spool.low_stock_armed


async def test_sealed_first_use_and_initial_low_registration(ledger: Ledger) -> None:
    spool_id = await ledger.use_cases.register_spool.execute(
        replace(
            command(),
            opening_condition="new",
            opening_weight=Grams.of(100),
            opening_basis="DECLARED_NET",
            gross_weight=None,
        )
    )
    await ledger.use_cases.adjust_spool.execute(
        AdjustSpoolCommand(spool_id, Grams.of(-1), "First use")
    )
    assert len(ledger.events.of(LowStockCrossed)) == 1
    detail = await ledger.use_cases.queries.detail(spool_id)
    assert detail.summary.spool.opened_at is not None


async def test_threshold_edit_and_open_rebase_without_fictional_crossing(ledger: Ledger) -> None:
    spool_id = await ledger.use_cases.register_spool.execute(
        replace(
            command(), opening_weight=Grams.of(150), opening_basis="DECLARED_NET", gross_weight=None
        )
    )
    await ledger.use_cases.edit_spool_details.execute(spool_id, low_stock_threshold=Grams.of(200))
    assert (await ledger.use_cases.queries.detail(spool_id)).summary.is_low_stock
    await ledger.use_cases.adjust_spool.execute(
        AdjustSpoolCommand(spool_id, Grams.of(-1), "Still below changed threshold")
    )
    assert not ledger.events.of(LowStockCrossed)
    await ledger.use_cases.adjust_spool.execute(
        AdjustSpoolCommand(spool_id, Grams.of(51), "Recovery")
    )
    await ledger.use_cases.adjust_spool.execute(
        AdjustSpoolCommand(spool_id, Grams.of(-1), "Crossing")
    )
    assert len(ledger.events.of(LowStockCrossed)) == 1


async def test_commit_publication_gap_keeps_quantity_and_latch_without_replay(
    ledger: Ledger,
) -> None:
    import pytest

    from custom_components.filament_ledger.domain.event import DomainEvent

    class InterruptedBus:
        async def publish(self, event: DomainEvent) -> None:
            raise RuntimeError("Interrupted after commit")

    spool_id = await ledger.use_cases.register_spool.execute(
        replace(
            command(), opening_weight=Grams.of(100), opening_basis="DECLARED_NET", gross_weight=None
        )
    )
    stock = ledger.use_cases.adjust_spool.low_stock
    assert stock is not None
    interrupted = replace(
        ledger.use_cases.adjust_spool, low_stock=replace(stock, events=InterruptedBus())
    )
    with pytest.raises(RuntimeError, match="after commit"):
        await interrupted.execute(AdjustSpoolCommand(spool_id, Grams.of(-1), "Crossing"))
    detail = await ledger.use_cases.queries.detail(spool_id)
    assert detail.summary.balance == Grams.of(99)
    assert not detail.summary.spool.low_stock_armed
    assert detail.summary.spool.last_low_stock_occurrence_id is not None
    async with ledger.database:
        await stock.baseline(spool_id)
    await ledger.use_cases.adjust_spool.execute(
        AdjustSpoolCommand(spool_id, Grams.of(-1), "Continuing shortage")
    )
    assert ledger.events.of(LowStockCrossed) == []


async def test_reconciliation_partial_discard_and_whole_retirement(ledger: Ledger) -> None:
    from custom_components.filament_ledger.application.adjust_spool import (
        DiscardFilamentCommand,
        DiscardMode,
    )
    from custom_components.filament_ledger.application.reconcile_spool import ReconcileSpoolCommand

    from .test_ledger import a_spool

    measured = await a_spool(ledger)
    partial = await a_spool(ledger)
    retired = await a_spool(ledger)
    await ledger.use_cases.reconcile_spool.execute(
        ReconcileSpoolCommand(measured, Grams.of(99), includes_core=False)
    )
    await ledger.use_cases.discard_filament.execute(
        DiscardFilamentCommand(partial, DiscardMode.PARTIAL, "Waste", Grams.of(901))
    )
    await ledger.use_cases.discard_filament.execute(
        DiscardFilamentCommand(retired, DiscardMode.WHOLE_SPOOL, "Retired")
    )
    assert {e.spool_id for e in ledger.events.of(LowStockCrossed)} == {measured, partial}


async def test_reassignment_rearms_source_and_warns_target(ledger: Ledger) -> None:
    from custom_components.filament_ledger.application.reassign_movement import (
        ReassignMovementCommand,
    )

    from .test_ledger import a_spool

    source = await a_spool(ledger)
    target = await a_spool(ledger)
    await ledger.use_cases.adjust_spool.execute(
        AdjustSpoolCommand(source, Grams.of(-901), "Charge")
    )
    charge = next(
        line.movement
        for line in (await ledger.use_cases.queries.detail(source)).lines
        if line.movement.amount.is_negative
    )
    await ledger.use_cases.reassign_movement.execute(ReassignMovementCommand(charge.id, target))
    assert {e.spool_id for e in ledger.events.of(LowStockCrossed)} == {source, target}
    assert (await ledger.use_cases.queries.detail(source)).summary.spool.low_stock_armed


async def test_void_recovery_and_reinstatement_are_separate_crossings(ledger: Ledger) -> None:
    from custom_components.filament_ledger.application.void_movement import VoidMovementCommand

    from .test_ledger import a_spool

    spool_id = await a_spool(ledger)
    await ledger.use_cases.adjust_spool.execute(
        AdjustSpoolCommand(spool_id, Grams.of(-901), "Charge")
    )
    charge = next(
        line.movement
        for line in (await ledger.use_cases.queries.detail(spool_id)).lines
        if line.movement.amount.is_negative
    )
    await ledger.use_cases.void_movement.execute(VoidMovementCommand(charge.id))
    assert len(ledger.events.of(LowStockCrossed)) == 1
    await ledger.use_cases.restore_movement.execute(charge.id)
    assert len(ledger.events.of(LowStockCrossed)) == 2


async def test_print_and_approved_review_crossings(ledger: Ledger) -> None:
    from custom_components.filament_ledger.application.review_queue import ApproveReviewCommand

    from .conftest import a_tray
    from .test_ledger import a_spool
    from .test_record_print_consumption import ran_to_completion
    from .test_review_queue import a_job, opened

    first = await a_spool(ledger)
    await ledger.use_cases.mount_spool.execute(first, a_tray(1))
    await ran_to_completion(ledger, {a_tray(1): Grams.of(901)})
    assert len(ledger.events.of(LowStockCrossed)) == 1
    second = await a_spool(ledger)
    await ledger.use_cases.mount_spool.execute(second, a_tray(1))
    review_id = await opened(ledger, a_job(), amounts={a_tray(1): Grams.of(901)})
    await ledger.use_cases.approve_review.execute(
        ApproveReviewCommand(review_id, amounts={a_tray(1): Grams.of(901)})
    )
    assert {e.spool_id for e in ledger.events.of(LowStockCrossed)} == {first, second}


async def test_published_occurrence_does_not_repeat_after_database_restart(
    ledger: Ledger, tmp_path: Path
) -> None:
    from custom_components.filament_ledger.infrastructure.persistence.database import run_inline

    from .conftest import build_ledger
    from .test_ledger import a_spool

    spool_id = await a_spool(ledger)
    await ledger.use_cases.adjust_spool.execute(
        AdjustSpoolCommand(spool_id, Grams.of(-901), "Crossing")
    )
    [published] = ledger.events.of(LowStockCrossed)
    await ledger.database.close()
    restarted = await build_ledger(tmp_path, run_inline)
    try:
        assert restarted.events.of(LowStockCrossed) == []
        assert (
            await restarted.use_cases.queries.detail(spool_id)
        ).summary.spool.last_low_stock_occurrence_id == published.occurrence_id
        await restarted.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id, Grams.of(-1), "Still below")
        )
        assert restarted.events.of(LowStockCrossed) == []
    finally:
        await restarted.database.close()
