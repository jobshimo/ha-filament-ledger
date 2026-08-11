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

from ...application.query import ObservedPrintTime, Queries
from ...domain.port.repositories import SpoolRepository
from ...domain.value.identifiers import AmsIndex, PrinterSerial
from .bambu_gateway import TRACKED_AMS, BambuLabGateway, JobStatus
from .tray_sync import SlotSyncOutcome, slot_outcome


@dataclass(frozen=True, slots=True)
class PrinterTracking:
    """Which machines this ledger follows, and how many it could not name.

    Identity, not measurement — which is why this rides beside `dormant` while every figure
    in the snapshot below does not. A ledger with no printer still has a tray space to mount
    spools into and the panel has to be able to name it; a hull of nulls would invite dashes
    for a printer that is not there, and a missing tray space would leave the AMS view
    guessing.

    `printers` is empty when discovery named nobody — *no machine was identified*, which is
    a different statement from *one machine called UNIDENTIFIED* and is rendered as the
    teaching empty state rather than as a section. The mount command resolves that absence
    server-side, in the one place that knows what an unidentified printer is called
    (`LedgerRuntime.tray_printer`).

    `unnamed` replaces v1.4's `ignored`, and the replacement is the feature: every machine
    with a readable serial is now followed, so the only thing left to report is a machine
    whose serial could not be read — see `BambuLabGateway.unnamed_printers`.
    """

    printers: tuple[PrinterSerial, ...] = ()
    ams: AmsIndex = TRACKED_AMS
    unnamed: int = 0


@dataclass(frozen=True, slots=True)
class MachineSnapshot:
    """One machine's glance: what it is called, what it is printing, what its trays hold.

    Every figure is nullable and null means *the printer did not say* — the gateway's
    standing policy, applied per machine because a printer that has gone quiet says nothing
    while the one beside it goes on printing, and one dash must never spread to both.
    """

    printer: PrinterSerial
    job: JobStatus
    # The three sensors docs/14 §14.5 names beyond the job set. They waited here through
    # v1.4 and v2.5 for their upstream `translation_key`s to be *read* rather than guessed,
    # and they were read on 2026-08-11 — two of the three guesses right, `connection_mode`
    # wrong and actually `mqtt_mode`. `BambuLabGateway.MQTT_MODE_KEY` tells that story; the
    # readers behind these three are total like every other one, so a null here still means
    # exactly what it always did: the printer did not say.
    online: bool | None = None
    connection_mode: str | None = None
    active_tray: int | None = None
    trays: list[SlotSyncOutcome] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PrinterSnapshot:
    """One glance at every followed machine, as of the moment it was asked.

    `dormant` is the honest no-printer flag, and — bar `tracking`, which says why beside
    itself — it is the *whole* answer when it is set: the panel renders the teaching empty
    state rather than a spinner or four invented trays.

    `machines` carries one entry per followed printer, in the tracking order, because the
    tab renders a section each (docs/14 §14.5, amended v2.0). A household with one machine
    gets a one-element list and a tab that reads exactly as it always has.

    `observed_print_time` is the one figure here that no printer said at all, and it sits
    **outside** `machines` deliberately: it is this ledger's own sum over the jobs it has
    recorded, and every row written before migration 0008 names no machine — so splitting
    the total per printer would file real hours under a heading nobody could read. One
    total, and the sentence bounding it names the ledger rather than a machine.
    """

    dormant: bool
    tracking: PrinterTracking = PrinterTracking()
    machines: list[MachineSnapshot] = field(default_factory=list)
    observed_print_time: ObservedPrintTime | None = None


@dataclass(frozen=True, slots=True)
class ReadPrinterState:
    """Assemble one snapshot from the gateway and the ledger.

    Constructed once in the composition root and held on the runtime, so the websocket
    command reads through exactly the gateway startup wired — not a re-creation of it.
    """

    gateway: BambuLabGateway
    spools: SpoolRepository
    # The accumulated-hours total is a read model, not a sensor, so it comes from the
    # layer that owns aggregation. Summing job rows here instead would put a second
    # accumulator in the one place `PrintTime.of` exists to prevent (docs/14 §14.5).
    queries: Queries

    async def execute(self) -> PrinterSnapshot:
        # Tracking is answered on both branches. A dormant gateway still has a tray space
        # — the one it would mount into — and a machine found but unnameable is exactly the
        # fact worth reporting when nothing else resolved.
        tracking = PrinterTracking(
            printers=self.gateway.printers,
            unnamed=self.gateway.unnamed_printers,
        )
        if not self.gateway.discovered:
            return PrinterSnapshot(dormant=True, tracking=tracking)
        # One pass over the readings, grouped by the printer each tray names. `TrayRef`
        # orders by printer first, so the grouping falls out of the order the gateway
        # already hands them over in rather than needing a sort of its own.
        trays: dict[PrinterSerial, list[SlotSyncOutcome]] = {}
        for reading in (await self.gateway.current_trays()).values():
            trays.setdefault(reading.tray.printer, []).append(
                await slot_outcome(self.spools, reading)
            )
        return PrinterSnapshot(
            dormant=False,
            tracking=tracking,
            machines=[
                MachineSnapshot(
                    printer=printer,
                    job=self.gateway.current_job_status(printer),
                    online=self.gateway.online(printer),
                    connection_mode=self.gateway.connection_mode(printer),
                    active_tray=self.gateway.active_tray(printer),
                    trays=trays.get(printer, []),
                )
                for printer in self.gateway.printers
            ],
            observed_print_time=await self.queries.observed_print_time(),
        )
