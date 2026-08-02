"""Entities.

Entities exist so that automations, notifications, history, statistics and voice assistants
work **without this project building any of them**. That is the whole argument for a custom
integration over a standalone add-on — see docs/adr/0003-custom-integration-over-addon.md.

Every entity here is a read surface over the ledger. None of them can change a balance.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfMass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .application.query import SpoolSummary
from .const import DOMAIN
from .infrastructure.ha.runtime import LedgerConfigEntry, LedgerRuntime
from .infrastructure.ha.serialisers import (
    spool_summary,
    stock_grams,
    stock_per_material,
    whole_grams,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LedgerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data

    async_add_entities(
        [
            TotalStockSensor(runtime, entry),
            SpoolCountSensor(runtime, entry),
            NeedsWeighingSensor(runtime, entry),
        ]
    )

    @callback
    def add_new_spools() -> None:
        """A spool registered while Home Assistant is running gets its entity immediately.

        Without this, a newly registered spool would only appear after a restart — which is
        exactly the kind of "works, but not until later" behaviour that makes people stop
        trusting an integration.
        """
        summaries = runtime.coordinator.data or []
        fresh = [s for s in summaries if s.spool.id not in runtime.known_spool_ids]
        if not fresh:
            return
        runtime.known_spool_ids.update(s.spool.id for s in fresh)
        async_add_entities([SpoolSensor(runtime, entry, s.spool.id) for s in fresh])

    add_new_spools()
    entry.async_on_unload(runtime.coordinator.async_add_listener(add_new_spools))


class LedgerEntity(CoordinatorEntity[Any]):
    _attr_has_entity_name = True

    def __init__(self, runtime: LedgerRuntime, entry: LedgerConfigEntry) -> None:
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._entry = entry

    @property
    def summaries(self) -> list[SpoolSummary]:
        return list(self.coordinator.data or [])


class SpoolSensor(LedgerEntity, SensorEntity):
    """One per physical spool. State is the balance, because that is the number the user
    cares about; everything else is an attribute."""

    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, runtime: LedgerRuntime, entry: LedgerConfigEntry, spool_id: str) -> None:
        super().__init__(runtime, entry)
        self._spool_id = spool_id
        self._attr_unique_id = f"{DOMAIN}_spool_{spool_id}"
        self._attr_name = None

    @property
    def _summary(self) -> SpoolSummary | None:
        return next((s for s in self.summaries if s.spool.id == self._spool_id), None)

    @property
    def available(self) -> bool:
        return self._summary is not None

    @property
    def device_info(self) -> DeviceInfo:
        summary = self._summary
        return DeviceInfo(
            identifiers={(DOMAIN, self._spool_id)},
            name=summary.spool.display_name if summary else "Spool",
            manufacturer=(summary.spool.vendor if summary and summary.spool.vendor else None),
            model=summary.spool.material.display_name if summary else None,
            via_device=(DOMAIN, self._entry.entry_id),
        )

    @property
    def native_value(self) -> int | None:
        summary = self._summary
        return whole_grams(summary.balance) if summary else None

    @property
    def icon(self) -> str:
        return "mdi:printer-3d-nozzle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        summary = self._summary
        return spool_summary(summary) if summary else {}


class LedgerAggregateSensor(LedgerEntity, SensorEntity):
    """Aggregates hang off a device representing the ledger itself, not off any spool."""

    def __init__(
        self,
        runtime: LedgerRuntime,
        entry: LedgerConfigEntry,
        *,
        key: str,
        name: str,
        icon: str,
        value: Callable[[list[SpoolSummary]], int],
    ) -> None:
        super().__init__(runtime, entry)
        self._value = value
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Filament Ledger",
            manufacturer="Filament Ledger",
            entry_type=None,
        )

    @property
    def native_value(self) -> int:
        return self._value(self.summaries)


class TotalStockSensor(LedgerAggregateSensor):
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, runtime: LedgerRuntime, entry: LedgerConfigEntry) -> None:
        super().__init__(
            runtime,
            entry,
            key="total_stock",
            name="Total stock",
            icon="mdi:sigma",
            # The exact balances are summed and rounded once, exactly as `Queries.stock()`
            # does for the websocket — summing per-spool roundings would let the sensor
            # and the panel disagree about the same ledger.
            value=stock_grams,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"per_material": stock_per_material(self.summaries)}


class SpoolCountSensor(LedgerAggregateSensor):
    def __init__(self, runtime: LedgerRuntime, entry: LedgerConfigEntry) -> None:
        super().__init__(
            runtime,
            entry,
            key="spool_count",
            name="Spools",
            icon="mdi:database",
            value=lambda summaries: len([s for s in summaries if s.state.counts_as_stock]),
        )


class NeedsWeighingSensor(LedgerAggregateSensor):
    """Drives the "go and weigh something" prompt. A warning with no adjacent remedy is
    just noise, so the panel puts the action next to this number."""

    def __init__(self, runtime: LedgerRuntime, entry: LedgerConfigEntry) -> None:
        super().__init__(
            runtime,
            entry,
            key="needs_weighing",
            name="Needs weighing",
            icon="mdi:scale-balance",
            value=lambda summaries: len(
                [s for s in summaries if s.confidence.needs_weighing and s.state.counts_as_stock]
            ),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "spools": [
                {"id": s.spool.id, "name": s.spool.display_name}
                for s in self.summaries
                if s.confidence.needs_weighing and s.state.counts_as_stock
            ]
        }
