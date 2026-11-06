"""The ledger entry.

**Immutable after creation.** No setters, no update method, no delete. The repository port
exposes no mutation either, and the database enforces the same with triggers — three
independent enforcements of the one rule that, if it fails, makes every number this product
reports untrustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..error import InvalidValueError
from ..value.grams import Grams
from ..value.identifiers import MovementId, PrintJobId, ReviewId, SpoolId, new_movement_id
from ..value.movement_type import MovementSource, MovementType


@dataclass(frozen=True, slots=True)
class Movement:
    id: MovementId
    spool_id: SpoolId
    type: MovementType
    amount: Grams
    source: MovementSource
    occurred_at: datetime
    recorded_at: datetime
    note: str | None = None
    job_id: PrintJobId | None = None
    review_id: ReviewId | None = None
    # Correction provenance, on the entry itself (docs/14 §14.7). Written at INSERT and
    # never after — which is why the immutability triggers are never confronted by the
    # corrections this release adds. A `REASSIGNMENT` leg names the charge it moves; a
    # `REINSTATEMENT` names the entry it brings back. The third link, void → reversal,
    # lives on the `movement_void` row instead: it is a status record *about* a movement,
    # and the movements it points at stay untouched (docs/adr/0007).
    reassigns_movement_id: MovementId | None = None
    reinstates_movement_id: MovementId | None = None

    opening_basis: str = "LEGACY_UNSPECIFIED"
    gross_weight_mg: int | None = None
    tare_weight_mg: int | None = None

    def __post_init__(self) -> None:
        if self.opening_basis not in {
            "DECLARED_NET",
            "MEASURED_NET",
            "MEASURED_GROSS",
            "CONFIGURED_DEFAULT",
            "DETECTED_NOMINAL",
            "LEGACY_UNSPECIFIED",
        }:
            raise InvalidValueError("Invalid opening basis")
        if (
            self.type is not MovementType.OPENING_BALANCE
            and self.opening_basis != "LEGACY_UNSPECIFIED"
        ):
            raise InvalidValueError("Opening provenance applies only to opening balances")
        if self.opening_basis == "MEASURED_GROSS":
            if (
                self.type is not MovementType.OPENING_BALANCE
                or self.gross_weight_mg is None
                or self.tare_weight_mg is None
                or self.tare_weight_mg < 0
                or self.gross_weight_mg <= self.tare_weight_mg
                or self.amount.milligrams != self.gross_weight_mg - self.tare_weight_mg
            ):
                raise InvalidValueError(
                    "Gross opening quantity must equal gross minus non-negative tare"
                )
        elif self.gross_weight_mg is not None or self.tare_weight_mg is not None:
            raise InvalidValueError("Gross and tare apply only to measured gross openings")
        if self.amount.is_zero:
            msg = f"{self.type} of zero records nothing and only adds noise"
            raise InvalidValueError(msg)
        if not self.type.permits(self.amount.milligrams):
            msg = (
                f"{self.type} must be {self.type.direction.lower()}, got {self.amount.as_decimal} g"
            )
            raise InvalidValueError(msg)

    @property
    def is_estimate(self) -> bool:
        """Whether this entry came from inferring how far an interrupted print got."""
        return self.type is MovementType.ESTIMATED_CONSUMPTION

    @property
    def is_reconciliation(self) -> bool:
        return self.type is MovementType.RECONCILIATION


def record(
    *,
    spool_id: SpoolId,
    type: MovementType,  # the domain word is "type"; renaming it here would make every
    amount: Grams,  # call site read worse than this single shadowed name does.
    source: MovementSource,
    occurred_at: datetime,
    recorded_at: datetime | None = None,
    note: str | None = None,
    job_id: PrintJobId | None = None,
    review_id: ReviewId | None = None,
    reassigns_movement_id: MovementId | None = None,
    reinstates_movement_id: MovementId | None = None,
    opening_basis: str = "LEGACY_UNSPECIFIED",
    gross_weight_mg: int | None = None,
    tare_weight_mg: int | None = None,
) -> Movement:
    """Build a movement, generating its identity.

    `occurred_at` and `recorded_at` are separate facts: a print may have finished while Home
    Assistant was down. Collapsing them loses one.
    """
    return Movement(
        id=new_movement_id(),
        spool_id=spool_id,
        type=type,
        amount=amount,
        source=source,
        occurred_at=occurred_at,
        recorded_at=recorded_at if recorded_at is not None else occurred_at,
        note=note,
        job_id=job_id,
        review_id=review_id,
        reassigns_movement_id=reassigns_movement_id,
        reinstates_movement_id=reinstates_movement_id,
        opening_basis=opening_basis,
        gross_weight_mg=gross_weight_mg,
        tare_weight_mg=tare_weight_mg,
    )


@dataclass(frozen=True, slots=True)
class MovementLine:
    """A movement paired with the balance that stood after it.

    The history view exists to make the arithmetic visible rather than asserted, which it
    can only do if each row carries its running total.
    """

    movement: Movement
    balance_after: Grams
