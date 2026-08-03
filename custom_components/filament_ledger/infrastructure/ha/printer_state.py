"""The read-only printer glance (docs/14 §14.5).

The gateway already reads the printer's state to drive the ledger; the owner had no
surface that *shows* it beside the inventory it feeds. This module is that surface's
server half — what is printing, how far along, which tray is feeding — and nothing more.
Printer *control* stays a non-goal (N1, docs/01 §1.3): `ha-bambulab` has its own cards,
and duplicating them adds risk with no benefit.

**Reading writes nothing.** The per-slot shape is computed with `slot_outcome`, the same
repository reads the sync pass performs, *without* running `DetectSpool` first: a tab that
mutated the ledger by being looked at would violate the reader's reasonable model of "just
looking". The sync button on the Inventory tab remains the one mutation path.

**No new polling.** The ledger is push-shaped; this reader answers with current entity
state when it is called, and the panel calls it on opening the tab and on an explicit
Refresh. A glance has a moment, and the moment is the user's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...domain.port.repositories import SpoolRepository
from .bambu_gateway import BambuLabGateway, JobStatus
from .tray_sync import SlotSyncOutcome, slot_outcome


@dataclass(frozen=True, slots=True)
class PrinterSnapshot:
    """One glance at the printer, as of the moment it was asked.

    `dormant` is the honest no-printer flag, and it is the *whole* answer when it is set:
    the panel renders the teaching empty state rather than a spinner or four invented
    trays. Everything else is nullable, and null always means *the printer did not say*.
    """

    dormant: bool
    job: JobStatus | None = None
    # The three sensors docs/14 §14.5 names beyond the discovered set. They stay `None`
    # until their upstream `translation_key`s are read off the reference instance and
    # frozen into `PRINT_SENSOR_KEYS` — see `FUTURE_PRINT_SENSOR_KEYS` for why a guessed
    # key is worse than an honest null.
    online: bool | None = None
    connection_mode: str | None = None
    active_tray: int | None = None
    trays: list[SlotSyncOutcome] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReadPrinterState:
    """Assemble one snapshot from the gateway and the ledger.

    Constructed once in the composition root and held on the runtime, so the websocket
    command reads through exactly the gateway startup wired — not a re-creation of it.
    """

    gateway: BambuLabGateway
    spools: SpoolRepository

    async def execute(self) -> PrinterSnapshot:
        if not self.gateway.discovered:
            return PrinterSnapshot(dormant=True)
        trays = [
            await slot_outcome(self.spools, reading)
            for reading in (await self.gateway.current_trays()).values()
        ]
        return PrinterSnapshot(
            dormant=False,
            job=self.gateway.current_job_status(),
            trays=trays,
        )
