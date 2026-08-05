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
from ...domain.error import InvalidValueError
from ...domain.value.identifiers import UNIDENTIFIED_PRINTER, PrinterSerial
from ..persistence.database import Database
from .printer_state import ReadPrinterState
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
    # The read-only printer glance, wired by the composition root beside the pass above.
    # `None` only in test harnesses that install no printer; a printerless install answers
    # through the gateway's own dormant flag instead (docs/14 §14.5).
    printer: ReadPrinterState | None = None

    @property
    def tray_printer(self) -> PrinterSerial:
        """The machine a caller that named no printer means — **or a refusal.**

        The answer for a service call, an automation written before a tray had three parts,
        or any caller naming only a slot. It is the gateway's own answer, never a blind
        sentinel, and that distinction is load-bearing: with a printer discovered, the spool
        rows already carry its serial (`printer_adoption`), and defaulting to the sentinel
        instead would open a *second* tray space in which every slot looked free. Two spools
        in tray 1, with the unique index correctly seeing two different trays.

        Without a gateway there is nothing to ask, and the sentinel is exactly what those
        rows carry — so the two agree there too.

        **With more than one machine followed, the absence is refused rather than resolved.**
        There is no such thing as *the* printer then, and every way of picking one is a guess
        with somebody's spool on the other end of it. The v1 promise that an automation
        naming only a slot keeps working held because there was one machine for it to mean;
        the moment there are two, that automation is ambiguous and says so out loud instead
        of landing somewhere plausible (docs/05 §5.4, amended v2.0).
        """
        if self.printer is None:
            return UNIDENTIFIED_PRINTER
        default = self.printer.gateway.default_printer
        if default is None:
            followed = ", ".join(serial.value for serial in self.printer.gateway.printers)
            msg = (
                f"this ledger follows more than one printer ({followed}), so a tray named "
                f"by slot alone does not say which machine; name the printer"
            )
            raise InvalidValueError(msg)
        return default

    async def async_refresh(self) -> None:
        await self.coordinator.async_request_refresh()

    async def async_close(self) -> None:
        await self.database.close()


type LedgerConfigEntry = ConfigEntry[LedgerRuntime]


def loaded_entries(hass: HomeAssistant) -> list[LedgerConfigEntry]:
    """Every set-up Filament Ledger config entry.

    `runtimes` reads the runtime off these. The settings commands (docs/14 §14.6.4) need
    the entry *itself*: options live on the entry, and writing them goes through
    `hass.config_entries.async_update_entry`, which is what fires the update listener that
    reloads the ledger.
    """
    return [
        entry
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]


def runtimes(hass: HomeAssistant) -> list[LedgerRuntime]:
    """Every set-up Filament Ledger entry's runtime.

    v1 targets a single ledger. The list is what the websocket layer reads, so supporting
    more later is a change in one place rather than everywhere.
    """
    return [entry.runtime_data for entry in loaded_entries(hass)]
