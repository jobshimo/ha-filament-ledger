"""LinearProgressEstimator, against the contract every strategy must obey.

Return per-slot grams or raise `EstimationUnavailableError` — never `None`, never an
invented zero. The strategy is known-imprecise and says so; these tests pin the arithmetic
and the refusals, not an accuracy nobody has measured yet (docs/07 §7.5).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.filament_ledger.domain.error import EstimationUnavailableError
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import PrintJobId, SlotIndex
from custom_components.filament_ledger.domain.value.percentage import Percentage
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.domain.value.review import EstimatorKind
from custom_components.filament_ledger.infrastructure.estimation.linear_progress_estimator import (
    LinearProgressEstimator,
)

EPOCH = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
SLOT_1 = SlotIndex(1)
SLOT_2 = SlotIndex(2)

estimator = LinearProgressEstimator()


def a_job(
    *,
    layer_reached: int | None = None,
    total_layers: int | None = None,
    progress: Percentage | None = None,
    reported_usage: dict[SlotIndex, Grams] | None = None,
) -> PrintJob:
    return PrintJob(
        id=PrintJobId("job-under-estimation"),
        name="bracket_v3.gcode.3mf",
        state=PrintJobState.CANCELLED,
        started_at=EPOCH,
        layer_reached=layer_reached,
        total_layers=total_layers,
        progress=progress,
        reported_usage=reported_usage,
    )


class TestProgressSignals:
    async def test_layers_are_the_preferred_signal(self) -> None:
        """Layer count is the closest available proxy for material; `mc_percent` tracks
        time. When both are present the weaker one must not even be consulted."""
        job = a_job(
            layer_reached=30,
            total_layers=100,
            progress=Percentage.of(90),
            reported_usage={SLOT_1: Grams.of(100)},
        )
        assert await estimator.estimate(job) == {SLOT_1: Grams.of(30)}

    async def test_percent_is_the_fallback_when_layers_are_missing(self) -> None:
        job = a_job(progress=Percentage.of(25), reported_usage={SLOT_1: Grams.of(100)})
        assert await estimator.estimate(job) == {SLOT_1: Grams.of(25)}

    async def test_half_a_signal_is_no_signal(self) -> None:
        """`layer_reached` without `total_layers` is a numerator with no denominator."""
        job = a_job(layer_reached=30, reported_usage={SLOT_1: Grams.of(100)})
        with pytest.raises(EstimationUnavailableError):
            await estimator.estimate(job)

    async def test_a_layer_count_past_the_plan_is_clamped_to_the_whole_plan(self) -> None:
        """Priming and the slicer's counting disagree at the edges; scaling past the
        totals would claim more filament than the whole plan contains."""
        job = a_job(layer_reached=105, total_layers=100, reported_usage={SLOT_1: Grams.of(80)})
        assert await estimator.estimate(job) == {SLOT_1: Grams.of(80)}

    async def test_a_job_stopped_before_its_first_layer_computes_zero(self) -> None:
        """A computed zero is returned, not suppressed — the contract forbids inventing
        a zero to paper over a failure, not arithmetic whose honest answer is nothing."""
        job = a_job(layer_reached=0, total_layers=60, reported_usage={SLOT_1: Grams.of(80)})
        assert await estimator.estimate(job) == {SLOT_1: Grams.zero()}


class TestRefusals:
    async def test_no_signal_at_all_raises_rather_than_guesses(self) -> None:
        with pytest.raises(EstimationUnavailableError):
            await estimator.estimate(a_job(reported_usage={SLOT_1: Grams.of(100)}))

    @pytest.mark.parametrize("usage", [None, {}], ids=["missing", "empty"])
    async def test_no_totals_to_scale_raises_rather_than_guesses(
        self, usage: dict[SlotIndex, Grams] | None
    ) -> None:
        job = a_job(layer_reached=30, total_layers=100, reported_usage=usage)
        with pytest.raises(EstimationUnavailableError):
            await estimator.estimate(job)


class TestProportionality:
    async def test_every_slot_scales_by_the_same_ratio(self) -> None:
        """The proportion is what [ Distribute ] later relies on: the estimator supplies
        the shape even when the user supplies the magnitude (docs/07 §7.4)."""
        job = a_job(
            layer_reached=71,
            total_layers=209,
            reported_usage={SLOT_1: Grams.of(209), SLOT_2: Grams.of("41.8")},
        )
        assert await estimator.estimate(job) == {
            SLOT_1: Grams.of(71),
            SLOT_2: Grams.of("14.2"),
        }

    async def test_rounding_lands_on_the_nearest_milligram(self) -> None:
        # 1/3 of 100 g: 33333.33... mg must round, not truncate.
        job = a_job(layer_reached=1, total_layers=3, reported_usage={SLOT_1: Grams.of(100)})
        assert await estimator.estimate(job) == {SLOT_1: Grams(33333)}


class TestProvenance:
    def test_the_strategy_names_itself(self) -> None:
        """The kind lands on every review it estimates for; the UI labels the figure
        *approximate* because of it."""
        assert estimator.kind is EstimatorKind.LINEAR_PROGRESS
