from typing import cast

from custom_components.filament_ledger.application.adjust_spool import AdjustSpoolCommand
from custom_components.filament_ledger.binary_sensor import SpoolLowStockSensor
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.infrastructure.ha.runtime import LedgerConfigEntry

from .conftest import Harness, a_spool


async def test_low_stock_entity_is_stable_current_status_and_retired_unavailable(
    harness: Harness,
) -> None:
    spool_id = await a_spool(harness.ledger)
    await harness.runtime.async_refresh()
    sensor = SpoolLowStockSensor(harness.runtime, cast(LedgerConfigEntry, harness.entry), spool_id)
    assert sensor.unique_id == f"filament_ledger_{spool_id}_low_stock"

    def read_state() -> str | None:
        return sensor.state

    assert read_state() == "off"
    await harness.ledger.use_cases.adjust_spool.execute(
        AdjustSpoolCommand(spool_id, Grams.of(-901), "Quantity")
    )
    await harness.runtime.async_refresh()
    assert read_state() == "on"
    assert sensor.extra_state_attributes["spool_id"] == spool_id
    await harness.ledger.use_cases.delete_spool.execute(spool_id)
    await harness.runtime.async_refresh()
    assert not sensor.available
    assert sensor.is_on is None
