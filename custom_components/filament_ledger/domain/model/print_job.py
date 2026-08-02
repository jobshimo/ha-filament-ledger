"""What the printer said about one job.

Deliberately a *record of claims*, not a source of truth. Everything here is the printer's
report — the slicer's plan, the progress counters, the raw state strings — and the ledger
never trusts it further than docs/01-vision.md §1.1 allows. The entity's job is to hold
those claims verbatim so that estimation, review and retroactive reclassification all read
from the same preserved facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..error import InvalidValueError
from ..value.grams import Grams
from ..value.identifiers import PrintJobId, SlotIndex
from ..value.percentage import Percentage
from ..value.print_job_state import PrintJobState


@dataclass(frozen=True, slots=True)
class PrintJob:
    """One print, as reported. Frozen for the same reason `Spool` is.

    `reported_usage` carries the slicer's per-tray totals — the `used_g` figures
    `ha-bambulab` parses out of the sliced `.3mf` (docs/07-consumption-estimation.md §7.2).
    The same numbers serve two readings: for a `FINISHED` job they are what the print
    consumed, because the plan was carried out in full; for an interrupted job they are the
    *totals the plan would have consumed*, which is exactly what `LinearProgressEstimator`
    scales by progress. One field, because the printer reports one set of numbers.

    `None` and an empty mapping are different facts, and the schema keeps the column
    nullable for that reason: `None` means the per-tray figure never materialised — a known
    failure of `.3mf` retrieval in LAN mode — while `{}` would mean the printer reported
    and named no trays. A missing figure is not a figure of zero (docs/04-use-cases.md
    UC-04); collapsing the two would turn a retrieval failure into a silent claim that
    nothing was consumed.

    `raw_print_error` stays the verbatim integer, matching the column. Formatting it into
    the searchable HMS quad string is display work for the review card, not a fact about
    the job — and reformatting on the way in would destroy the very verbatimness that
    makes reclassification possible (docs/07-consumption-estimation.md §7.7).
    """

    id: PrintJobId
    name: str
    state: PrintJobState
    started_at: datetime
    ended_at: datetime | None = None
    layer_reached: int | None = None
    total_layers: int | None = None
    progress: Percentage | None = None
    reported_usage: dict[SlotIndex, Grams] | None = None
    raw_gcode_state: str | None = None
    raw_print_error: int | None = None
    # UC-04's idempotency guard: set in the same transaction as the movements it covers,
    # because a print deducted twice is indistinguishable from a real duplicate after the
    # fact. Carried here from day one so the schema and the entity never disagree.
    consumption_recorded: bool = False

    def __post_init__(self) -> None:
        if self.layer_reached is not None and self.layer_reached < 0:
            msg = f"layer_reached cannot be negative, got {self.layer_reached}"
            raise InvalidValueError(msg)
        if self.total_layers is not None and self.total_layers < 1:
            msg = f"total_layers must be >= 1 when known, got {self.total_layers}"
            raise InvalidValueError(msg)
        if self.ended_at is not None and self.ended_at < self.started_at:
            msg = f"job cannot end at {self.ended_at} before starting at {self.started_at}"
            raise InvalidValueError(msg)
        for slot, used in (self.reported_usage or {}).items():
            # Zero is tolerated: the printer legitimately reports 0 g for a tray the job
            # loaded but barely touched. Negative consumption is not a thing that exists.
            if used.is_negative:
                msg = f"reported usage for slot {slot} cannot be negative, got {used}"
                raise InvalidValueError(msg)
