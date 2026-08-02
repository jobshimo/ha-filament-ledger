"""Derives how much a balance can be trusted.

    LOW     if `since` contains any ESTIMATED_CONSUMPTION
    MEDIUM  else if total consumed in `since` >= 20% of opening weight
    HIGH    otherwise

Evaluated top-down, first match wins, where `since` is every movement after the most recent
RECONCILIATION — or every movement at all, if the spool has never been reconciled.

The function is **total**. Every spool lands on exactly one level, including one registered
thirty seconds ago with a single OPENING_BALANCE movement, which is HIGH. That matters
because confidence appears next to every balance in the product: a spool the rules do not
cover is a spool whose dot colour depends on which branch an implementer wrote first.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..model.movement import Movement
from ..value.confidence import Confidence
from ..value.grams import Grams
from ..value.movement_type import MovementType
from .balance_calculator import consumed

DEFAULT_MEDIUM_CONSUMPTION_RATIO = Decimal("0.20")


@dataclass(frozen=True, slots=True)
class ConfidenceThresholds:
    """Provisional by design.

    These are informed guesses. They are meant to be tuned against real usage in Phase 4,
    not defended.
    """

    medium_consumption_ratio: Decimal = DEFAULT_MEDIUM_CONSUMPTION_RATIO


@dataclass(frozen=True, slots=True)
class ConfidenceEvaluator:
    thresholds: ConfidenceThresholds = ConfidenceThresholds()

    def evaluate(self, *, movements: Sequence[Movement], opening_weight: Grams) -> Confidence:
        recent = movements_since_anchor(movements)

        if any(movement.is_estimate for movement in recent):
            return Confidence.LOW

        drawn = consumed(recent)
        if drawn.ratio_to(opening_weight) >= self.thresholds.medium_consumption_ratio:
            return Confidence.MEDIUM

        return Confidence.HIGH


def movements_since_anchor(movements: Sequence[Movement]) -> Sequence[Movement]:
    """Every movement strictly **after** the anchor.

    The anchor is the most recent `RECONCILIATION`, or the `OPENING_BALANCE` when the spool
    has never been reconciled. The opening balance counts as one because it is a
    human-confirmed number: the user read it off the packaging or weighed the spool. It is
    the weakest possible anchor, but it is an anchor, and treating it as one is what stops a
    freshly registered spool being displayed as untrustworthy on the day it is registered.

    The anchor itself is excluded, in both cases. It has to be: an anchor is the moment the
    balance was last known to be right, and consumption *at* that moment is already inside
    the figure it established.
    """
    for position in range(len(movements) - 1, -1, -1):
        if movements[position].is_reconciliation:
            return movements[position + 1 :]

    for position, movement in enumerate(movements):
        if movement.type is MovementType.OPENING_BALANCE:
            return movements[position + 1 :]

    # No anchor at all — a partial history, as an infrastructure query by date can return.
    # Everything is unaccounted for, which is the conservative reading.
    return movements
