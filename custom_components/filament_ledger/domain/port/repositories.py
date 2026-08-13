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
from ..value.colour import Colour
from ..value.grams import Grams
from ..value.identifiers import (
    MovementId,
    PrinterSerial,
    PrintJobId,
    ReelUid,
    ReviewId,
    SpoolId,
    TagUid,
)
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
        """Every **in-inventory** spool known to answer to this chip UID.

        Answers from the whole set of chips a reel owns, not merely the one it was
        registered with — a reel has a readable side in odd trays and another in even ones,
        and this question has to mean *whose chip is this* rather than *whose registration
        card says this* (docs/12-field-notes.md).

        The **fallback** lookup since v2.6: `find_by_reel` leads, and this answers for the
        reels that have no `tray_uuid` to lead with — third-party, refilled, and any hub the
        reader could not get a clean identity from.

        Returns a **list**, not an optional single spool. Two spools can still answer to one
        chip UID: a ledger written under the old rule may hold a pair, and a user may
        deliberately confirm a duplicate for two reels whose chips collide. A port returning
        one would force the adapter to pick, silently, and deduct from a spool the user
        never loaded. The port returns what is true and the use case decides what to do with
        an ambiguous answer — which is to ask.

        Deleted spools are excluded for the same reason discarded ones are, and it matters
        in one concrete way: a spool retracted as never-registered must not go on blocking
        its tag, or re-registering the reel the user actually owns demands a
        duplicate-confirmation about a spool that no longer exists anywhere in the UI.
        """
        ...

    async def find_by_reel(self, reel: ReelUid) -> list[Spool]:
        """Every **in-inventory** spool that is this physical reel.

        The lookup automatic recognition leads with. A reel reports one `tray_uuid` in every
        tray it is ever put in, so unlike `find_by_tag` this question has an answer that
        does not move when the reel does.

        Plural for one reason only: a ledger can *arrive* holding two rows for one reel,
        minted by the pre-v2.6 rule and revealed the moment both halves learn their reel.
        Nothing in this release creates that state. Returning the list is what lets the
        panel offer a merge rather than have the ledger pick a winner unasked.
        """
        ...

    async def claim_tag(self, spool_id: SpoolId, tag: TagUid) -> None:
        """Record that this reel also answers to this chip UID.

        Called when a reel resolved by `reel_uid` turns out to be sitting in a tray that
        reads its other side. Idempotent: the detection path re-observes the same chip on
        every republish and must not care.
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


@dataclass(frozen=True, slots=True)
class MovementFilter:
    """Query criteria for the global history. All fields optional; all combine with AND.

    One value object rather than a parameter list per question, for the reason `SpoolFilter`
    is one: a filtered read grows by gaining a field here, not by every layer between the
    panel and the SQL growing an argument.
    """

    # Both bounds inclusive and independent — a lone `since` is "everything from then on",
    # a lone `until` is "everything up to then". An arbitrary instant rather than one of
    # `StatisticsPeriod`'s three windows: the statistics page compares like with like and
    # needs coarse periods, while the history answers *what happened on the day the part
    # came out wrong?* and needs the day.
    since: datetime | None = None
    until: datetime | None = None
    # The colour of the **spool the entry belongs to** — a movement carries no colour of
    # its own, so this is a join. A set rather than a single value, because "the blacks and
    # the greys" is one question a user asks rather than two.
    colours: frozenset[Colour] = frozenset()
    # **Magnitude, never the stored sign.** A print consumption is written as −84.1 g, and
    # a user asking for entries over 50 g means that one: they are thinking of how much
    # filament moved, not which way. Named for the comparison rather than for the column so
    # that a later refactor cannot quietly start comparing signed amounts and still read
    # correctly.
    min_magnitude: Grams | None = None
    max_magnitude: Grams | None = None
    # Free text over the entry's own name. Which columns that is, and why, is settled in
    # `Queries.movement_history` — it is not one column.
    search: str | None = None


#: The unfiltered history. *Clear every filter* is this value rather than a flag, which is
#: what keeps it from being a special case anywhere: it is the natural empty state of the
#: object that carries the filters, and the statement it builds is the one the history has
#: always run.
NO_FILTERS = MovementFilter()


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

    async def list_recent(
        self, limit: int, criteria: MovementFilter = NO_FILTERS
    ) -> list[Movement]:
        """The newest `limit` movements matching `criteria`, across every spool, newest first.

        Newest first because that is the only order the global history view reads: the
        per-spool queries stay oldest-first, the order a running balance is derived in,
        and no balance is derivable from a cross-spool slice anyway.

        **The criteria are the adapter's work, not the caller's.** A ledger's history grows
        without bound, so a read that fetched everything and discarded most of it in Python
        would work for a year and then stop working; the limit must apply to the *filtered*
        slice, and only the database can do that. `NO_FILTERS` is the whole history, which
        is what every caller asked for before there were any.
        """
        ...

    async def list_since(self, spool_id: SpoolId, moment: datetime) -> list[Movement]: ...

    async def list_in_period(self, since: datetime | None) -> list[Movement]:
        """Every movement that occurred at or after `since`, oldest first — all of them
        when `since` is `None`.

        The statistics read model's one pass over the ledger. Unlike `list_recent` it is
        bounded by *time* rather than by a row count, because a period's totals must not
        depend on how many entries happen to fit under a limit: a hundred-row window over
        a busy month would silently under-report the month.

        Oldest first, like the per-spool reads and unlike `list_recent`. Nothing here
        derives a running balance, but accumulating in the order things happened is the
        order a reader would check the arithmetic in.
        """
        ...

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

    async def list_recent(self, limit: int, printer: PrinterSerial | None = None) -> list[PrintJob]:
        """The newest `limit` jobs, newest first — every machine's, or one machine's.

        **`None` here means every machine; `None` on a row means no machine.** The two
        readings of one absence are not the same fact and this is the only place they meet:
        the parameter is an unset filter, exactly as `NO_FILTERS` is above, while a row that
        names no printer is a row written before the ledger recorded which machine ran the
        job. Naming a serial therefore returns that machine's rows **and only those** — a
        nameless row is not evidence about any printer and answering with it would put one
        machine's history under another's name.
        """
        ...

    async def list_in_period(self, since: datetime | None) -> list[PrintJob]:
        """Every job that *started* at or after `since` — all of them when `None`.

        Started rather than ended, deliberately: a job is one event to the person reading
        a period's statistics, and the day they remember is the day they pressed print.
        A job still `RUNNING` comes back too — it has no outcome yet, and the read model
        is the one that decides what to do with that.
        """
        ...


class ReviewRepository(Protocol):
    """Deliberately no `find_pending_for_job`: `list_pending` returns the whole open queue,
    which every caller — the panel, the sensor, UC-05's one-per-job guard — already wants,
    and the queue is human-sized by construction. A narrower query would be a second code
    path to keep honest for no measurable gain."""

    async def get(self, review_id: ReviewId) -> PendingReview | None: ...

    async def list_pending(self) -> list[PendingReview]: ...

    async def list_resolved(self, since: datetime | None) -> list[PendingReview]:
        """Every review resolved at or after `since` — all of them when `None`.

        The counterpart to `list_pending`, and the only read that looks at the queue's
        past. Statistics count approvals against dismissals: how often the estimate was
        accepted is the honest measure of how much the estimator is trusted, and neither
        number is derivable from the movements alone — a dismissal writes none.
        """
        ...

    async def save(self, review: PendingReview) -> None: ...
