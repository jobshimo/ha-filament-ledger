"""What one configured Filament Ledger owns while Home Assistant is running.

Everything is constructed once, in the composition root, and handed here. Nothing in this
object constructs a dependency of its own — which is precisely what makes every use case
testable with in-memory fakes and no patching.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from ...application.query import LedgerSnapshot
from ...application.use_cases import UseCases
from ...const import DOMAIN
from ..persistence.database import Database
from .tray_sync import TraySync


@dataclass
class LedgerRuntime:
    database: Database
    use_cases: UseCases
    coordinator: DataUpdateCoordinator[LedgerSnapshot]
    # The printer gateway's `detach`, held here so `async_unload_entry` can stop tray
    # events *before* it closes the database below them — `async_on_unload` callbacks run
    # only after unload returns, which is too late for that ordering.
    detach_printer: CALLBACK_TYPE
    default_opening_weight_g: int
    default_core_weight_g: int
    known_spool_ids: set[str] = field(default_factory=set)
    # The on-demand reconciliation pass, wired by the composition root. `None` only in
    # test harnesses that install no printer; production always constructs one, and a
    # printerless install answers through the gateway's own dormant flag instead.
    sync_trays: TraySync | None = None

    async def async_refresh(self) -> None:
        await self.coordinator.async_request_refresh()

    async def async_close(self) -> None:
        await self.database.close()


type LedgerConfigEntry = ConfigEntry[LedgerRuntime]


def runtimes(hass: HomeAssistant) -> list[LedgerRuntime]:
    """Every set-up Filament Ledger entry.

    v1 targets a single ledger. The list is what the websocket layer reads, so supporting
    more later is a change in one place rather than everywhere.
    """
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]
