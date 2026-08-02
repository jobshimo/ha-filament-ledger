"""Turns read models into the plain dictionaries the panel and the entities consume.

Presentation lives here, at the boundary. The domain deals in `Grams` and `Decimal`; the
wire deals in numbers a browser can render. Rounding happens once, in this module, so no two
surfaces can disagree about what 611.7 g looks like.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ...application.query import (
    HistoryLine,
    PendingReviewDetail,
    SpoolDetail,
    SpoolSummary,
    describe_location,
    movement_label,
    source_label,
)
from ...domain.value.grams import Grams, total


def grams(value: Grams) -> float:
    """Movement amounts carry one decimal — a single movement is known to that precision."""
    return float(value.as_decimal.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def whole_grams(value: Grams) -> int:
    """Balances round to whole grams.

    The tenth is arithmetically real and physically meaningless: a kitchen scale reads to
    the gram, and showing a decimal claims a precision the system cannot back up.
    """
    return int(value.as_decimal.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def stock_grams(summaries: Iterable[SpoolSummary]) -> int:
    """Total stock, rounded exactly once.

    Accumulate exact `Grams` and round the sum — never sum per-spool roundings. Three
    0.3 g spools hold 0.9 g of stock, which is 1 g; rounding each spool first would call
    it 0. `Queries.stock()` already follows this rule; this helper is the entity surface
    doing the same, so the sensor and the websocket cannot disagree about one ledger.
    """
    return whole_grams(total([s.balance for s in summaries if s.state.counts_as_stock]))


def stock_per_material(summaries: Iterable[SpoolSummary]) -> dict[str, int]:
    """Per-material stock totals, each rounded exactly once — same rule as `stock_grams`."""
    per_material: dict[str, Grams] = {}
    for summary in summaries:
        if not summary.state.counts_as_stock:
            continue
        key = summary.spool.material.display_name
        per_material[key] = per_material.get(key, Grams.zero()) + summary.balance
    return {name: whole_grams(amount) for name, amount in per_material.items()}


def spool_summary(summary: SpoolSummary) -> dict[str, Any]:
    spool = summary.spool
    return {
        "id": spool.id,
        "name": spool.display_name,
        "label": spool.label,
        "vendor": spool.vendor,
        "material": spool.material.display_name,
        "material_kind": spool.material.kind.value,
        "colour": spool.colour.display_hex,
        "colour_hex8": spool.colour.hex8,
        "foreground": spool.colour.foreground.display_hex,
        "balance_g": whole_grams(summary.balance),
        "balance_exact_g": grams(summary.balance),
        "opening_weight_g": whole_grams(spool.opening_weight),
        "core_weight_g": whole_grams(spool.core_weight),
        "percentage": summary.percentage,
        "state": summary.state.value,
        "confidence": summary.confidence.value,
        "needs_weighing": summary.confidence.needs_weighing,
        "location": describe_location(spool.location),
        "tag_uid": spool.tag_uid.value if spool.tag_uid else None,
        "movement_count": summary.movement_count,
        "last_movement_at": (
            summary.last_movement_at.isoformat() if summary.last_movement_at else None
        ),
        "has_anomaly": summary.has_anomaly,
        "registered_at": spool.registered_at.isoformat(),
    }


def history_line(line: HistoryLine) -> dict[str, Any]:
    movement = line.movement
    return {
        "id": movement.id,
        "type": movement.type.value,
        "label": movement_label(movement),
        "amount_g": grams(movement.amount),
        "balance_after_g": whole_grams(line.balance_after),
        "balance_after_exact_g": grams(line.balance_after),
        "source": movement.source.value,
        "source_label": source_label(movement.source),
        "occurred_at": movement.occurred_at.isoformat(),
        "note": movement.note,
    }


def spool_detail(detail: SpoolDetail) -> dict[str, Any]:
    return {
        **spool_summary(detail.summary),
        "history": [history_line(line) for line in detail.lines],
    }


def pending_review(detail: PendingReviewDetail) -> dict[str, Any]:
    """One queue item, as the review card renders it (docs/06 §6.3).

    Estimates carry one decimal, like movement amounts — an estimate is a proposed
    movement. The total accumulates exact grams and rounds once, per this module's rule.
    `raw_print_error` travels as the verbatim integer: formatting it into the searchable
    HMS quad is the card's display work, not a fact about the job. A line's `spool_id` is
    the *frozen* resolution — `null` says no spool was mounted when the review opened,
    which is the row the approval flow asks the user to complete.
    """
    review = detail.review
    job = detail.job
    return {
        "id": review.id,
        "job_id": review.job_id,
        "job_name": job.name,
        "job_state": job.state.value,
        "reason": review.reason.value,
        "estimator": review.estimator_used.value,
        "opened_at": review.opened_at.isoformat(),
        "layer_reached": job.layer_reached,
        "total_layers": job.total_layers,
        "progress_pct": job.progress.rounded if job.progress is not None else None,
        "raw_gcode_state": job.raw_gcode_state,
        "raw_print_error": job.raw_print_error,
        "estimated_total_g": grams(total([line.estimated for line in review.lines])),
        "lines": [
            {
                "slot": line.slot.value,
                "estimated_g": grams(line.estimated),
                "spool_id": line.spool_id,
            }
            for line in review.lines
        ],
    }
