"""The reconciliation pass — at startup, and on demand from the panel.

The printer does not replay what happened while Home Assistant was off (the port's own
contract), so drift accumulates in the dark: a spool loaded by hand, a reel swapped. The
composition root runs this pass once at startup; the `filament_ledger/trays/sync` command
and the `sync_trays` service run the same pass whenever the user doubts the ledger.
`DetectSpool` is idempotent, so replaying an unchanged tray writes nothing.

This lives in `infrastructure/ha` because the pass needs the gateway, and the application
layer may not import Home Assistant. `DetectSpool` itself is HA-free — only the
orchestration sits with the gateway wiring.

The outcome is *derived by re-reading state*, not returned by `DetectSpool`. The use
case deliberately returns nothing — its results are ledger writes and domain events —
and giving it a return contract for one caller would couple every other caller to it.
The repositories already hold the answer; asking them is one query per occupied slot,
bounded at four.

Each slot's outcome is read back immediately after that slot's `DetectSpool` run — one
loop, not a mutation pass followed by a reporting pass — so a live tray event landing
mid-sync can stale only the slot it interleaves with, the same per-slot granularity the
live event path already has. That residual window stays open on purpose: `DetectSpool`
owns its unit of work, and holding the database lock across the whole pass to buy an
atomic strip would block live tray events for the pass's duration. The strip is a
snapshot, not a receipt — the panel refreshes right after it anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ...application.detect_spool import DetectSpool
from ...domain.model.spool import Spool
from ...domain.port.repositories import SpoolRepository
from ...domain.value.location import AmsSlot
from ...domain.value.tray_reading import TrayReading
from .bambu_gateway import BambuLabGateway


class SlotSyncStatus(StrEnum):
    """What one tray looks like once the pass has run.

    `DETECTED` is the auto-mount-off shape: the tag resolved to exactly one spool, but
    the user asked the system not to move spools, so nothing occupies the slot in the
    ledger. Reporting it as `MOUNTED` would be a lie; hiding it would waste the sighting.
    """

    EMPTY = "empty"
    MOUNTED = "mounted"
    DETECTED = "detected"
    UNKNOWN_TAG = "unknown_tag"
    AMBIGUOUS_TAG = "ambiguous_tag"
    NO_TAG = "no_tag"


@dataclass(frozen=True, slots=True)
class SlotSyncOutcome:
    """One tray's answer: the reading (the tray reference, the tag and the register-form
    hints), the status, and — for `MOUNTED` and `DETECTED` — the spool the ledger
    resolved."""

    reading: TrayReading
    status: SlotSyncStatus
    spool: Spool | None


@dataclass(frozen=True, slots=True)
class TraySyncResult:
    """`dormant` is the honest no-printer flag: an empty slot list with it unset means
    the printer reported no usable trays right now, which is a different fact from there
    being no printer to ask."""

    dormant: bool
    slots: list[SlotSyncOutcome]


@dataclass(frozen=True, slots=True)
class TraySync:
    """Run `DetectSpool` over every tray the gateway currently sees, then report.

    Constructed once in the composition root and held on the runtime, so the websocket
    command and the service run exactly the wiring startup ran — not a re-creation of it.
    """

    gateway: BambuLabGateway
    detect_spool: DetectSpool
    spools: SpoolRepository

    async def execute(self) -> TraySyncResult:
        if self.gateway.dormant:
            return TraySyncResult(dormant=True, slots=[])
        slots: list[SlotSyncOutcome] = []
        for reading in (await self.gateway.current_trays()).values():
            await self.detect_spool.execute(reading)
            slots.append(await slot_outcome(self.spools, reading))
        return TraySyncResult(dormant=False, slots=slots)


async def slot_outcome(spools: SpoolRepository, reading: TrayReading) -> SlotSyncOutcome:
    """Mirror `DetectSpool`'s branches by asking the repositories what is true now.

    The occupant read settles `MOUNTED`: with auto-mount on, the pass just put the
    resolved spool there; with auto-mount off, whatever the user mounted by hand is
    still the ledger's honest answer for the tray.

    **Reads only.** A module function rather than a method of `TraySync` because the
    Printer tab computes the same per-slot shape *without* running `DetectSpool` first
    (docs/14 §14.5) — a tab that mutated the ledger by being looked at would violate the
    reader's reasonable model of "just looking", and giving that path its own copy of
    these five branches would let the two drift apart.
    """
    if reading.empty:
        return SlotSyncOutcome(reading=reading, status=SlotSyncStatus.EMPTY, spool=None)
    if reading.tag is None:
        # Occupied, tag unreadable: nothing automatic is possible (UC-02/UC-03), and
        # naming whatever the ledger has in the tray would dress a guess as a match.
        return SlotSyncOutcome(reading=reading, status=SlotSyncStatus.NO_TAG, spool=None)
    candidates = await spools.find_by_tag(reading.tag)
    if not candidates:
        return SlotSyncOutcome(reading=reading, status=SlotSyncStatus.UNKNOWN_TAG, spool=None)
    if len(candidates) > 1:
        return SlotSyncOutcome(reading=reading, status=SlotSyncStatus.AMBIGUOUS_TAG, spool=None)
    occupant = await spools.find_by_location(AmsSlot(reading.tray))
    if occupant is None:
        return SlotSyncOutcome(reading=reading, status=SlotSyncStatus.DETECTED, spool=candidates[0])
    return SlotSyncOutcome(reading=reading, status=SlotSyncStatus.MOUNTED, spool=occupant)
