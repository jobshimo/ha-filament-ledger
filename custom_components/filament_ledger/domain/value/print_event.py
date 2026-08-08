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
    """

    name: str
    printer: PrinterSerial
    plan: dict[TrayRef, Grams] | None = None
    printer_started_at: datetime | None = None

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


PrintEvent = PrintStarted | PrintEnded
