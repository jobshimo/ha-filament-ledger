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
from ..model.spool import Spool
from ..value.identifiers import SpoolId, TagUid
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

    async def list_since(self, spool_id: SpoolId, moment: datetime) -> list[Movement]: ...

    async def count_for_spool(self, spool_id: SpoolId) -> int: ...
