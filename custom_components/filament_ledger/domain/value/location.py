"""Where a spool physically is.

A spool is in exactly one location. This models the physical world truthfully: a spool
cannot be in two places, and "in storage" is a real location rather than the absence of one.
"""

from __future__ import annotations

from dataclasses import dataclass

from .identifiers import FIRST_HOLDER, ExternalFeed, Feed, HolderIndex, PrinterSerial, TrayRef


@dataclass(frozen=True, slots=True)
class Storage:
    """On a shelf, not mounted."""

    def __str__(self) -> str:
        return "Storage"


@dataclass(frozen=True, slots=True)
class AmsSlot:
    """Mounted in an AMS tray, named in full: printer, AMS unit, tray.

    The reference is the whole of what makes this location unique. A bare tray number
    identified a position only for as long as there was one machine to hold it — see
    `TrayRef`.
    """

    tray: TrayRef

    def __str__(self) -> str:
        # The machine is named because there can now be more than one, and this string ends
        # up in an anomaly's explanation: *loaded in AMS slot 3* stopped being an address
        # the moment a second printer arrived with an AMS slot 3 of its own. What a user
        # reads on screen is built from the parts by the panel, which drops the serial while
        # only one machine holds spools (docs/06 §6.4).
        return f"AMS slot {self.tray.slot} on printer {self.tray.printer}"


@dataclass(frozen=True, slots=True)
class ExternalSpool:
    """Feeding one printer directly, bypassing that printer's AMS.

    **Named after its machine, for the same reason a tray is.** With several machines an
    unqualified *external spool* names as many positions as there are printers — and the
    partial unique index that states *the direct feed holds one spool* (docs/08 §8.1) would
    have refused the second machine's reel to a ledger that could truthfully hold it.
    Migration 0008 widened both together.

    **And named after its holder, for the same reason again.** A dual-nozzle printer has two
    of these bolted on, so *the external spool of machine X* named two positions on one
    machine and the index refused the second reel exactly as the ledger-wide one had refused
    the second machine's. Migration 0010 widened both together, one release later, for the
    release in which the second holder is followed rather than merely physical.
    """

    printer: PrinterSerial
    holder: HolderIndex = FIRST_HOLDER

    def __str__(self) -> str:
        """The single-holder sentence, verbatim, for holder 1 — `ExternalFeed.__str__`'s
        rule, applied to the location the same words describe."""
        if self.holder == FIRST_HOLDER:
            return f"External spool on printer {self.printer}"
        return f"External spool {self.holder} on printer {self.printer}"


Location = Storage | AmsSlot | ExternalSpool


def is_mounted(location: Location) -> bool:
    """True when the spool is loaded into the machine in any way."""
    return not isinstance(location, Storage)


def location_of(feed: Feed) -> AmsSlot | ExternalSpool:
    """The mounted location a consumption position is answered by.

    A figure is keyed by where the printer drew it from; the spool it is deducted from is
    whichever one the ledger holds *at* that place. Stated once, so UC-04's deduction and
    UC-05's freeze resolve a tray and the direct feed by the same rule rather than by two
    `isinstance` ladders that drift apart (docs/02 §2.3).
    """
    if isinstance(feed, TrayRef):
        return AmsSlot(feed)
    return ExternalSpool(feed.printer, feed.holder)


def feed_of(location: AmsSlot | ExternalSpool) -> Feed:
    """The inverse: the position a mounted spool would be charged through."""
    if isinstance(location, AmsSlot):
        return location.tray
    return ExternalFeed(location.printer, location.holder)
