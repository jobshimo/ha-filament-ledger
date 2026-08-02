"""How much an interrupted print consumed — a port, because *how* is a strategy.

Two implementations behind this interface (docs/07-consumption-estimation.md §7.3), and
every one obeys the same contract: return per-slot grams, or raise
`EstimationUnavailableError`. None returns `None` to signal failure, and none invents a
zero — that is Liskov substitution in practice, and it is what keeps UC-05 unable to be
broken by which strategy happens to run.

`estimate` is `async` although today's fallback is pure arithmetic: the preferred strategy
fetches and parses the sliced file, which is I/O, and the boundary belongs to the port —
not to whichever implementation happens to exist first (docs/adr/0005-async-io-ports.md).
"""

from __future__ import annotations

from typing import Protocol

from ..model.print_job import PrintJob
from ..value.grams import Grams
from ..value.identifiers import SlotIndex
from ..value.review import EstimatorKind


class ConsumptionEstimator(Protocol):
    @property
    def kind(self) -> EstimatorKind:
        """Which strategy this is, recorded on every review it estimates for.

        Provenance is data the user needs: *"layer-accurate"* and *"approximate"* are
        different invitations to reach for the scale, and hiding which one applies is how
        a guess gets mistaken for a measurement.
        """
        ...

    async def estimate(self, job: PrintJob) -> dict[SlotIndex, Grams]:
        """Per-slot grams consumed up to where the job stopped.

        Raises `EstimationUnavailableError` when no figure can honestly be produced.
        """
        ...
