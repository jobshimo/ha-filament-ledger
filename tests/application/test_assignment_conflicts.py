import pytest

from custom_components.filament_ledger.application.errors import AssignmentConflictError
from custom_components.filament_ledger.domain.value.identifiers import HolderIndex
from custom_components.filament_ledger.domain.value.location import AmsSlot, ExternalSpool, Storage
from tests.application.conftest import A_PRINTER, Ledger, a_tray

from .test_ledger import a_spool


async def test_stale_occupant_refuses_displacement_and_retry_is_idempotent(ledger: Ledger) -> None:
    first = await a_spool(ledger)
    second = await a_spool(ledger)
    await ledger.use_cases.mount_spool.execute(
        first, a_tray(1), expected_source=Storage(), expected_destination_spool_id=None
    )
    count = len(ledger.events.published)
    await ledger.use_cases.mount_spool.execute(
        first, a_tray(1), expected_source=Storage(), expected_destination_spool_id=None
    )
    assert len(ledger.events.published) == count
    with pytest.raises(AssignmentConflictError):
        await ledger.use_cases.mount_spool.execute(
            second, a_tray(1), expected_source=Storage(), expected_destination_spool_id=None
        )
    assert (await ledger.use_cases.queries.detail(first)).summary.spool.location == AmsSlot(
        a_tray(1)
    )
    assert (await ledger.use_cases.queries.detail(second)).summary.movement_count == 1


async def test_source_names_complete_tray_and_both_external_holders(ledger: Ledger) -> None:
    first = await a_spool(ledger)
    await ledger.use_cases.mount_spool.execute(first, a_tray(1, ams=2))
    with pytest.raises(AssignmentConflictError):
        await ledger.use_cases.mount_spool_externally.execute(
            first,
            A_PRINTER,
            HolderIndex(2),
            expected_source=AmsSlot(a_tray(1)),
            expected_destination_spool_id=None,
        )
    await ledger.use_cases.mount_spool_externally.execute(
        first,
        A_PRINTER,
        HolderIndex(2),
        expected_source=AmsSlot(a_tray(1, ams=2)),
        expected_destination_spool_id=None,
    )
    assert (await ledger.use_cases.queries.detail(first)).summary.spool.location == ExternalSpool(
        A_PRINTER, HolderIndex(2)
    )


async def test_two_simultaneous_empty_slot_claims_have_one_winner(ledger: Ledger) -> None:
    import asyncio

    first = await a_spool(ledger)
    second = await a_spool(ledger)
    results = await asyncio.gather(
        *[
            ledger.use_cases.mount_spool.execute(
                spool_id, a_tray(3), expected_source=Storage(), expected_destination_spool_id=None
            )
            for spool_id in (first, second)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, AssignmentConflictError) for result in results) == 1
    assert sum(result is None for result in results) == 1
