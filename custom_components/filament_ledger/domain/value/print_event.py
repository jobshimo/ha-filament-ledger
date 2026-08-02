"""What the printer reports about a job's lifecycle, translated to domain terms.

Two moments cross the boundary: a job starting, and a job stopping. Everything here is
already in the domain's vocabulary — slot indices, grams, a terminal state — because the
gateway translates before anything crosses this line, exactly as it does for
`TrayReading`. Which bus event fired, which sensor carried each figure, and what the
per-tray attribute keys looked like are boundary concerns that stay in the adapter
(docs/05-ha-integration.md §5.8).

Every optional field means *unavailable*, never zero. A printer that reported nothing has
not reported nothing-was-consumed (docs/03-architecture.md §3.8).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..error import InvalidValueError
from .print_job_state import PrintJobState

if TYPE_CHECKING:
    from .grams import Grams
    from .identifiers import SlotIndex
    from .percentage import Percentage


@dataclass(frozen=True, slots=True)
class PrintStarted:
    """A job began.

    `plan` carries the slicer's per-slot totals when the weight sensor's attributes
    already hold them — the same figures `PrintJob.reported_usage` preserves, captured at
    the moment they are known to describe *this* job. `None` records that the breakdown
    never materialised, which is the open Q4 question, not a claim of zero.
    """

    name: str
    plan: dict[SlotIndex, Grams] | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "a print event cannot carry a blank job name"
            raise InvalidValueError(msg)
        _refuse_negative_usage(self.plan)


@dataclass(frozen=True, slots=True)
class PrintEnded:
    """A job stopped — finished, cancelled or failed.

    `outcome` is read off the upstream event type and nothing else (Q1, closed): the
    classification is made by code that reads the MQTT stream for a living, and
    `raw_gcode_state` plus `raw_print_error` travel verbatim so a wrong classification
    stays recoverable (docs/07-consumption-estimation.md §7.7).

    The progress figures are the moment's readings, captured because an interrupted job
    will be estimated from them; `reported_usage` is whatever per-slot figures the weight
    sensor held when the job ended.
    """

    outcome: PrintJobState
    name: str
    layer_reached: int | None = None
    total_layers: int | None = None
    progress: Percentage | None = None
    reported_usage: dict[SlotIndex, Grams] | None = None
    raw_gcode_state: str | None = None
    raw_print_error: int | None = None

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


def _refuse_negative_usage(usage: dict[SlotIndex, Grams] | None) -> None:
    for slot, used in (usage or {}).items():
        if used.is_negative:
            msg = f"usage for slot {slot} cannot be negative, got {used}"
            raise InvalidValueError(msg)


PrintEvent = PrintStarted | PrintEnded
