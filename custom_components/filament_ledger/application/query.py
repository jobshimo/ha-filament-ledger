"""UC-11 · SpoolOverview and UC-12 · MovementHistory.

Read models. No side effects, no events, no mutation — a query has no business emitting
events or changing state, and keeping them in a separate module is how that stays true.

UC-12 is the use case that makes the ledger *worth* being a ledger. Without it,
immutability is overhead with no payoff.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from ..domain.model.movement import Movement
from ..domain.model.movement_void import MovementVoid
from ..domain.model.pending_review import PendingReview
from ..domain.model.print_job import PrintJob
from ..domain.model.spool import Spool
from ..domain.port.clock import Clock
from ..domain.port.repositories import (
    NO_FILTERS,
    MovementFilter,
    MovementRepository,
    MovementVoidRepository,
    PrintJobRepository,
    ReviewRepository,
    SpoolFilter,
    SpoolRepository,
)
from ..domain.service.anomaly_detector import AnomalyDetector
from ..domain.service.balance_calculator import balance, consumed, running_balances
from ..domain.service.confidence_evaluator import (
    ConfidenceEvaluator,
    anchor_movement,
    movements_since_anchor,
)
from ..domain.value.colour import Colour
from ..domain.value.confidence import Confidence
from ..domain.value.grams import Grams
from ..domain.value.identifiers import MovementId, PrintJobId, SpoolId
from ..domain.value.location import AmsSlot, ExternalSpool, Location, Storage
from ..domain.value.movement_type import MovementSource, MovementType
from ..domain.value.print_job_state import PrintJobState
from ..domain.value.review import ReviewState
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
class ConfidenceBasis:
    """The facts a confidence badge was derived from, so a surface can say why it changed.

    `ConfidenceEvaluator` answers *how much should I trust this number* and returns a level
    and nothing else — it is a pure domain function and stays one. The question a user
    actually asks is the next one: **why has it changed?** Answering that means naming the
    anchor and measuring from it, which is assembly rather than judgement, so it happens
    here. docs/adr/0007 already settled the same boundary for void filtering: the domain
    service stays pure, and the application prepares what it is fed and what is shown beside
    its answer.

    Nothing here is a second opinion. The window is the evaluator's own
    `movements_since_anchor`, over the same movements the level was evaluated on, so the
    sentence and the badge cannot disagree.
    """

    # The anchor's type, which is what distinguishes *since you weighed it* from *since you
    # registered it* — two different claims, and the user deserves to know which one they
    # are being given. Always `RECONCILIATION` or `OPENING_BALANCE`; `None` only for a
    # history that carries no anchor at all.
    anchor: MovementType | None
    anchored_at: datetime | None
    consumed_since: Grams
    # How many approved estimates have landed since the anchor, and when the most recent one
    # did. Zero is the ordinary case and is what tells a surface that the level was reached
    # by consumption rather than by an estimate.
    estimates_since: int
    latest_estimate_at: datetime | None


def confidence_basis(movements: Sequence[Movement]) -> ConfidenceBasis:
    """Read the same window `ConfidenceEvaluator` reads, and report what is in it."""
    since = movements_since_anchor(movements)
    anchor = anchor_movement(movements)
    estimates = [movement for movement in since if movement.is_estimate]
    return ConfidenceBasis(
        anchor=anchor.type if anchor is not None else None,
        anchored_at=anchor.occurred_at if anchor is not None else None,
        consumed_since=consumed(since),
        estimates_since=len(estimates),
        latest_estimate_at=max((e.occurred_at for e in estimates), default=None),
    )


@dataclass(frozen=True, slots=True)
class SpoolSummary:
    spool: Spool
    balance: Grams
    state: SpoolState
    confidence: Confidence
    # Why the level is what it is. Travels with it everywhere, because a badge nothing
    # explains is a badge the user learns to ignore.
    confidence_basis: ConfidenceBasis
    movement_count: int
    last_movement_at: datetime | None
    has_anomaly: bool

    @property
    def percentage(self) -> int:
        return self.spool.remaining_percentage(self.balance).rounded

    @property
    def drawn_since_anchor(self) -> Decimal:
        """What has left the spool since the anchor, as a share of the opening weight.

        The figure the consumption rungs of §2.6 are read against, so a surface can show
        the reader the same number the rule was applied to rather than a paraphrase.
        """
        return self.confidence_basis.consumed_since.ratio_to(self.spool.opening_weight)


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


class StatisticsPeriod(StrEnum):
    """How far back the statistics view looks.

    Three answers and no date pickers. A window this coarse is what a filament ledger can
    actually answer honestly — the ledger is months old at best, and a custom range would
    invite comparisons across periods whose sample sizes make them meaningless.
    """

    LAST_30_DAYS = "30d"
    LAST_90_DAYS = "90d"
    ALL_TIME = "all"

    @property
    def days(self) -> int | None:
        """How many days back, or `None` for all of it."""
        return _PERIOD_DAYS[self]

    def since(self, now: datetime) -> datetime | None:
        """The cut-off this period implies at `now` — `None` means no cut-off at all."""
        days = self.days
        return None if days is None else now - timedelta(days=days)


_PERIOD_DAYS: dict[StatisticsPeriod, int | None] = {
    StatisticsPeriod.LAST_30_DAYS: 30,
    StatisticsPeriod.LAST_90_DAYS: 90,
    StatisticsPeriod.ALL_TIME: None,
}

# What counts as filament *used*: a print the printer carried out, and an estimate a person
# approved. Nothing inferred and unconfirmed ever reaches the ledger, so an
# `ESTIMATED_CONSUMPTION` row is by construction a confirmed one
# (docs/adr/0004-approval-queue-for-estimates.md).
_CONSUMPTION_TYPES = frozenset({MovementType.PRINT_CONSUMPTION, MovementType.ESTIMATED_CONSUMPTION})

# What counts as filament *wasted* (docs/14 §14.4.5). `PURGE_WASTE` is listed even though no
# use case writes one yet: the type exists, and a statistic that would silently ignore it the
# day one is written is a statistic that goes quietly wrong.
_WASTE_TYPES = frozenset({MovementType.DISCARD, MovementType.PURGE_WASTE})

# How many prints the top-consumers table names. Five is a glance; twenty is a report.
TOP_PRINT_COUNT = 5


@dataclass(frozen=True, slots=True)
class ColourConsumption:
    """One bar of the by-colour chart. The colour is the real stored value, so the panel
    paints the swatch the user recognises rather than a palette entry we invented."""

    colour: Colour
    grams: Grams


@dataclass(frozen=True, slots=True)
class MaterialConsumption:
    material: str
    grams: Grams


@dataclass(frozen=True, slots=True)
class TopPrint:
    """One row of the biggest-prints table, joined to the job that consumed it."""

    job_id: PrintJobId
    name: str
    started_at: datetime
    grams: Grams


@dataclass(frozen=True, slots=True)
class PrintOutcomes:
    """How the period's jobs ended.

    A job still `RUNNING` is deliberately in none of the three: it has not ended, so it has
    no outcome, and counting it anywhere would be reporting a result that does not exist.
    """

    finished: int = 0
    cancelled: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.finished + self.cancelled + self.failed


@dataclass(frozen=True, slots=True)
class ReviewOutcomes:
    """How the period's doubts were settled. Neither number is derivable from the
    movements: a dismissal writes none, which is the whole point of dismissing."""

    approved: int = 0
    dismissed: int = 0

    @property
    def total(self) -> int:
        return self.approved + self.dismissed


@dataclass(frozen=True, slots=True)
class PrintTime:
    """Measured print time, and how many prints it was measured over.

    Every duration comes from `PrintJob.measured_duration`, so this is a measurement and
    never an estimate: the printer's own `start_time`/`end_time` pair for a job that ran to
    completion, and the ledger's own `started_at`/`ended_at` — real columns since migration
    0001 — for everything else. It covers **only jobs with a positive duration**,
    which excludes the row `TrackPrintJob` writes when a restart swallowed a print's start
    and both of its timestamps became the moment the ending arrived. That row's duration is
    zero, and zero is not how long a print took. `prints` travels beside the total so the
    panel can say what the average is an average *of* rather than implying it covers every
    job in the period.
    """

    total: timedelta
    prints: int

    @classmethod
    def of(cls, jobs: Sequence[PrintJob]) -> PrintTime | None:
        """Sum what can be measured, or `None` when nothing can.

        **The one accumulator.** The Stats card sums a period's jobs and the Printer tab
        sums every job the ledger holds; two accumulators would be two answers to one
        question, and they would eventually disagree about which rows count.
        """
        durations = [duration for job in jobs if (duration := job.measured_duration) is not None]
        if not durations:
            return None
        return cls(total=sum(durations, timedelta()), prints=len(durations))

    @property
    def average(self) -> timedelta:
        return self.total / self.prints


@dataclass(frozen=True, slots=True)
class ObservedPrintTime:
    """Every print this ledger has ever timed, and the day it started counting.

    Deliberately **not** the machine's lifetime hours. `ha-bambulab` exposes no sensor for
    those, so a figure presented as an odometer could only be this sum wearing a label it
    has not earned — one that quietly began the day the integration was installed. `since`
    is what keeps the claim honest: the earliest print in the ledger, so the panel can say
    what the total is a total *of* rather than implying it covers the machine's life.

    `since` is the first job **recorded**, not the first job measured. The question it
    answers is when this ledger started watching, and a job whose duration could not be
    measured was still watched.
    """

    measured: PrintTime
    since: datetime


@dataclass(frozen=True, slots=True)
class StatisticsView:
    """Everything the Stats tab renders, for one period (docs/06 §6.7, docs/15 §15.6).

    Computed here rather than in the panel, deliberately: an aggregation is a query, and
    panel JavaScript is the one layer this project cannot test (docs/14 §14.8).

    The visibility law of docs/14 §14.4.5 governs every figure below — a deleted spool's
    movements count in nothing, an open void chapter's two rows drop out as a pair, and a
    discard is waste rather than consumption.
    """

    period: StatisticsPeriod
    since: datetime | None
    consumed: Grams
    wasted: Grams
    prints: PrintOutcomes
    reviews: ReviewOutcomes
    by_colour: list[ColourConsumption]
    by_material: list[MaterialConsumption]
    top_prints: list[TopPrint]
    # `None` when nothing in the period had a measurable duration. A card of dashes teaches
    # nothing; an absent card says the honest thing by saying nothing.
    print_time: PrintTime | None

    @property
    def is_empty(self) -> bool:
        """Whether the period contains nothing at all — computed on the exact grams, not on
        the rounded ones, so 0.4 g of consumption is not reported as an empty period."""
        return (
            self.consumed.is_zero
            and self.wasted.is_zero
            and self.prints.total == 0
            and self.reviews.total == 0
        )


@dataclass(frozen=True, slots=True)
class Queries:
    spools: SpoolRepository
    movements: MovementRepository
    reviews: ReviewRepository
    jobs: PrintJobRepository
    voids: MovementVoidRepository
    # Read models are timeless except for one question — *how far back?* — and a period
    # relative to "now" needs the same port every use case already reads time from, so the
    # cut-off is a value a test can set rather than a call to the wall clock.
    clock: Clock
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
        # One filtered view of the history, read by both the level and the reason for it.
        # Two calls to `chapters.visible` would be two chances for the sentence to describe
        # a window the badge was not evaluated over.
        accounted = chapters.visible(history)
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
                movements=accounted, opening_weight=spool.opening_weight
            ),
            confidence_basis=confidence_basis(accounted),
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

    async def movement_history(
        self, limit: int = 100, criteria: MovementFilter = NO_FILTERS
    ) -> list[GlobalHistoryLine]:
        """UC-12 across every spool: the newest `limit` entries matching `criteria`.

        `criteria` reaches SQL untouched — date bounds, the colours of the spools an entry
        may belong to, a band the entry's *magnitude* must fall in, and free text. Nothing
        here re-implements any of it, which is the point: a history that grew without bound
        would eventually not fit in the reply, let alone in a browser.

        **What the free text searches is the entry's own name**, which is not one column.
        The History table's entry cell renders three things (docs/06 §6.6): a label, the
        job name, and the note. The note and the job name are stored text the user wrote or
        the printer reported, and both are searched. The label is not: it is generated in
        the panel from the movement's `type` and translated, so searching it server-side
        would match English words against a Spanish screen. The spool's name is not
        searched either — it is a column of its own beside the entry, with a colour filter
        of its own to narrow it.

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
        the missing-spool skip has always worked. Those two exclusions stay in Python while
        the user's filters go to SQL, and the asymmetry is deliberate: filtering them
        inside SQL would mean the history query consulting the void table, and
        docs/adr/0007 keeps that table out of the read path that matters.
        """
        lines: list[GlobalHistoryLine] = []
        spools: dict[SpoolId, Spool | None] = {}
        jobs: dict[PrintJobId, PrintJob | None] = {}
        chapters = await self.open_chapters()
        for movement in await self.movements.list_recent(limit, criteria):
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

    async def statistics(
        self, period: StatisticsPeriod = StatisticsPeriod.LAST_30_DAYS
    ) -> StatisticsView:
        """What the ledger says about one period (docs/15 §15.6, shipped early).

        One time-bounded pass over the movements, one over the jobs, one over the resolved
        reviews. The visibility law of docs/14 §14.4.5 is applied here, once, so no chart
        can disagree with another about which grams were real:

        - **Open void chapters drop out** — the voided entry and its reversal both. They
          sum to zero, so nothing hidden could have changed a number shown.
        - **A deleted spool's movements count in nothing**, driven by the spool's state
          rather than by per-movement rows: retracting a registration is one fact about
          the spool. A *discarded* spool's movements stay, because waste is history.
        - **Discards are waste, never consumption.** They are filament that left the
          spool without printing anything, and folding them into consumption would flatter
          every number on the page.

        Consumption is attributed to the spool the consumption entry names. A later
        `REASSIGNMENT` moves the charge in the balances but not in these buckets — the pair
        is not counted here at all, because counting its two signed legs would draw a
        negative bar for any correction whose original entry fell outside the period. The
        totals are unaffected either way (the pair nets to zero); only the colour and
        material attribution of a corrected charge stays with the entry as written.
        """
        since = period.since(self.clock.now())
        chapters = await self.open_chapters()

        # `used` rather than `consumed`: the module-level `consumed()` is the domain sum over
        # one spool's window, and a local of the same name inside a period-wide aggregation
        # would read as that function to anyone skimming.
        used = Grams.zero()
        wasted = Grams.zero()
        by_colour: dict[Colour, Grams] = {}
        by_material: dict[str, Grams] = {}
        per_job: dict[PrintJobId, Grams] = {}
        spools: dict[SpoolId, Spool | None] = {}

        for movement in await self.movements.list_in_period(since):
            if chapters.covers(movement):
                continue
            if movement.spool_id not in spools:
                spools[movement.spool_id] = await self.spools.get(movement.spool_id)
            spool = spools[movement.spool_id]
            if spool is None or spool.is_deleted:
                continue
            # Magnitude, not sign. Every type counted here is a decrease by definition, and
            # a chart of negative bars would be arithmetic showing through as decoration.
            amount = abs(movement.amount)
            if movement.type in _WASTE_TYPES:
                wasted = wasted + amount
            elif movement.type in _CONSUMPTION_TYPES:
                used = used + amount
                by_colour[spool.colour] = by_colour.get(spool.colour, Grams.zero()) + amount
                material = spool.material.display_name
                by_material[material] = by_material.get(material, Grams.zero()) + amount
                if movement.job_id is not None:
                    per_job[movement.job_id] = per_job.get(movement.job_id, Grams.zero()) + amount

        jobs = await self.jobs.list_in_period(since)

        return StatisticsView(
            period=period,
            since=since,
            consumed=used,
            wasted=wasted,
            prints=PrintOutcomes(
                finished=_count(jobs, PrintJobState.FINISHED),
                cancelled=_count(jobs, PrintJobState.CANCELLED),
                failed=_count(jobs, PrintJobState.FAILED),
            ),
            reviews=await self._review_outcomes(since),
            by_colour=[
                ColourConsumption(colour=colour, grams=amount)
                for colour, amount in _descending(by_colour)
            ],
            by_material=[
                MaterialConsumption(material=material, grams=amount)
                for material, amount in _descending(by_material)
            ],
            top_prints=await self._top_prints(per_job),
            print_time=PrintTime.of(jobs),
        )

    async def observed_print_time(self) -> ObservedPrintTime | None:
        """Every print this ledger has ever timed — the Printer tab's total (docs/14 §14.5).

        The same sum `statistics` shows for a period, with no period at all. `None` when
        nothing has a measurable duration, which is how every read model here declines to
        invent a metric it cannot support.

        One unbounded pass over `print_job`, which is the read `statistics(ALL_TIME)` has
        always performed and is affordable for the same reason: a heavy household prints a
        few hundred times a year, and the table holds one narrow row each (docs/08 §8.5).
        It runs on every printer push, so the cost is worth naming rather than assuming —
        should a ledger ever grow past that, the answer is a stored running total, not a
        second accumulator disagreeing with this one.
        """
        jobs = await self.jobs.list_in_period(None)
        measured = PrintTime.of(jobs)
        if measured is None:
            return None
        # `list_in_period` answers oldest first, so the first row is the first print this
        # ledger ever saw — the honest anchor for a total that is not the machine's own.
        return ObservedPrintTime(measured=measured, since=jobs[0].started_at)

    async def _review_outcomes(self, since: datetime | None) -> ReviewOutcomes:
        resolved = await self.reviews.list_resolved(since)
        return ReviewOutcomes(
            approved=sum(1 for review in resolved if review.state is ReviewState.APPROVED),
            dismissed=sum(1 for review in resolved if review.state is ReviewState.DISMISSED),
        )

    async def _top_prints(self, per_job: dict[PrintJobId, Grams]) -> list[TopPrint]:
        """The heaviest few jobs, each joined to the name the user recognises.

        At most `TOP_PRINT_COUNT` reads, and they are `get` rather than a filter over the
        period's job list: a print that started in March and was approved in April belongs
        in April's biggest prints, and its job row is outside the period's window.
        A job row that has gone missing is skipped rather than crashing the tab — the same
        policy every other join in this module applies, and the foreign key backs it.
        """
        top: list[TopPrint] = []
        for job_id, amount in sorted(
            per_job.items(), key=lambda item: (-item[1].milligrams, item[0])
        )[:TOP_PRINT_COUNT]:
            job = await self.jobs.get(job_id)
            if job is None:  # pragma: no cover - the movement's foreign key backs this
                continue
            top.append(
                TopPrint(job_id=job_id, name=job.name, started_at=job.started_at, grams=amount)
            )
        return top


def _count(jobs: Sequence[PrintJob], state: PrintJobState) -> int:
    return sum(1 for job in jobs if job.state is state)


def _descending[K](totals: dict[K, Grams]) -> list[tuple[K, Grams]]:
    """Biggest first. Ties break on insertion order, which is the order the ledger was
    read in — stable across calls, and never arbitrary."""
    return sorted(totals.items(), key=lambda item: -item[1].milligrams)


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
