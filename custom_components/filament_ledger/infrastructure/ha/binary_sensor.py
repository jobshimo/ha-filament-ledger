"""Per-physical-spool current shortage; occurrences travel on the event bus."""

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ...application.query import SpoolSummary
from ...const import DOMAIN
from ...sensor import LedgerEntity
from .runtime import LedgerConfigEntry, LedgerRuntime


async def async_setup_entry(
    hass: HomeAssistant, entry: LedgerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_spools() -> None:
        snapshot = runtime.coordinator.data
        fresh = [s.spool.id for s in snapshot.spools if s.spool.id not in known] if snapshot else []
        known.update(fresh)
        async_add_entities([SpoolLowStockSensor(runtime, entry, spool_id) for spool_id in fresh])

    add_new_spools()
    entry.async_on_unload(runtime.coordinator.async_add_listener(add_new_spools))


class SpoolLowStockSensor(LedgerEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "low_stock"
    _attr_icon = "mdi:spool"

    def __init__(self, runtime: LedgerRuntime, entry: LedgerConfigEntry, spool_id: str) -> None:
        super().__init__(runtime, entry)
        self._spool_id = spool_id
        self._attr_unique_id = f"{DOMAIN}_{spool_id}_low_stock"

    @property
    def _summary(self) -> SpoolSummary | None:
        return next((s for s in self.summaries if s.spool.id == self._spool_id), None)

    @property
    def available(self) -> bool:
        summary = self._summary
        return summary is not None and summary.spool.is_in_inventory

    @property
    def is_on(self) -> bool | None:
        summary = self._summary
        return summary.is_low_stock if self.available and summary else None

    @property
    def device_info(self) -> DeviceInfo:
        summary = self._summary
        return DeviceInfo(
            identifiers={(DOMAIN, self._spool_id)},
            name=summary.spool.display_name if summary else "Spool",
            via_device=(DOMAIN, self._entry.entry_id),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        summary = self._summary
        return {
            "spool_id": self._spool_id,
            "threshold_g": summary.effective_low_stock_threshold_mg / 1000 if summary else None,
            "eligible": summary is not None and summary.state.value in ("ACTIVE", "DEPLETED"),
        }
