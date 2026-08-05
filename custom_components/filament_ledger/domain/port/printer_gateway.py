"""The printer, as a port.

The only knowledge the domain has of a printer is this interface and the values it
speaks — `TrayReading` for the AMS, `PrintEvent` for the job lifecycle. Which integration
provides the data, which entities carry it, and what the sixteen-zero tag sentinel means
are all boundary concerns — translated by the adapter before anything crosses this line.
See docs/02-domain-model.md §2 and docs/05-ha-integration.md §5.8.

Two subscription surfaces, deliberately: trays and jobs change for different reasons, are
consumed by different use cases, and a listener registered for one has no business being
woken for the other.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from ..value.identifiers import TrayRef
from ..value.print_event import PrintEvent
from ..value.tray_reading import TrayReading

# A tray change, delivered to whoever subscribed. Awaitable because the receiving use case
# is a read-compute-write sequence against the repositories (ADR-0005).
TrayListener = Callable[[TrayReading], Awaitable[None]]

# A job lifecycle moment — started, finished, cancelled, failed — already translated.
# Awaitable for the same reason as above: the receiver writes to the ledger.
PrintListener = Callable[[PrintEvent], Awaitable[None]]


class PrinterGateway(Protocol):
    def subscribe(self, listener: TrayListener) -> None:
        """Register a callback for tray changes. Registration itself does no I/O."""
        ...

    def subscribe_jobs(self, listener: PrintListener) -> None:
        """Register a callback for job lifecycle events. Registration itself does no I/O."""
        ...

    async def current_trays(self) -> dict[TrayRef, TrayReading]:
        """Every tray as last reported, keyed by the tray it describes.

        Exists so a restart can reconcile the ledger with reality instead of waiting for
        the next change: the printer does not replay what happened while nobody listened.
        """
        ...
