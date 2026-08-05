"""The fallback estimator: progress × the slicer's per-tray totals.

Infrastructure by placement (docs/03-architecture.md §3.5), pure by construction: this
module needs nothing from Home Assistant, no I/O, no clock — only a `PrintJob`. The
placement is about the *family*: its Phase 4 siblings fetch files over FTP, and the
strategy list should live in one directory rather than straddle a layer boundary.

Known-imprecise, and honestly so. Consumption is not linear in layer count — first layers
are denser, purge is spent at colour changes, `mc_percent` tracks time rather than material
(docs/07-consumption-estimation.md §7.1) — which is why every review carries
`EstimatorKind.LINEAR_PROGRESS` and the UI labels the figure *approximate*.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ...domain.error import EstimationUnavailableError
from ...domain.model.print_job import PrintJob
from ...domain.value.grams import Grams
from ...domain.value.identifiers import TrayRef
from ...domain.value.review import EstimatorKind

ONE = Decimal(1)
PERCENT_SCALE = Decimal(100)


@dataclass(frozen=True, slots=True)
class LinearProgressEstimator:
    @property
    def kind(self) -> EstimatorKind:
        return EstimatorKind.LINEAR_PROGRESS

    async def estimate(self, job: PrintJob) -> dict[TrayRef, Grams]:
        """Per-tray grams: the best available progress signal times the slicer's totals.

        Signals in order of preference (docs/07-consumption-estimation.md §7.3): layers,
        the closest available proxy for material; then `mc_percent`, which tracks time and
        is weakest. No signal and no totals both raise — a review still opens, carrying an
        explicit no-data flag instead of a fabricated figure.

        A computed zero — a job stopped before its first layer — is returned, not
        suppressed. The contract forbids *inventing* a zero to paper over a failure; it
        does not forbid arithmetic whose honest answer is nothing yet.
        """
        totals = job.reported_usage
        if not totals:
            msg = f"job {job.id} carries no per-tray totals to scale"
            raise EstimationUnavailableError(msg)
        progress = _progress_of(job)
        return {tray: total.scaled_by(progress) for tray, total in totals.items()}


def _progress_of(job: PrintJob) -> Decimal:
    """The fraction of the plan carried out, in 0..1.

    Clamped at 1: a printer can report `layer_reached` past `total_layers` — priming and
    the slicer's own counting disagree at the edges — and scaling past the totals would
    claim more filament than the whole plan contains.
    """
    if job.layer_reached is not None and job.total_layers is not None:
        return min(Decimal(job.layer_reached) / Decimal(job.total_layers), ONE)
    if job.progress is not None:
        return job.progress.value / PERCENT_SCALE
    msg = f"job {job.id} reports neither layers nor percent progress"
    raise EstimationUnavailableError(msg)
