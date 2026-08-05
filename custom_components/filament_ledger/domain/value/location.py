"""Where a spool physically is.

A spool is in exactly one location. This models the physical world truthfully: a spool
cannot be in two places, and "in storage" is a real location rather than the absence of one.
"""

from __future__ import annotations

from dataclasses import dataclass

from .identifiers import TrayRef


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
        # Still the single-machine sentence, because the ledger still follows one machine
        # and this string is what a user reads. The reference behind it is what changed.
        return f"AMS slot {self.tray.slot}"


@dataclass(frozen=True, slots=True)
class ExternalSpool:
    """Feeding the printer directly, bypassing the AMS."""

    def __str__(self) -> str:
        return "External spool"


Location = Storage | AmsSlot | ExternalSpool


def is_mounted(location: Location) -> bool:
    """True when the spool is loaded into the machine in any way."""
    return not isinstance(location, Storage)
