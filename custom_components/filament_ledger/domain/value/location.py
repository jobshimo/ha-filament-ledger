"""Where a spool physically is.

A spool is in exactly one location. This models the physical world truthfully: a spool
cannot be in two places, and "in storage" is a real location rather than the absence of one.
"""

from __future__ import annotations

from dataclasses import dataclass

from .identifiers import PrinterSerial, TrayRef


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

    **Named after its machine, for the same reason a tray is.** Each printer has exactly
    one direct feed, so with several machines an unqualified *external spool* names as many
    positions as there are printers — and the partial unique index that states *the direct
    feed holds one spool* (docs/08 §8.1) would have refused the second machine's reel to a
    ledger that could truthfully hold it. Migration 0008 widened both together.
    """

    printer: PrinterSerial

    def __str__(self) -> str:
        return f"External spool on printer {self.printer}"


Location = Storage | AmsSlot | ExternalSpool


def is_mounted(location: Location) -> bool:
    """True when the spool is loaded into the machine in any way."""
    return not isinstance(location, Storage)
