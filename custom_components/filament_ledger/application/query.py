"""UC-11 · SpoolOverview and UC-12 · MovementHistory.

Read models. No side effects, no events, no mutation — a query has no business emitting
events or changing state, and keeping them in a separate module is how that stays true.

UC-12 is the use case that makes the ledger *worth* being a ledger. Without it,
immutability is overhead with no payoff.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..domain.model.movement import Movement
from ..domain.model.movement_void import MovementVoid
from ..domain.model.pending_review import PendingReview
from ..domain.model.print_job import PrintJob
from ..domain.model.spool import Spool
from ..domain.port.repositories import (
    MovementRepository,
    MovementVoidRepository,
    PrintJobRepository,
    ReviewRepository,
    SpoolFilter,
    SpoolRepository,
)
from ..domain.service.anomaly_detector import AnomalyDetector
from ..domain.service.balance_calculator import balance, running_balances
from ..domain.service.confidence_evaluator import ConfidenceEvaluator
from ..domain.value.colour import Colour
from ..domain.value.confidence import Confidence
from ..domain.value.grams import Grams
from ..domain.value.identifiers import MovementId, PrintJobId, SpoolId
from ..domain.value.location import AmsSlot, ExternalSpool, Location, Storage
from ..domain.value.movement_type import MovementSource, MovementType
from ..domain.value.spool_state import SpoolState
from .errors import SpoolNotFoundError


def describe_location(location: Location) -> dict[str, str | int | None]:
    match location:
        case AmsSlot(slot):
            return {"kind": "AMS_SLOT", "slot": slot.value, "label": f"AMS slot {slot.value}"}
        case ExternalSpool():
            return {"kind": "EXTERNAL_SPOOL", "slot": None, "label": "External spool"}
        case Storage():
            return {"kind": "STORAGE", "slot": None, "label": "Storage"}


@dataclass(frozen=True, slots=True)
class SpoolSummary:
    spool: Spool
    balance: Grams
    state: SpoolState
    confidence: Confidence
    movement_count: int
    last_movement_at: datetime | None
    has_anomaly: bool

    @property
    def percentage(self) -> int:
        return self.spool.remaining_percentage(self.balance).rounded


@dataclass(frozen=True, slots=True)
class VoidChapters:
    """Which entries the open void chapters cover — computed once per query pass.

    A movement is *hidden as voided* iff it has an open void row, **or** it is the
    `VOID_REVERSAL` of one (docs/14 §14.4.5). The pair drops out together, which is
    arithmetically neutral: they sum to zero, so nothing a default view hides can change
    a number a default view shows.

    Two frozen sets rather than a repository call per row. The Trash is human-sized and so
    is this: one read serves the whole overview, the whole global history and every
    confidence evaluation in the same pass.
    """

    voided: frozenset[MovementId] = frozenset()
    reversals: frozenset[MovementId] = frozenset()

    @classmethod
    def of(cls, chapters: Sequence[MovementVoid]) -> VoidChapters:
        return cls(
            voided=frozenset(chapter.movement_id for chapter in chapters),
            reversals=frozenset(
                chapter.reversal_movement_id
                for chapter in chapters
                if chapter.reversal_movement_id is not None
            ),
        )

    def covers(self, movement: Movement) -> bool:
        return movement.id in self.voided or movement.id in self.reversals

    def visible(self, movements: Sequence[Movement]) -> list[Movement]:
        return [movement for movement in movements if not self.covers(movement)]


@dataclass(frozen=True, slots=True)
class HistoryLine:
    """One row of the spool detail — **the one place nothing is ever hidden.**

    docs/06 §6.5 defines that view as a derivation whose rows must reconcile to the
    header, so a voided row is *styled*, never omitted: dropping it there would break the
    closed sum in the very view that exists to prove it. `voided` is what the styling
    turns on.
    """

    movement: Movement
    balance_after: Grams
    voided: bool = False


@dataclass(frozen=True, slots=True)
class SpoolDetail:
    summary: SpoolSummary
    lines: list[HistoryLine]


@dataclass(frozen=True, slots=True)
class GlobalHistoryLine:
    """One ledger entry joined to what the global history table renders beside it.

    The movement names its spool and job by id; the table renders a swatch, a name and a
    job title. The read model serves the join so the panel never keeps an id→spool map of
    its own — and unlike `HistoryLine` there is no running balance here, because no
    balance is derivable from a cross-spool slice.
    """

    movement: Movement
    spool_name: str
    spool_colour: Colour
    job_name: str | None
    # False in every default view — an open chapter is filtered out before it gets here —
    # and part of the contract anyway, so a row can offer the right actions without a
    # second query. A row that says `voided` offers neither Delete nor Reassign.
    voided: bool = False


@dataclass(frozen=True, slots=True)
class TrashedMovement:
    """One open void chapter, as the Trash's second section renders it (docs/14 §14.4.4).

    Closed chapters are deliberately absent from this list: the Trash shows what is
    currently out, not everything that ever was.
    """

    void: MovementVoid
    movement: Movement
    spool: Spool

    @property
    def restorable(self) -> bool:
        """Whether the **[ Restore ]** button is offered at all.

        Two ways it is not. A without-restitution void returned nothing, so there is
        nothing to deduct again and the row carries an explanation in the button's place.
        A retired spool has nowhere to deduct *from*, which is the symmetric rule to
        voiding (docs/14 §14.4.2).
        """
        return self.void.had_restitution and self.spool.is_in_inventory


@dataclass(frozen=True, slots=True)
class TrashView:
    """Both sections of the Trash tab: retracted spools, and open void chapters.

    Nothing here is a holding pen for rows awaiting destruction — it is a *view* over
    facts that already exist, which is why everything in it can be restored
    (docs/adr/0007).
    """

    spools: list[SpoolSummary]
    movements: list[TrashedMovement]


@dataclass(frozen=True, slots=True)
class StockTotals:
    total: Grams
    spool_count: int
    needs_weighing: int
    per_material: dict[str, Grams]


@dataclass(frozen=True, slots=True)
class PendingReviewDetail:
    """One queue item joined to the job it questions — the shape the review card renders.

    The review freezes the estimate and the slot→spool resolution; the job carries the
    name, the raw state strings and the progress figures. The card needs both halves, so
    the read model serves them together rather than making the panel re-join them.
    """

    review: PendingReview
    job: PrintJob


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    """What one coordinator refresh distributes: the overview plus the queue's size.

    The count rides along with the spools because they change on the same occasions —
    every mutation path refreshes the coordinator — and a badge that lags the queue would
    defeat the queue (docs/05-ha-integration.md §5.7).
    """

    spools: list[SpoolSummary]
    pending_review_count: int


@dataclass(frozen=True, slots=True)
class Queries:
    spools: SpoolRepository
    movements: MovementRepository
    reviews: ReviewRepository
    jobs: PrintJobRepository
    voids: MovementVoidRepository
    confidence: ConfidenceEvaluator = field(default_factory=ConfidenceEvaluator)
    anomalies: AnomalyDetector = field(default_factory=AnomalyDetector)

    async def open_chapters(self) -> VoidChapters:
        """Every entry currently hidden as voided. One read, reused across a query pass."""
        return VoidChapters.of(await self.voids.list_open())

    async def summarise(self, spool: Spool, chapters: VoidChapters | None = None) -> SpoolSummary:
        """`chapters` is passed in by callers that already read them — `overview` does it
        once for every spool rather than once per spool. Absent, it is read here."""
        if chapters is None:
            chapters = await self.open_chapters()
        history = await self.movements.list_for_spool(spool.id)
        current = balance(history)
        return SpoolSummary(
            spool=spool,
            balance=current,
            state=spool.state(balance=current, movement_count=len(history)),
            # **Confidence ignores open void chapters** (docs/14 §14.4.5). A voided
            # estimate no longer bears on the balance, so it must not keep a spool at
            # LOW; the voided original and its reversal drop out as a pair, which is
            # arithmetically neutral and semantically right. The filtering happens here,
            # at the application layer — `ConfidenceEvaluator` stays pure, an accepted
            # cost recorded in docs/adr/0007.
            confidence=self.confidence.evaluate(
                movements=chapters.visible(history), opening_weight=spool.opening_weight
            ),
            # The count is of the whole history: it is what the detail view renders and
            # what SEALED is derived from, and hiding rows from it would make a spool
            # with one voided print read as never used.
            movement_count=len(history),
            last_movement_at=history[-1].occurred_at if history else None,
            # The balance an anomaly is judged against is the full sum, and the pair nets
            # to zero, so voids need no rule here — the arithmetic already carries it.
            has_anomaly=bool(
                self.anomalies.inspect(spool_id=spool.id, balance=current, location=spool.location)
            ),
        )

    async def overview(self, *, include_discarded: bool = False) -> list[SpoolSummary]:
        """Inventory. Deleted spools are absent and there is no flag to bring them back:
        the Trash is where a retracted registration is seen, and `SpoolFilter.deleted_only`
        is how it asks (docs/14 §14.4.5)."""
        spools = await self.spools.list(SpoolFilter(include_discarded=include_discarded))
        chapters = await self.open_chapters()
        summaries = [await self.summarise(spool, chapters) for spool in spools]
        # Depleted spools sink to the bottom but stay visible: still a real object until
        # somebody throws it away.
        return sorted(
            summaries,
            key=lambda s: (
                s.state is SpoolState.DEPLETED,
                s.state is SpoolState.DISCARDED,
                -s.balance.milligrams,
            ),
        )

    async def detail(self, spool_id: SpoolId) -> SpoolDetail:
        """Everything, always — including a deleted spool's, reached from the Trash.

        No row is hidden here. The view is a derivation whose rows must reconcile to the
        header (docs/06 §6.5); voided rows are marked so the panel can strike them
        through, and the arithmetic stays whole.
        """
        spool = await self.spools.get(spool_id)
        if spool is None:
            raise SpoolNotFoundError(spool_id)
        chapters = await self.open_chapters()
        history = await self.movements.list_for_spool(spool_id)
        return SpoolDetail(
            summary=await self.summarise(spool, chapters),
            lines=[
                HistoryLine(
                    movement=line.movement,
                    balance_after=line.balance_after,
                    voided=chapters.covers(line.movement),
                )
                for line in reversed(running_balances(history))
            ],
        )

    async def movement_history(self, limit: int = 100) -> list[GlobalHistoryLine]:
        """UC-12 across every spool: the newest `limit` entries, newest first.

        Spools and jobs are fetched once per distinct id, not once per row — a hundred
        prints from one spool is one spool read. A movement whose spool row is missing
        cannot be written by any use case — every append happens in the unit of work that
        saved the spool, and the schema's foreign key backs it — so such a row is skipped
        rather than crashing the whole view, the same policy `pending_reviews` applies.
        A missing job row is different: `job_id` is nullable by design, and a movement
        without one simply carries no job name.

        Two more rows are skipped now, both by the same mechanism (docs/14 §14.4.5):

        - **A deleted spool's movements.** Driven by the spool's state, not by per-movement
          void rows — retracting a registration is one fact about the spool. A *discarded*
          spool's movements stay: waste is history.
        - **Open void chapters** — the voided entry and its reversal both. They are listed
          in the Trash instead. A closed chapter shows all three of its rows, labelled;
          the net is honest and the story is complete.

        The limit bounds the slice that is read, not the rows that survive it, exactly as
        the missing-spool skip has always worked. Filtering inside SQL would mean the
        history query consulting the void table, and docs/adr/0007 keeps that table out of
        the read path that matters.
        """
        lines: list[GlobalHistoryLine] = []
        spools: dict[SpoolId, Spool | None] = {}
        jobs: dict[PrintJobId, PrintJob | None] = {}
        chapters = await self.open_chapters()
        for movement in await self.movements.list_recent(limit):
            if chapters.covers(movement):
                continue
            if movement.spool_id not in spools:
                spools[movement.spool_id] = await self.spools.get(movement.spool_id)
            spool = spools[movement.spool_id]
            if spool is None or spool.is_deleted:
                continue
            job = None
            if movement.job_id is not None:
                if movement.job_id not in jobs:
                    jobs[movement.job_id] = await self.jobs.get(movement.job_id)
                job = jobs[movement.job_id]
            lines.append(
                GlobalHistoryLine(
                    movement=movement,
                    spool_name=spool.display_name,
                    spool_colour=spool.colour,
                    job_name=job.name if job is not None else None,
                )
            )
        return lines

    async def pending_reviews(self) -> list[PendingReviewDetail]:
        """The open queue, oldest first, each review joined to its job.

        Order comes from the repository: decisions are asked for in the order the doubts
        arose. A pending review whose job row is missing cannot be built by any use case —
        UC-05 saves the job before the review inside one unit, and the schema's foreign
        key backs it — so such a row is skipped rather than crashing the whole queue.
        """
        details: list[PendingReviewDetail] = []
        for review in await self.reviews.list_pending():
            job = await self.jobs.get(review.job_id)
            if job is None:
                continue
            details.append(PendingReviewDetail(review=review, job=job))
        return details

    async def snapshot(self) -> LedgerSnapshot:
        """One coordinator refresh: everything the entities read, in one pass."""
        return LedgerSnapshot(
            spools=await self.overview(),
            pending_review_count=len(await self.reviews.list_pending()),
        )

    async def stock(self) -> StockTotals:
        summaries = await self.overview()
        per_material: dict[str, Grams] = {}
        total = Grams.zero()
        for summary in summaries:
            if not summary.state.counts_as_stock:
                continue
            total = total + summary.balance
            key = summary.spool.material.display_name
            per_material[key] = per_material.get(key, Grams.zero()) + summary.balance
        return StockTotals(
            total=total,
            spool_count=len([s for s in summaries if s.state.counts_as_stock]),
            needs_weighing=len([s for s in summaries if s.confidence.needs_weighing]),
            per_material=per_material,
        )

    async def trash(self) -> TrashView:
        """Both sections of the Trash tab (docs/14 §14.4.4).

        Spools come from the one filter that inverts; movement chapters come from
        `list_open`, newest deletion first. A chapter whose movement or spool row has
        gone missing is skipped rather than crashing the tab — the same policy every
        other join in this module applies, and the foreign keys make it unreachable
        anyway.
        """
        deleted = await self.spools.list(SpoolFilter(deleted_only=True))
        chapters = await self.open_chapters()
        summaries = [await self.summarise(spool, chapters) for spool in deleted]

        trashed: list[TrashedMovement] = []
        spools: dict[SpoolId, Spool | None] = {}
        for chapter in await self.voids.list_open():
            movement = await self.movements.get(chapter.movement_id)
            if movement is None:  # pragma: no cover - the void row's foreign key backs this
                continue
            if movement.spool_id not in spools:
                spools[movement.spool_id] = await self.spools.get(movement.spool_id)
            spool = spools[movement.spool_id]
            if spool is None:  # pragma: no cover - the movement's foreign key backs this
                continue
            trashed.append(TrashedMovement(void=chapter, movement=movement, spool=spool))

        return TrashView(spools=summaries, movements=trashed)


def movement_label(movement: Movement) -> str:
    return _MOVEMENT_LABELS[movement.type]


def source_label(source: MovementSource) -> str:
    return "confirmed by you" if source is MovementSource.USER_CONFIRMED else "automatic"


def percentage_of(part: Grams, whole: Grams) -> Decimal:
    return part.ratio_to(whole) * 100


_MOVEMENT_LABELS: dict[MovementType, str] = {
    MovementType.OPENING_BALANCE: "Opening balance",
    MovementType.PRINT_CONSUMPTION: "Print",
    MovementType.PURGE_WASTE: "Purge waste",
    MovementType.ESTIMATED_CONSUMPTION: "Estimated consumption",
    MovementType.MANUAL_ADJUSTMENT: "Adjustment",
    MovementType.RECONCILIATION: "Reconciliation",
    MovementType.DISCARD: "Discard",
    # The corrections, named for what they did to the ledger rather than for the table
    # they were written from. A row in the spool detail has to explain itself to somebody
    # reading it six months later with no memory of the modal (docs/14 §14.3, §14.4).
    MovementType.VOID_REVERSAL: "Deleted entry — returned",
    MovementType.REINSTATEMENT: "Restored entry",
    MovementType.REASSIGNMENT: "Reassignment",
}
