"""Turns read models into the plain dictionaries the panel and the entities consume.

Presentation lives here, at the boundary. The domain deals in `Grams` and `Decimal`; the
wire deals in numbers a browser can render. Rounding happens once, in this module, so no two
surfaces can disagree about what 611.7 g looks like.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ...application.query import (
    HistoryLine,
    SpoolDetail,
    SpoolSummary,
    describe_location,
    movement_label,
    source_label,
)
from ...domain.value.grams import Grams


def grams(value: Grams) -> float:
    """Movement amounts carry one decimal — a single movement is known to that precision."""
    return float(value.as_decimal.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def whole_grams(value: Grams) -> int:
    """Balances round to whole grams.

    The tenth is arithmetically real and physically meaningless: a kitchen scale reads to
    the gram, and showing a decimal claims a precision the system cannot back up.
    """
    return int(value.as_decimal.quantize(Decimal(1), rounding=ROUND_HALF_UP))


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
