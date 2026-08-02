"""The real clock.

Trivial by design. It exists so that every time-dependent rule in the domain can be driven
by a fake in tests without sleeping or patching the system clock.
"""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
