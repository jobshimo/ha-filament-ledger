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
from ...domain.value.identifiers import AmsIndex, Feed, HolderIndex, PrinterSerial
from .bambu_gateway import BambuLabGateway, JobStatus
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

    **`ams` is gone since v2.9**, and its absence is the feature this time. It carried the
    one ordinal this ledger followed, ledger-wide, because there was only ever one; a
    machine now states its own units in `MachineSnapshot.ams_units`, and a single number
    beside the printer list could only have contradicted them.
    """

    printers: tuple[PrinterSerial, ...] = ()
    unnamed: int = 0


@dataclass(frozen=True, slots=True)
class HolderSnapshot:
    """One direct feed, as the printer describes it right now.

    `empty` is three-way for the reason every reading here is: *no spool* and *the sensor
    did not say* are different facts, and a card that rendered them identically would tell
    a user their reel had gone because a sensor blinked.

    `name_hint` is whatever the printer was told is on the holder. A hint, never an
    identity — most machines have no reader on the holder — so it captions a card and
    resolves nothing.
    """

    holder: HolderIndex
    empty: bool | None = None
    name_hint: str | None = None


@dataclass(frozen=True, slots=True)
class MachineSnapshot:
    """One machine's glance: what it is called, what it is printing, what its trays hold.

    Every figure is nullable and null means *the printer did not say* — the gateway's
    standing policy, applied per machine because a printer that has gone quiet says nothing
    while the one beside it goes on printing, and one dash must never spread to both.
    """

    printer: PrinterSerial
    job: JobStatus
    # What the machine calls itself, for display beside the serial. Null when neither the
    # printer's own name sensor nor the device registry said, and then the panel shows the
    # serial alone — which is what it has always shown (docs/14 §14.5).
    printer_name: str | None = None
    # The three sensors docs/14 §14.5 names beyond the job set. They waited here through
    # v1.4 and v2.5 for their upstream `translation_key`s to be *read* rather than guessed,
    # and they were read on 2026-08-11 — two of the three guesses right, `connection_mode`
    # wrong and actually `mqtt_mode`. `BambuLabGateway.MQTT_MODE_KEY` tells that story; the
    # readers behind these three are total like every other one, so a null here still means
    # exactly what it always did: the printer did not say.
    online: bool | None = None
    connection_mode: str | None = None
    # Where this machine is drawing from right now — a tray or one of its holders — or null
    # when it did not say. It replaced `active_tray: int` in v2.9, which had been a dash on
    # every machine since it was added: the sensor's state is a filament name and the
    # position lives in its attributes (`BambuLabGateway.active_feed`).
    active_feed: Feed | None = None
    # Every AMS unit this machine has, by the printer's own numbering, and every direct
    # feed an entity describes. The panel renders a block per unit and a card per holder,
    # flooring the holders at one — a machine has a holder whether or not upstream
    # published a sensor for it.
    ams_units: tuple[AmsIndex, ...] = ()
    holders: list[HolderSnapshot] = field(default_factory=list)
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
                    printer_name=self.gateway.printer_name(printer),
                    online=self.gateway.online(printer),
                    connection_mode=self.gateway.connection_mode(printer),
                    active_feed=self.gateway.active_feed(printer),
                    ams_units=self.gateway.ams_units(printer),
                    holders=self._holders(printer),
                    trays=trays.get(printer, []),
                )
                for printer in self.gateway.printers
            ],
            observed_print_time=await self.queries.observed_print_time(),
        )

    def _holders(self, printer: PrinterSerial) -> list[HolderSnapshot]:
        """One entry per direct feed the gateway discovered, in numbered order.

        Empty when upstream published no holder sensor for this machine, and the panel
        floors its own union at the first holder rather than this reader inventing one: a
        holder nobody reported is still a holder a user can mount onto, but it is not a
        holder the printer *said* anything about, and this is the reader that only repeats
        what was said.
        """
        return [
            HolderSnapshot(
                holder=holder,
                empty=self.gateway.holder_empty(printer, holder),
                name_hint=self.gateway.holder_name(printer, holder),
            )
            for holder in self.gateway.holders(printer)
        ]
