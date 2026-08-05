"""Turns read models into the plain dictionaries the panel and the entities consume.

Presentation lives here, at the boundary. The domain deals in `Grams` and `Decimal`; the
wire deals in numbers a browser can render. Rounding happens once, in this module, so no two
surfaces can disagree about what 611.7 g looks like.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from ...application.query import (
    GlobalHistoryLine,
    HistoryLine,
    PendingReviewDetail,
    PrintTime,
    SpoolDetail,
    SpoolSummary,
    StatisticsView,
    TrashedMovement,
    TrashView,
    describe_location,
    movement_label,
    source_label,
)
from ...domain.model.movement import Movement
from ...domain.value.grams import Grams, total

if TYPE_CHECKING:
    # Type-only: `tray_sync` imports the gateway, which imports Home Assistant — and the
    # application test suite imports this module on machines without it. The functions
    # below only read attributes, so the types can stay annotations that never execute.
    from .bambu_gateway import JobStatus
    from .printer_state import PrinterSnapshot
    from .tray_sync import SlotSyncOutcome, TraySyncResult


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
        # Provenance, so the edit dialog knows whether the tag is the user's to change
        # (docs/14 §14.2). Null exactly when there is no tag.
        "tag_source": spool.tag_source.value if spool.tag_source else None,
        "movement_count": summary.movement_count,
        "last_movement_at": (
            summary.last_movement_at.isoformat() if summary.last_movement_at else None
        ),
        "has_anomaly": summary.has_anomaly,
        "registered_at": spool.registered_at.isoformat(),
    }


def entry_direction(movement: Movement) -> str:
    """Which way **this entry** went — read off its sign, not off its type.

    `MovementType.direction` answers a question about the *type*, and for the three
    correction types the answer is `EITHER`: a `REASSIGNMENT` is one type for both legs of
    a compensating pair, and a `VOID_REVERSAL` negates whatever it undoes. A row's actions
    turn on which way that particular row went — docs/14 §14.3 offers **[ Reassign… ]**
    only where there is a charge to move, and calls a reassignment's *debit leg*
    reassignable again — so the wire carries the entry's direction rather than its type's.
    For every fixed-direction type the two agree, because `Movement` refuses an amount its
    type does not permit.
    """
    return "DECREASE" if movement.amount.is_negative else "INCREASE"


def history_line(line: HistoryLine) -> dict[str, Any]:
    """One row of the spool detail table — the derivation surface, where nothing is hidden.

    `voided` is what the strike-through and the chip turn on; the two link fields are what
    a reassignment or a reinstatement row names. The rows stay in the table either way, so
    the visible sum still closes (docs/14 §14.4.5).
    """
    movement = line.movement
    return {
        "id": movement.id,
        # The same value under the name the row actions use, so the detail table and the
        # global table can share one dispatch path in the panel.
        "movement_id": movement.id,
        "type": movement.type.value,
        "label": movement_label(movement),
        "amount_g": grams(movement.amount),
        "balance_after_g": whole_grams(line.balance_after),
        "balance_after_exact_g": grams(line.balance_after),
        "source": movement.source.value,
        "source_label": source_label(movement.source),
        "occurred_at": movement.occurred_at.isoformat(),
        "note": movement.note,
        "direction": entry_direction(movement),
        "voided": line.voided,
        "reassigns_movement_id": movement.reassigns_movement_id,
        "reinstates_movement_id": movement.reinstates_movement_id,
    }


def movement_line(line: GlobalHistoryLine) -> dict[str, Any]:
    """One row of the global history table (docs/06 §6.6).

    Amounts carry one decimal and their sign, per this module's rule — the direction is
    data, not decoration. `job_name`, `review_id` and `note` are nullable: most rows have
    none of the three, and the table renders their absence rather than inventing filler.

    `movement_id`, `spool_id`, `direction` and `voided` ride along so a row can offer the
    right actions without a second query (docs/14 §14.4): Delete and Reassign both name a
    movement, Reassign needs to know a charge is what it is moving, and neither is offered
    on a row that is already out of the ledger's default view.
    """
    movement = line.movement
    return {
        "occurred_at": movement.occurred_at.isoformat(),
        "movement_id": movement.id,
        "spool_id": movement.spool_id,
        "spool_name": line.spool_name,
        "spool_colour": line.spool_colour.display_hex,
        "type": movement.type.value,
        "amount_g": grams(movement.amount),
        "direction": entry_direction(movement),
        "voided": line.voided,
        "source": movement.source.value,
        "job_name": line.job_name,
        "review_id": movement.review_id,
        "note": movement.note,
    }


def trash_result(view: TrashView) -> dict[str, Any]:
    """The Trash tab's two sections (docs/14 §14.4.4).

    Spools reuse the ordinary summary shape — the tab renders the same swatch, name,
    material and balance the inventory does, because a retracted spool is still the same
    object — plus the one fact that puts it here.
    """
    return {
        "spools": [
            {**spool_summary(summary), "deleted_at": _iso_or_none(summary.spool.deleted_at)}
            for summary in view.spools
        ],
        "movements": [_trashed_movement(entry) for entry in view.movements],
    }


def _trashed_movement(entry: TrashedMovement) -> dict[str, Any]:
    """One open void chapter.

    `restorable` is computed server-side, deliberately: it is the conjunction of two rules
    the use case also enforces — the void returned something, and the spool is in
    inventory — and a rule that lives only in panel JavaScript is a rule in the one
    untestable layer (docs/14 §14.8).
    """
    movement = entry.movement
    return {
        "movement_id": movement.id,
        "spool_id": entry.spool.id,
        "spool_name": entry.spool.display_name,
        "spool_colour": entry.spool.colour.display_hex,
        # The two retirement facts, not the derived `SpoolState`. A trash row never shows
        # SEALED or ACTIVE, and deriving those would cost a balance read per row to
        # produce a word nobody renders — while these two are what the explanatory line
        # under a missing [ Restore ] button has to choose between.
        "spool_deleted": entry.spool.is_deleted,
        "spool_discarded": entry.spool.is_discarded,
        "type": movement.type.value,
        "label": movement_label(movement),
        "amount_g": grams(movement.amount),
        "occurred_at": movement.occurred_at.isoformat(),
        "voided_at": entry.void.voided_at.isoformat(),
        "reason": entry.void.reason,
        "had_restitution": entry.void.had_restitution,
        "restorable": entry.restorable,
    }


def _iso_or_none(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def statistics_result(view: StatisticsView) -> dict[str, Any]:
    """One period's figures, as the Stats tab renders them (docs/06 §6.7).

    Every gram figure here is a **sum of exact `Grams` rounded exactly once**, which is
    this module's standing rule and matters more in aggregate than anywhere else: rounding
    each of forty prints to the gram and then adding them can be twenty grams out, and a
    statistics page that disagrees with the history it summarises is worse than no
    statistics page.

    Whole grams rather than tenths, for the reason §6.8 gives balances: a period's total is
    a figure somebody reads at a glance, and a tenth on top of a kilogram claims a
    precision that means nothing to the reader.

    `empty` is computed server-side from the exact values. The panel branches on it to
    choose the teaching empty state, and a rule that lives only in panel JavaScript is a
    rule in the one untestable layer (docs/14 §14.8).
    """
    return {
        "period": view.period.value,
        "since": _iso_or_none(view.since),
        "empty": view.is_empty,
        "consumed_g": whole_grams(view.consumed),
        "wasted_g": whole_grams(view.wasted),
        "prints": {
            "finished": view.prints.finished,
            "cancelled": view.prints.cancelled,
            "failed": view.prints.failed,
            "total": view.prints.total,
        },
        "reviews": {
            "approved": view.reviews.approved,
            "dismissed": view.reviews.dismissed,
            "total": view.reviews.total,
        },
        "by_colour": [
            {"colour": entry.colour.display_hex, "grams": whole_grams(entry.grams)}
            for entry in view.by_colour
        ],
        "by_material": [
            {"material": entry.material, "grams": whole_grams(entry.grams)}
            for entry in view.by_material
        ],
        "top_prints": [
            {
                "job_id": entry.job_id,
                "name": entry.name,
                "started_at": entry.started_at.isoformat(),
                "grams": whole_grams(entry.grams),
            }
            for entry in view.top_prints
        ],
        "print_time": _print_time(view.print_time),
    }


def _print_time(measured: PrintTime | None) -> dict[str, Any] | None:
    """Total and average print time in whole minutes, or **null** when nothing in the
    period had a measurable duration.

    Null rather than zeros: a period with no timed print has no print time, and a card
    reading `0 min` would be a claim about the printer rather than about the data. The
    minute is the unit because that is the precision a print duration is remembered in —
    and both figures round from the exact `timedelta`, the average from the exact total
    rather than from the rounded one.
    """
    if measured is None:
        return None
    return {
        "total_minutes": _minutes(measured.total),
        "average_minutes": _minutes(measured.average),
        "prints": measured.prints,
    }


def _minutes(span: timedelta) -> int:
    return int((Decimal(span.total_seconds()) / 60).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def tray_sync_result(result: TraySyncResult) -> dict[str, Any]:
    """What one on-demand sync reports (docs/05 §5.6), slot by slot.

    `dormant` is the honest no-printer flag the panel branches on: an empty `slots` with
    `dormant` false means the printer reported no usable trays, which is a different fact
    from there being no printer to ask.
    """
    return {
        "dormant": result.dormant,
        "slots": [_slot_sync(outcome) for outcome in result.slots],
    }


def _slot_sync(outcome: SlotSyncOutcome) -> dict[str, Any]:
    """The hints ride along for `unknown_tag`: the register form pre-fills from them, so
    the user confirms the one number the tray cannot report (docs/06 §6.4)."""
    reading = outcome.reading
    spool = outcome.spool
    return {
        "slot": reading.slot.value,
        "status": outcome.status.value,
        "tag_uid": reading.tag.value if reading.tag else None,
        "name_hint": reading.name,
        "material_hint": reading.material,
        "colour_hint": reading.colour.display_hex if reading.colour else None,
        "spool_id": spool.id if spool else None,
        "spool_name": spool.display_name if spool else None,
    }


def printer_state(snapshot: PrinterSnapshot) -> dict[str, Any]:
    """The Printer tab's read-only glance (docs/14 §14.5).

    A dormant gateway answers `{"dormant": true}` **and nothing else**: the panel renders
    the teaching empty state, and shipping a hull of nulls beside the flag would invite it
    to render dashes for a printer that is not there.

    Every other figure is nullable, and null means *the printer did not say*. The panel
    renders each one as a dash, never as a zero — a missing figure is not a figure of zero
    (docs/04 UC-04 step 2's principle, applied to display).
    """
    if snapshot.dormant:
        return {"dormant": True}
    job = snapshot.job
    return {
        "dormant": False,
        "status": job.status if job else None,
        "progress_pct": job.progress.rounded if job and job.progress is not None else None,
        "current_layer": job.current_layer if job else None,
        "total_layers": job.total_layers if job else None,
        "job_name": job.name if job else None,
        "error": _printer_error(job),
        # Null until their upstream translation keys are verified on the reference
        # instance and frozen (`FUTURE_PRINT_SENSOR_KEYS`). An undiscovered sensor
        # serialises as null, never as an invented value — the gateway's standing policy.
        "online": snapshot.online,
        "connection_mode": snapshot.connection_mode,
        "active_tray": snapshot.active_tray,
        "trays": [_slot_sync(outcome) for outcome in snapshot.trays],
    }


def _printer_error(job: JobStatus | None) -> dict[str, Any] | None:
    """The error sensor, or null when it said nothing.

    The code crosses the wire as a **decimal string**, the same rule the review card's
    `raw_print_error` follows: HMS codes are 64-bit — 0x0300010000020001 already exceeds
    2^53 — and a JSON number lands in JavaScript as a double, corrupting the code before
    the panel's `hms()` could format it.
    """
    if job is None or job.error is None:
        return None
    code = job.error.code
    return {"active": job.error.active, "code": str(code) if code is not None else None}


def spool_detail(detail: SpoolDetail) -> dict[str, Any]:
    return {
        **spool_summary(detail.summary),
        "history": [history_line(line) for line in detail.lines],
    }


def pending_review(detail: PendingReviewDetail) -> dict[str, Any]:
    """One queue item, as the review card renders it (docs/06 §6.3).

    Estimates carry one decimal, like movement amounts — an estimate is a proposed
    movement. The total accumulates exact grams and rounds once, per this module's rule.
    `raw_print_error` travels as a DECIMAL STRING, or null: HMS codes are 64-bit
    integers — 0x0300010000020001 already exceeds 2^53 — and a JSON number crosses the
    websocket into a JavaScript double, which would corrupt the code before the panel
    could format it. The database keeps the verbatim integer; only the wire
    representation changes. Formatting it into the searchable HMS quad is the card's
    display work, not a fact about the job.

    A line is one **tray**: its estimate, and the charges that attribute it. `charges` is
    the *frozen* attribution — empty says no spool was mounted when the review opened,
    which is the row the approval flow asks the user to complete, and more than one entry
    says the tray fed from more than one spool. It is a list rather than a single spool
    because the card has to be able to render the split, and a wire shape that could only
    carry one spool would put the panel back where the model was.
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
        "raw_print_error": str(job.raw_print_error) if job.raw_print_error is not None else None,
        "estimated_total_g": grams(total([line.estimated for line in review.lines])),
        "lines": [
            {
                "slot": line.slot.value,
                "estimated_g": grams(line.estimated),
                "charges": [
                    {"spool_id": charge.spool_id, "amount_g": grams(charge.amount)}
                    for charge in line.charges
                ],
            }
            for line in review.lines
        ],
    }
