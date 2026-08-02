"""Where a print job is in its lifecycle."""

from __future__ import annotations

from enum import StrEnum


class PrintJobState(StrEnum):
    """The four states a job can hold, mirrored verbatim in the `print_job` table.

    Deliberately *not* the vocabulary reviews are classified in. `CANCELLED` here says what
    the printer reported about the job; `ReviewReason.CANCELLED` says why a review exists,
    and the two are allowed to disagree — the classification comes from the `ha-bambulab`
    event type, upstream code can be wrong, and `raw_gcode_state` is stored verbatim so a
    wrong conclusion stays recoverable (docs/07-consumption-estimation.md §7.7).
    """

    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        """Whether the job has stopped producing filament movements.

        Every consumption decision — automatic recording, opening a review — happens at a
        terminal state. A `RUNNING` job has no final figure to record or estimate yet.
        """
        return self is not PrintJobState.RUNNING
