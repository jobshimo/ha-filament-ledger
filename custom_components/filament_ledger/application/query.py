"""UC-11 · SpoolOverview and UC-12 · MovementHistory.

Read models. No side effects, no events, no mutation — a query has no business emitting
events or changing state, and keeping them in a separate module is how that stays true.

UC-12 is the use case that makes the ledger *worth* being a ledger. Without it,
immutability is overhead with no payoff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..domain.model.movement import Movement
from ..domain.model.pending_review import PendingReview
from ..domain.model.print_job import PrintJob
from ..domain.model.spool import Spool
from ..domain.port.repositories import (
    MovementRepository,
    PrintJobRepository,
    ReviewRepository,
    SpoolFilter,
    SpoolRepository,
)
from ..domain.service.anomaly_detector import AnomalyDetector
from ..domain.service.balance_calculator import balance, running_balances
from ..domain.service.confidence_evaluator import ConfidenceEvaluator
from ..domain.value.confidence import Confidence
from ..domain.value.grams import Grams
from ..domain.value.identifiers import SpoolId
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
class HistoryLine:
    movement: Movement
    balance_after: Grams


@dataclass(frozen=True, slots=True)
class SpoolDetail:
    summary: SpoolSummary
    lines: list[HistoryLine]


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
    confidence: ConfidenceEvaluator = field(default_factory=ConfidenceEvaluator)
    anomalies: AnomalyDetector = field(default_factory=AnomalyDetector)

    async def summarise(self, spool: Spool) -> SpoolSummary:
        history = await self.movements.list_for_spool(spool.id)
        current = balance(history)
        return SpoolSummary(
            spool=spool,
            balance=current,
            state=spool.state(balance=current, movement_count=len(history)),
            confidence=self.confidence.evaluate(
                movements=history, opening_weight=spool.opening_weight
            ),
            movement_count=len(history),
            last_movement_at=history[-1].occurred_at if history else None,
            has_anomaly=bool(
                self.anomalies.inspect(spool_id=spool.id, balance=current, location=spool.location)
            ),
        )

    async def overview(self, *, include_discarded: bool = False) -> list[SpoolSummary]:
        spools = await self.spools.list(SpoolFilter(include_discarded=include_discarded))
        summaries = [await self.summarise(spool) for spool in spools]
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
        spool = await self.spools.get(spool_id)
        if spool is None:
            raise SpoolNotFoundError(spool_id)
        history = await self.movements.list_for_spool(spool_id)
        return SpoolDetail(
            summary=await self.summarise(spool),
            lines=[
                HistoryLine(movement=line.movement, balance_after=line.balance_after)
                for line in reversed(running_balances(history))
            ],
        )

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
}
