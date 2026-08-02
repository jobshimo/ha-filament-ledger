"""Where a spool physically is.

A spool is in exactly one location. This models the physical world truthfully: a spool
cannot be in two places, and "in storage" is a real location rather than the absence of one.
"""

from __future__ import annotations

from dataclasses import dataclass

from .identifiers import SlotIndex


@dataclass(frozen=True, slots=True)
class Storage:
    """On a shelf, not mounted."""

    def __str__(self) -> str:
        return "Storage"


@dataclass(frozen=True, slots=True)
class AmsSlot:
    """Mounted in an AMS tray."""

    slot: SlotIndex

    def __str__(self) -> str:
        return f"AMS slot {self.slot}"


@dataclass(frozen=True, slots=True)
class ExternalSpool:
    """Feeding the printer directly, bypassing the AMS."""

    def __str__(self) -> str:
        return "External spool"


Location = Storage | AmsSlot | ExternalSpool


def is_mounted(location: Location) -> bool:
    """True when the spool is loaded into the machine in any way."""
    return not isinstance(location, Storage)
