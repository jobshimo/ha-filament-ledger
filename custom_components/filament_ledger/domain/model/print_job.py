"""What the printer said about one job.

Deliberately a *record of claims*, not a source of truth. Everything here is the printer's
report — the slicer's plan, the progress counters, the raw state strings — and the ledger
never trusts it further than docs/01-vision.md §1.1 allows. The entity's job is to hold
those claims verbatim so that estimation, review and retroactive reclassification all read
from the same preserved facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..error import InvalidValueError
from ..value.grams import Grams
from ..value.identifiers import PrintJobId, TrayRef
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

    **Keyed by `TrayRef`, not by a bare tray number.** The printer reports one figure per
    tray, and a tray is only identified once its printer and AMS unit are named — two
    machines both have a tray 1, so a figure keyed by the number alone would be deducted
    from whichever spool happened to be in *a* tray 1.

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

    **Four timestamps, two clocks, and the split is deliberate.** `started_at` and
    `ended_at` are the ledger's: the moments Home Assistant processed the lifecycle events,
    stamped from the same `Clock` every movement is stamped from. `printer_started_at` and
    `printer_ended_at` are the machine's own report of the same two moments, and they are
    *additional* rather than authoritative. Letting the printer's clock set the ledger's
    pair would put a foreign clock into `Movement.occurred_at`, which UC-04 derives from
    `ended_at` — and the ledger orders itself by `occurred_at`
    (docs/08-data-model.md §8.1). A printer running a few minutes slow would sort its print
    *before* a reconciliation that happened earlier in real time, which drops that print out
    of `movements_since_anchor` and hands the spool a confidence it has not earned. The
    printer's pair is read by `measured_duration` and by nothing else, so no ordering
    anywhere in the ledger can see it.
    """

    id: PrintJobId
    name: str
    state: PrintJobState
    started_at: datetime
    ended_at: datetime | None = None
    layer_reached: int | None = None
    total_layers: int | None = None
    progress: Percentage | None = None
    reported_usage: dict[TrayRef, Grams] | None = None
    raw_gcode_state: str | None = None
    raw_print_error: int | None = None
    printer_started_at: datetime | None = None
    printer_ended_at: datetime | None = None
    # UC-04's idempotency guard: set in the same transaction as the movements it covers,
    # because a print deducted twice is indistinguishable from a real duplicate after the
    # fact. Carried here from day one so the schema and the entity never disagree.
    consumption_recorded: bool = False

    @property
    def measured_duration(self) -> timedelta | None:
        """How long this print actually ran, or `None` when nothing here can say.

        The printer's own pair wins **for a job that reached `FINISHED`**, because there it
        is the better measurement: the ledger's pair is bounded by when Home Assistant
        *heard*, so a slow bus, a restart or an integration reload lands in that
        subtraction and none of it happened to the print. The clearest case is the row a
        restart leaves behind, where the ledger has no start at all and the machine does.

        **An interrupted job keeps the ledger's pair, and that is not a detail.** Upstream
        computes its end from the time remaining, so before a job stops that figure is a
        *prediction* of when it would finish. At a finish the prediction has converged on
        the present and is a measurement; at a cancellation forty minutes in, it is still
        pointing at the ending that never happened, and trusting it would report a print
        as hours longer than it ran. An interrupted print is different in kind — the same
        distinction docs/adr/0004 rests on — and this is where that costs something.

        Zero is not a duration, in either pair. On the ledger's that excludes exactly one
        row: the one `TrackPrintJob` writes when a restart swallowed a start and both
        timestamps became the moment the ending arrived (docs/06-ui-spec.md §6.7). On the
        printer's it also absorbs the incoherent pair — an ending read while `start_time`
        had already been reset for the next job — which is why the events record that pair
        verbatim and leave the sense-making here.
        """
        if self.state is PrintJobState.FINISHED:
            machine = _span(self.printer_started_at, self.printer_ended_at)
            if machine is not None:
                return machine
        return _span(self.started_at, self.ended_at)

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
        for tray, used in (self.reported_usage or {}).items():
            # Zero is tolerated: the printer legitimately reports 0 g for a tray the job
            # loaded but barely touched. Negative consumption is not a thing that exists.
            if used.is_negative:
                msg = f"reported usage for {tray} cannot be negative, got {used}"
                raise InvalidValueError(msg)


def _span(start: datetime | None, end: datetime | None) -> timedelta | None:
    """One clock's elapsed time, or `None` when that pair cannot describe a print.

    Half a pair says nothing, and a pair that does not move forward is not a duration —
    which covers both the zero-length restart row and a printer whose two moments arrived
    out of order.
    """
    if start is None or end is None or end <= start:
        return None
    return end - start
