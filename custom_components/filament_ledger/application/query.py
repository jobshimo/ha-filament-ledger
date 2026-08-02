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
from ..domain.model.spool import Spool
from ..domain.port.repositories import MovementRepository, SpoolFilter, SpoolRepository
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
class Queries:
    spools: SpoolRepository
    movements: MovementRepository
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
