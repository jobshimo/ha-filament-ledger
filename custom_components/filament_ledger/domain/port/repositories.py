"""Persistence ports.

Interfaces the domain defines and infrastructure implements. Dependencies point inward: the
domain never imports an adapter.

Every method that performs I/O is `async`. Home Assistant runs on asyncio and SQLite work is
dispatched to an executor, so the boundary is a fact of the host rather than a preference —
see docs/adr/0005-async-io-ports.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..model.movement import Movement
from ..model.pending_review import PendingReview
from ..model.print_job import PrintJob
from ..model.spool import Spool
from ..value.identifiers import PrintJobId, ReviewId, SpoolId, TagUid
from ..value.location import Location


@dataclass(frozen=True, slots=True)
class SpoolFilter:
    """Query criteria for listing spools. All fields optional; all combine with AND."""

    include_discarded: bool = False
    mounted_only: bool = False
    search: str | None = None


class SpoolRepository(Protocol):
    async def get(self, spool_id: SpoolId) -> Spool | None: ...

    async def find_by_tag(self, tag: TagUid) -> list[Spool]:
        """Every non-discarded spool carrying this tag.

        Returns a **list**, not an optional single spool. A Bambu tag identifies a product
        batch rather than a physical unit, so two spools may legitimately carry the same
        payload; a port returning one would force the adapter to pick, silently, and deduct
        from a spool the user never loaded. The port returns what is true and the use case
        decides what to do with an ambiguous answer — which is to ask.
        """
        ...

    async def find_by_location(self, location: Location) -> Spool | None: ...

    async def list(self, criteria: SpoolFilter) -> list[Spool]: ...

    async def save(self, spool: Spool) -> None: ...


class MovementRepository(Protocol):
    """Deliberately exposes no `update` and no `delete`.

    Immutability is enforced by the shape of the interface, not by a comment asking
    politely. A rule that can only be broken by changing the interface is a rule that holds.
    """

    async def append(self, movement: Movement) -> None: ...

    async def list_for_spool(self, spool_id: SpoolId) -> list[Movement]:
        """Every movement for a spool, oldest first."""
        ...

    async def list_recent(self, limit: int) -> list[Movement]:
        """The newest `limit` movements across every spool, newest first.

        Newest first because that is the only order the global history view reads: the
        per-spool queries stay oldest-first, the order a running balance is derived in,
        and no balance is derivable from a cross-spool slice anyway.
        """
        ...

    async def list_since(self, spool_id: SpoolId, moment: datetime) -> list[Movement]: ...

    async def count_for_spool(self, spool_id: SpoolId) -> int: ...


class PrintJobRepository(Protocol):
    """Jobs are upserted, not appended: a job's state and counters evolve as the printer
    reports, and `save` reflects whatever is currently claimed. The ledger's immutability
    lives in `movement`, not here — a job is a report, a movement is a fact."""

    async def get(self, job_id: PrintJobId) -> PrintJob | None: ...

    async def save(self, job: PrintJob) -> None: ...

    async def list_recent(self, limit: int) -> list[PrintJob]: ...


class ReviewRepository(Protocol):
    """Deliberately no `find_pending_for_job`: `list_pending` returns the whole open queue,
    which every caller — the panel, the sensor, UC-05's one-per-job guard — already wants,
    and the queue is human-sized by construction. A narrower query would be a second code
    path to keep honest for no measurable gain."""

    async def get(self, review_id: ReviewId) -> PendingReview | None: ...

    async def list_pending(self) -> list[PendingReview]: ...

    async def save(self, review: PendingReview) -> None: ...
