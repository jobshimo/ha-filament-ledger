"""Derives how much a balance can be trusted.

    LOW     if `since` contains any ESTIMATED_CONSUMPTION,
            or total consumed in `since` >= 41% of opening weight
    MEDIUM  else if total consumed in `since` >= 20% of opening weight
    HIGH    otherwise

Evaluated top-down, first match wins, where `since` is every movement after the most recent
RECONCILIATION — or every movement at all, if the spool has never been reconciled.

**LOW is reached two ways, and the level does not say which.** That is deliberate: the level
answers *how much should I trust this number*, and both routes answer it the same way —
weigh the spool. Which route was taken is a separate question, answered by the basis the
application layer assembles beside the level (`ConfidenceBasis`, `application/query.py`), so
a user is never shown a badge that changed for a reason nothing on screen names.

The function is **total**. Every spool lands on exactly one level, including one registered
thirty seconds ago with a single OPENING_BALANCE movement, which is HIGH. That matters
because confidence appears next to every balance in the product: a spool the rules do not
cover is a spool whose dot colour depends on which branch an implementer wrote first.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from ..model.movement import Movement
from ..value.confidence import Confidence
from ..value.grams import Grams
from ..value.movement_type import MovementType
from .balance_calculator import consumed

DEFAULT_MEDIUM_CONSUMPTION_RATIO = Decimal("0.20")

# Where drawing more filament stops being a nudge and becomes a reason to reach for a scale.
#
# One threshold gave a two-position ladder: a reel a fifth drawn and a reel drawn to the core
# wore the same badge, so the signal stopped carrying information after roughly one ordinary
# print. This is the rung above it, and it is derived rather than picked.
#
# Two reconciliations on the reference instance are the whole of what has been measured
# (docs/07 §7.5): 50.0 g adrift over 220.0 g drawn, and 333.1 g adrift over 916.9 g drawn.
# Two points are not a curve and are not fitted as one — the worse of the two rates, 36.3% of
# whatever has been drawn, is taken as a bound on how far the balance can wander.
#
# `AnomalyDetector` already names the disagreement worth telling the user about: a
# reconciliation delta reaching 15% of the opening weight (docs/02 §2.5). Under that bound
# the drift reaches 15% of the reel once 41.3% of it has been drawn — past which weighing
# would plausibly raise an anomaly rather than confirm a number, and *weigh this when you get
# a chance* is exactly what LOW means. The figure is rounded **down** to 41% for the reason
# the anomaly boundary is inclusive: it errs toward telling the user.
DEFAULT_LOW_CONSUMPTION_RATIO = Decimal("0.41")


@dataclass(frozen=True, slots=True)
class ConfidenceThresholds:
    """Provisional by design.

    These are informed guesses. They are meant to be tuned against real usage in Phase 4,
    not defended.
    """

    medium_consumption_ratio: Decimal = DEFAULT_MEDIUM_CONSUMPTION_RATIO
    low_consumption_ratio: Decimal = DEFAULT_LOW_CONSUMPTION_RATIO


@dataclass(frozen=True, slots=True)
class ConfidenceEvaluator:
    thresholds: ConfidenceThresholds = field(default_factory=ConfidenceThresholds)

    def evaluate(self, *, movements: Sequence[Movement], opening_weight: Grams) -> Confidence:
        recent = movements_since_anchor(movements)

        if any(movement.is_estimate for movement in recent):
            return Confidence.LOW

        drawn = consumed(recent).ratio_to(opening_weight)
        if drawn >= self.thresholds.low_consumption_ratio:
            return Confidence.LOW
        if drawn >= self.thresholds.medium_consumption_ratio:
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

    A history with no anchor at all is taken whole — a partial slice, as an infrastructure
    query by date can return. Everything in it is unaccounted for, which is the conservative
    reading.
    """
    position = _anchor_position(movements)
    return movements if position is None else movements[position + 1 :]


def anchor_movement(movements: Sequence[Movement]) -> Movement | None:
    """The entry the window opens after: the last moment the balance was known to be right.

    The same anchor `movements_since_anchor` measures from, returned rather than dropped,
    because *since you weighed it* and *since you registered it* are different claims and a
    surface explaining a spool's confidence has to be able to say which one it is making.

    `None` when the history carries no anchor at all — nothing in it was ever confirmed, so
    there is nothing to name.
    """
    position = _anchor_position(movements)
    return None if position is None else movements[position]


def _anchor_position(movements: Sequence[Movement]) -> int | None:
    """Where the anchor sits, so both readings of it are derived from one rule."""
    for position in range(len(movements) - 1, -1, -1):
        if movements[position].is_reconciliation:
            return position

    for position, movement in enumerate(movements):
        if movement.type is MovementType.OPENING_BALANCE:
            return position

    return None
