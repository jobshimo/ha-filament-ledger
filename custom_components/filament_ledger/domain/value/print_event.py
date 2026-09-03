"""What the printer reports about a job's lifecycle, translated to domain terms.

Two moments cross the boundary: a job starting, and a job stopping. Everything here is
already in the domain's vocabulary — tray references, grams, a terminal state — because the
gateway translates before anything crosses this line, exactly as it does for
`TrayReading`. Which bus event fired, which sensor carried each figure, and what the
per-tray attribute keys looked like are boundary concerns that stay in the adapter
(docs/05-ha-integration.md §5.8).

Every optional field means *unavailable*, never zero. A printer that reported nothing has
not reported nothing-was-consumed (docs/03-architecture.md §3.8).

**Both moments name their machine, and the field is required.** The bus carries one event
type for every printer in the house and the payload names only a device, so *which machine
said this* is knowledge the adapter has and the receiving use case cannot recover: an
ending that arrived anonymously would be correlated against whatever job happened to be
running, which with two printers is a coin toss played with somebody's inventory
(`application/track_print_job.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..error import InvalidValueError
from .print_job_state import PrintJobState

if TYPE_CHECKING:
    from datetime import datetime

    from .grams import Grams
    from .identifiers import PrinterSerial, TrayRef
    from .percentage import Percentage

# What a job is called when no name sensor can say. Never blank: the review card and the
# notification both lead with the name, and an empty string reads as a rendering bug rather
# than an honest unknown. It lives with the events rather than in the adapter that answers
# it because the receiving use case has to recognise it: a row opened under this name is
# corrected by the first observation that carries a real one, and no row is ever renamed
# *to* it (`TrackPrintJob._plan_observed`).
UNKNOWN_JOB_NAME = "unknown print"


@dataclass(frozen=True, slots=True)
class PrintStarted:
    """A job began.

    `printer` is the machine that began it — the module docstring says why it is required.

    `plan` carries the slicer's per-tray totals when a gateway can honestly say they
    describe *this* job — the same figures `PrintJob.reported_usage` preserves. `None`
    records that they were not known when the job began, never a claim of zero.

    **The Bambu gateway always sends `None`, and that is not a gap to be closed by
    reading the sensor here.** Measured on the reference machine: the weight sensor is
    republished about three-quarters of a minute *after* the start event fires, so
    anything standing on it at this moment belongs to the print before — capturing it is
    how a 937-layer job was charged the 2.1 g of its predecessor
    (docs/12-field-notes.md, 2026-08-08). That adapter therefore follows the sensor
    through the job and reports the figures on the *ending* instead. The field stays
    because the port permits a gateway that genuinely knows the plan up front, and
    because `TrackPrintJob` must not let a figureless ending erase one that did.

    `printer_started_at` is the machine's own answer to *when did this print begin*, and
    the name says whose clock it is. The ledger stamps its own moment when it hears the
    event; these two are different facts and the field exists so neither has to pretend to
    be the other (docs/04-use-cases.md UC-04, docs/08-data-model.md §8.1).

    **`derived` says whether the printer announced this start or the ledger inferred it**,
    and it is the exact mirror of the flag `PrintEnded` carries — written here for the same
    reason and enforced from the opposite side.

    An announced start is a discrete event, and it means *a print began just now*: a row is
    created unconditionally, because that is what the machine said happened.

    An inferred start is read off a *level* — the stage sensor says `printing` for as long
    as the job runs, and it flickers back into it on every reconnect. So it may only open a
    row the ledger does **not** already hold, and it identifies the job by
    `printer_started_at` rather than by the level being set. Without that limit every
    `offline → printing` bounce would mint a phantom print, and the reference machine did
    ten of those in the thirty-six minutes of one job (docs/12-field-notes.md, 2026-08-10).

    Inference exists because the announced start is **not delivered when it is needed
    most**, which is the same defect `_DERIVED_OUTCOMES` answers for the ending: upstream
    guards `event_print_started` with `previous_gcode_state != "unknown"`
    (`pybambu/models.py`), and a reconnection resets that to `unknown`. A machine whose
    connection drops before its own start therefore announces nothing at all, the ledger
    opens no row, and the ending that follows finds nothing to close — so the whole print,
    and every gram it consumed, is lost in silence. Measured on the reference instance
    (docs/12-field-notes.md, 2026-08-11): a 291.42 g job was running at 68 % with no row in
    the ledger, after the integration dropped for 12.3 s at 22:38:28 and returned to
    `running` at 22:38:40. The 248.41 g `MANUAL_ADJUSTMENT` of 2026-08-08 is the same
    failure, paid for by hand.
    """

    name: str
    printer: PrinterSerial
    plan: dict[TrayRef, Grams] | None = None
    printer_started_at: datetime | None = None
    derived: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "a print event cannot carry a blank job name"
            raise InvalidValueError(msg)
        _refuse_negative_usage(self.plan)


@dataclass(frozen=True, slots=True)
class PrintEnded:
    """A job stopped — finished, cancelled or failed.

    `printer` is the machine that stopped it, and it is what this ending will be correlated
    against — the module docstring says why it is required.

    `outcome` is read off the upstream event type and nothing else (Q1, closed): the
    classification is made by code that reads the MQTT stream for a living, and
    `raw_gcode_state` plus `raw_print_error` travel verbatim so a wrong classification
    stays recoverable (docs/07-consumption-estimation.md §7.7).

    The progress figures are the moment's readings, captured because an interrupted job
    will be estimated from them; `reported_usage` is whatever per-tray figures the weight
    sensor held when the job ended.

    **`derived` says whether the printer announced this ending or the ledger inferred it**,
    and it is the difference between an ending that may open a job and one that may only
    close one. An announced ending is a discrete event: it fired once, because the machine
    said so, so a row missing for it means the ledger was not listening and creating one is
    the honest repair (`TrackPrintJob._ended`). An inferred ending is read off a *level* —
    the status sensor rests in `finish` for hours between prints and flickers back into it
    on every reconnect, five times in ten minutes on the reference machine
    (docs/12-field-notes.md, 2026-08-08) — so letting one open a job would mint a phantom
    print, and charge its predecessor's plan again, on every restart and every dropout.
    Inference is trustworthy about a job the ledger already knows is running and about
    nothing else, and this flag is where that limit is written down rather than assumed.

    `printer_started_at` and `printer_ended_at` are the machine's own pair, read at the
    ending because that is the last moment they describe *this* job. Both are recorded
    whatever the outcome — they are the printer's claims and the record keeps claims
    verbatim — but only a `FINISHED` job's pair is a *measurement*, because upstream's end
    is computed from the time remaining and stops being a prediction only once the job has
    actually ended. `PrintJob.measured_duration` is where that distinction is made.

    **Their order is not checked here** either. A boundary that refused an incoherent pair
    would turn one glitch in somebody else's clock into a lost job row and a lost review,
    so the sense-making happens where it costs nothing.
    """

    outcome: PrintJobState
    name: str
    printer: PrinterSerial
    layer_reached: int | None = None
    total_layers: int | None = None
    progress: Percentage | None = None
    reported_usage: dict[TrayRef, Grams] | None = None
    raw_gcode_state: str | None = None
    raw_print_error: int | None = None
    printer_started_at: datetime | None = None
    printer_ended_at: datetime | None = None
    derived: bool = False

    def __post_init__(self) -> None:
        if not self.outcome.is_terminal:
            msg = f"a print cannot end in {self.outcome}; RUNNING is what ending leaves"
            raise InvalidValueError(msg)
        if not self.name.strip():
            msg = "a print event cannot carry a blank job name"
            raise InvalidValueError(msg)
        # The same bounds `PrintJob` enforces, checked here so a malformed reading is
        # refused where it is built rather than deep inside a use case.
        if self.layer_reached is not None and self.layer_reached < 0:
            msg = f"layer_reached cannot be negative, got {self.layer_reached}"
            raise InvalidValueError(msg)
        if self.total_layers is not None and self.total_layers < 1:
            msg = f"total_layers must be >= 1 when known, got {self.total_layers}"
            raise InvalidValueError(msg)
        _refuse_negative_usage(self.reported_usage)


def _refuse_negative_usage(usage: dict[TrayRef, Grams] | None) -> None:
    for tray, used in (usage or {}).items():
        if used.is_negative:
            msg = f"usage for {tray} cannot be negative, got {used}"
            raise InvalidValueError(msg)


@dataclass(frozen=True, slots=True)
class PrintPlanObserved:
    """The machine published per-tray figures while a job was running.

    **The moment between the two the port used to know about**, and it exists because the
    figures were being received, held, and then lost. A Bambu gateway cannot report the
    plan at the start — the weight sensor republishes about three-quarters of a minute
    later, so anything standing on it then belongs to the print before — so it followed the
    sensor through the job and reported on the ending instead. That works right up until
    there is no ending: a connection that goes quiet across a finish leaves the row open,
    and the plan the gateway was holding in memory dies with the process or is overwritten
    by the next print. The ledger then has a job it knows ran and no idea what it drew,
    which is the emptiest a review card can be.

    Reported as an observation rather than folded into the start for the reason the start's
    own docstring gives: *when* the figures describe this job is knowledge only the adapter
    has. Every delivery is a reading that spoke the per-tray dialect — a shape without tray
    keys is silence and never reaches here — so a later one supersedes an earlier one and
    none of them can mean *nothing was consumed*.

    `name` rides along because it suffers the same lag from the same cause: the file sensor
    is republished after the start, so a row opened at the first `prepare` carries the
    previous print's filename until something refreshes it. `None` leaves the stored name
    alone, and so does `UNKNOWN_JOB_NAME`: a row opened while every name sensor was silent
    is corrected by the first observation that carries a real name, and a named row is
    never renamed to the admission that nothing spoke.

    `printer_started_at` is the machine's own answer to *when did the print I am
    describing begin*, read off the same sensor the starts carry. It exists so the
    receiver can ask of an observation the question `_same_print` asks of a start: an
    observation is delivered to whichever row is open, and a stale row — an orphan whose
    ending never arrived — must be able to refuse figures that belong to the print
    running now (docs/12-field-notes.md, 2026-08-31: an orphan wore the next print's
    name and plan, and its review card offered grams the new print's own row then
    deducted again). `None` when the sensor did not say, which keeps adoption exactly as
    it was for a gateway that cannot answer.
    """

    printer: PrinterSerial
    plan: dict[TrayRef, Grams]
    name: str | None = None
    printer_started_at: datetime | None = None

    def __post_init__(self) -> None:
        # An observation of nothing is the silence this event exists to be distinguished
        # from. The adapter drops those before they get here; this is the backstop that
        # keeps an empty mapping from being written over a real plan.
        if not self.plan:
            msg = "a plan observation carries at least one tray; silence is not an event"
            raise InvalidValueError(msg)


PrintEvent = PrintStarted | PrintEnded | PrintPlanObserved
