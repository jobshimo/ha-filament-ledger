"""Atomicity, as a port.

A mutating use case is a read-compute-write sequence, and the ledger's invariants only
hold if that sequence is indivisible. A crash between two writes must leave no partial
state behind — a spool with no movement that explains its balance is exactly the thing
this system exists to make impossible — and two concurrent sequences must not interleave,
or both compute from the same stale read and one of them silently corrupts the balance.

The port is an async context manager and nothing more. Entering starts the unit; a clean
exit makes every write inside it durable at once; an exceptional exit discards them all.
How is infrastructure's business — the domain states only the guarantee it needs. One
honest limit: cancellation during commit resolves to whichever outcome the engine had
already reached — durable or discarded — but the connection is always left clean, and the
unit's exclusivity ends only once the connection is quiescent again.

Domain events are published **after** the unit commits, never inside it. An event for a
write that could still roll back would announce something that never happened.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol


class UnitOfWork(Protocol):
    """One atomic, serialised unit of read-compute-write work."""

    async def __aenter__(self) -> None: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
