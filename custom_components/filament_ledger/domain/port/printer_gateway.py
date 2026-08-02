"""The printer, as a port.

The only knowledge the domain has of a printer is this interface and the `TrayReading`
value it speaks. Which integration provides the data, which entities carry it, and what
the sixteen-zero tag sentinel means are all boundary concerns — translated by the adapter
before anything crosses this line. See docs/02-domain-model.md §2 and
docs/05-ha-integration.md §5.8.

Exactly the surface docs/02 specifies, and nothing more. Job lifecycle events arrive with
UC-04; adding them here today would be a guess about a shape no fixture has confirmed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from ..value.identifiers import SlotIndex
from ..value.tray_reading import TrayReading

# A tray change, delivered to whoever subscribed. Awaitable because the receiving use case
# is a read-compute-write sequence against the repositories (ADR-0005).
TrayListener = Callable[[TrayReading], Awaitable[None]]


class PrinterGateway(Protocol):
    def subscribe(self, listener: TrayListener) -> None:
        """Register a callback for tray changes. Registration itself does no I/O."""
        ...

    async def current_trays(self) -> dict[SlotIndex, TrayReading]:
        """Every tray as last reported, keyed by slot.

        Exists so a restart can reconcile the ledger with reality instead of waiting for
        the next change: the printer does not replay what happened while nobody listened.
        """
        ...
