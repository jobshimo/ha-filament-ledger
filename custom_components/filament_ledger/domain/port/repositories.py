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
from ..model.movement_void import MovementVoid
from ..model.pending_review import PendingReview
from ..model.print_job import PrintJob
from ..model.spool import Spool
from ..value.identifiers import MovementId, PrintJobId, ReviewId, SpoolId, TagUid
from ..value.location import Location


@dataclass(frozen=True, slots=True)
class SpoolFilter:
    """Query criteria for listing spools. All fields optional; all combine with AND.

    The two retirement flags are separate because the two states are separate facts
    (docs/14 §14.4.5): a discarded spool's movements are still history worth showing, a
    deleted spool's are not. Both default to *excluded*, which is what "inventory" means
    everywhere in this product.
    """

    include_discarded: bool = False
    include_deleted: bool = False
    # The Trash's own query, and the only one that inverts a flag rather than widening it:
    # *only* spools whose registration was retracted. Set alone — combining it with the
    # include flags would be asking for spools that are and are not deleted.
    deleted_only: bool = False
    mounted_only: bool = False
    search: str | None = None


class SpoolRepository(Protocol):
    async def get(self, spool_id: SpoolId) -> Spool | None:
        """By id, whatever its state.

        The one read that never filters. A deleted spool's detail view stays reachable
        from the Trash and shows its history in full (docs/14 §14.4.5), and every use case
        that has to *refuse* a retired spool has to load it first in order to say so.
        """
        ...

    async def find_by_tag(self, tag: TagUid) -> list[Spool]:
        """Every **in-inventory** spool carrying this tag — neither discarded nor deleted.

        Returns a **list**, not an optional single spool. A Bambu tag identifies a product
        batch rather than a physical unit, so two spools may legitimately carry the same
        payload; a port returning one would force the adapter to pick, silently, and deduct
        from a spool the user never loaded. The port returns what is true and the use case
        decides what to do with an ambiguous answer — which is to ask.

        Deleted spools are excluded for the same reason discarded ones are, and it matters
        in one concrete way: a spool retracted as never-registered must not go on blocking
        its tag, or re-registering the reel the user actually owns demands a
        duplicate-confirmation about a spool that no longer exists anywhere in the UI.
        """
        ...

    async def find_by_location(self, location: Location) -> Spool | None:
        """Who is in this slot — counting only spools in inventory.

        Deleted and discarded spools hold no position: their locations are cleared when
        they retire, and the partial unique indexes stopped watching them (migration
        0003). A read that still saw them would mount past an occupant that is not there.
        """
        ...

    async def list(self, criteria: SpoolFilter) -> list[Spool]: ...

    async def save(self, spool: Spool) -> None: ...


class MovementRepository(Protocol):
    """Deliberately exposes no `update` and no `delete`.

    Immutability is enforced by the shape of the interface, not by a comment asking
    politely. A rule that can only be broken by changing the interface is a rule that holds.
    """

    async def append(self, movement: Movement) -> None: ...

    async def get(self, movement_id: MovementId) -> Movement | None:
        """One entry by id — a read, and the corrections of docs/14 need it.

        Voiding, restoring and reassigning all start from a row the user pointed at in a
        history table, so the id is what they have. A read adds nothing to this port's
        surface that could mutate anything: `get` is how the use case *checks* the rules
        before appending the new entries that express the correction.
        """
        ...

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


class MovementVoidRepository(Protocol):
    """The void chapters — a **different thing** from the movements they describe.

    Its own port precisely so `MovementRepository` keeps exposing no update and no delete:
    `movement_void` is a status record *about* a movement, so the one table in this design
    that is ever written after insert is not the ledger. The architecture test that guards
    the movement port's shape guards exactly that interface, and this one does not weaken
    it (docs/adr/0007, docs/14 §14.4).

    No balance query consults this port. A voided entry and its reversal sum to zero, so
    `balance = Σ(movements)` still derives from one table.
    """

    async def append(self, void: MovementVoid) -> None: ...

    async def get(self, movement_id: MovementId) -> MovementVoid | None:
        """The chapter for this entry, open or closed — or `None` if it was never voided."""
        ...

    async def list_open(self) -> list[MovementVoid]:
        """Every chapter still out: what the Trash lists, and what the default views hide.

        Closed chapters are deliberately absent. The Trash shows what is currently
        deleted, not everything that ever was, and a reinstated entry is ordinary history
        again — all three of its rows show, labelled, and the net is honest.
        """
        ...

    async def record_reinstatement(
        self, movement_id: MovementId, reinstatement_id: MovementId, at: datetime
    ) -> None:
        """Close a chapter. The only post-insert write in the correction design."""
        ...


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
