"""Time, as a port.

A port so that time-dependent rules — confidence windows, review ages — are testable
without sleeping or patching system time.

**Synchronous, deliberately.** Reading a clock is not I/O, and making it a coroutine would
force every pure calculation that needs a timestamp to become one. See
docs/adr/0005-async-io-ports.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current instant, timezone-aware and in UTC."""
        ...
