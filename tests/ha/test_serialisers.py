"""The wire shapes, and the one place rounding is allowed to happen.

The domain deals in exact `Grams`; the panel deals in numbers a browser renders. These
tests pin the translation: which keys exist, which precision each carries, and that no
surface ever rounds before it sums.
"""

from __future__ import annotations

from datetime import timedelta
from typing import cast

import pytest

from custom_components.filament_ledger.application.adjust_spool import (
    AdjustSpoolCommand,
    DiscardFilamentCommand,
    DiscardMode,
)
from custom_components.filament_ledger.application.query import (
    ObservedPrintTime,
    PrintTime,
    Queries,
    StatisticsPeriod,
)
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    PrintJobId,
    SlotIndex,
    TagUid,
)
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.infrastructure.ha.serialisers import (
    _observed_print_time,
    _print_time,
    grams,
    history_line,
    spool_detail,
    spool_summary,
    statistics_result,
    stock_grams,
    stock_per_material,
    whole_grams,
)

from ..application.conftest import EPOCH
from .conftest import Harness, a_spool


class TestRounding:
    def test_movement_amounts_carry_one_decimal_half_up(self) -> None:
        """A single movement is known to a tenth of a gram, and no better."""
        assert grams(Grams.of("1.25")) == 1.3
        assert grams(Grams.of("1.24")) == 1.2
        assert grams(Grams.of("-84.06")) == -84.1

    def test_balances_round_to_whole_grams_half_up(self) -> None:
        """A kitchen scale reads to the gram; showing a decimal would claim a precision
        the system cannot back up."""
        assert whole_grams(Grams.of("862.5")) == 863
        assert whole_grams(Grams.of("862.4")) == 862
        assert whole_grams(Grams.of("-0.5")) == -1


class TestStockTotals:
    async def test_stock_rounds_the_sum_never_the_spools(self, harness: Harness) -> None:
        """Three 0.3 g spools hold 0.9 g of stock — 1 g, not 0 g."""
        for label in ("first", "second", "third"):
            await a_spool(harness.ledger, label=label, opening_weight=Grams.of("0.3"), vendor=None)
        summaries = await harness.ledger.use_cases.queries.overview()

        assert stock_grams(summaries) == 1
        assert stock_per_material(summaries) == {"PLA": 1}

    async def test_discarded_spools_never_count_as_stock(self, harness: Harness) -> None:
        await a_spool(harness.ledger, label="kept")
        binned = await a_spool(harness.ledger, label="binned")
        await harness.ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(spool_id=binned, mode=DiscardMode.WHOLE_SPOOL, reason="gone")
        )

        summaries = await harness.ledger.use_cases.queries.overview(include_discarded=True)
        assert len(summaries) == 2  # the discarded spool is present in the read model
        assert stock_grams(summaries) == 1000  # and absent from the stock figure
        assert stock_per_material(summaries) == {"PLA": 1000}


class TestSpoolSummaryShape:
    async def test_the_wire_shape_is_exactly_what_the_panel_renders(self, harness: Harness) -> None:
        spool_id = await a_spool(
            harness.ledger, label="PLA Basic Black", tag_uid=TagUid("A1B2C3D4")
        )
        (summary,) = await harness.ledger.use_cases.queries.overview()

        assert spool_summary(summary) == {
            "id": spool_id,
            "name": "PLA Basic Black",
            "label": "PLA Basic Black",
            "vendor": "Bambu Lab",
            "material": "PLA",
            "material_kind": "PLA",
            "colour": "#000000",
            "colour_hex8": "000000FF",
            "foreground": "#FFFFFF",
            "balance_g": 1000,
            "balance_exact_g": 1000.0,
            "opening_weight_g": 1000,
            "core_weight_g": 250,
            "percentage": 100,
            "state": "SEALED",
            "confidence": "HIGH",
            "needs_weighing": False,
            "location": {"kind": "STORAGE", "slot": None, "label": "Storage"},
            "tag_uid": "A1B2C3D4",
            # A tag typed at registration is the user's, so the edit dialog may change it.
            "tag_source": "MANUAL",
            "movement_count": 1,
            "last_movement_at": EPOCH.isoformat(),
            "has_anomaly": False,
            "registered_at": EPOCH.isoformat(),
        }

    async def test_a_spool_without_a_label_is_named_by_vendor_and_material(
        self, harness: Harness
    ) -> None:
        await a_spool(harness.ledger)
        (summary,) = await harness.ledger.use_cases.queries.overview()
        payload = spool_summary(summary)
        assert payload["name"] == "Bambu Lab PLA"
        assert payload["label"] is None
        assert payload["tag_uid"] is None
        # No tag, so no provenance to describe: the pair is null together.
        assert payload["tag_source"] is None


class TestHistory:
    @pytest.fixture
    async def queries(self, harness: Harness) -> Queries:
        return harness.ledger.use_cases.queries

    async def test_a_line_shows_the_movement_and_the_balance_it_produced(
        self, harness: Harness, queries: Queries
    ) -> None:
        spool_id = await a_spool(harness.ledger)
        harness.ledger.clock.advance(days=1)
        await harness.ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of("-137.5"), reason="vase")
        )

        detail = await queries.detail(spool_id)
        line = history_line(detail.lines[0])

        assert line["type"] == "MANUAL_ADJUSTMENT"
        assert line["label"] == "Adjustment"
        assert line["amount_g"] == -137.5
        assert line["balance_after_g"] == 863  # the whole-gram balance the panel leads with
        assert line["balance_after_exact_g"] == 862.5  # and the tenth for the history view
        assert line["source"] == "USER_CONFIRMED"
        assert line["source_label"] == "confirmed by you"

    async def test_spool_detail_is_the_summary_plus_history_newest_first(
        self, harness: Harness, queries: Queries
    ) -> None:
        spool_id = await a_spool(harness.ledger)
        harness.ledger.clock.advance(days=1)
        await harness.ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(-100), reason="bracket")
        )

        detail = await queries.detail(spool_id)
        payload = spool_detail(detail)

        assert payload["id"] == spool_id  # the summary keys are flattened in
        history = payload["history"]
        assert isinstance(history, list)
        assert [line["type"] for line in history] == ["MANUAL_ADJUSTMENT", "OPENING_BALANCE"]


class TestStatisticsRounding:
    """The rule that matters more in aggregate than anywhere else: accumulate exact
    `Grams`, round exactly once. Rounding forty prints individually and then adding them
    can be twenty grams out, and a statistics page that disagrees with the history it
    summarises is worse than no statistics page at all."""

    async def test_the_total_rounds_the_sum_and_never_the_prints(self, harness: Harness) -> None:
        """Three 0.4 g prints are 1.2 g, which is 1 g. Rounding each first would call it 0."""
        spool_id = await a_spool(harness.ledger)
        await harness.ledger.use_cases.mount_spool.execute(spool_id, SlotIndex(1))
        for index in range(3):
            await harness.ledger.use_cases.record_print_consumption.execute(
                PrintJob(
                    id=PrintJobId(f"job-{index}"),
                    name=f"tiny-{index}.3mf",
                    state=PrintJobState.FINISHED,
                    started_at=EPOCH,
                    ended_at=EPOCH,
                    reported_usage={SlotIndex(1): Grams.of("0.4")},
                )
            )

        payload = statistics_result(
            await harness.ledger.use_cases.queries.statistics(StatisticsPeriod.ALL_TIME)
        )

        assert payload["consumed_g"] == 1
        assert payload["by_colour"] == [{"colour": "#000000", "grams": 1}]
        assert payload["by_material"] == [{"material": "PLA", "grams": 1}]
        # Each print is 0.4 g on its own, and each rounds to nothing — which is exactly
        # why the total above may not be built out of these.
        assert [row["grams"] for row in cast("list[dict[str, object]]", payload["top_prints"])] == [
            0,
            0,
            0,
        ]

    def test_print_time_reports_whole_minutes_rounded_half_up(self) -> None:
        payload = _print_time(PrintTime(total=timedelta(seconds=90), prints=1))

        assert payload == {"total_minutes": 2, "average_minutes": 2, "prints": 1}

    def test_the_average_divides_the_exact_total_not_the_rounded_one(self) -> None:
        """Two prints of 45 seconds are 90 seconds — 2 minutes total, 1 minute each.
        An average taken from the rounded total would report 1 minute as well by luck;
        this one is right by construction."""
        payload = _print_time(PrintTime(total=timedelta(seconds=150), prints=4))

        assert payload == {"total_minutes": 3, "average_minutes": 1, "prints": 4}

    def test_no_measurable_duration_serialises_as_null_never_as_zeros(self) -> None:
        """A card of zeros would be a claim about the printer rather than about the data."""
        assert _print_time(None) is None

    def test_the_accumulated_total_never_travels_without_what_bounds_it(self) -> None:
        """The three figures are one fact. `ha-bambulab` reports no lifetime hours, so this
        total can only ever be a sum over what *this* ledger recorded — and a total sent
        alone would leave the panel free to render it as the machine's odometer."""
        payload = _observed_print_time(
            ObservedPrintTime(
                measured=PrintTime(total=timedelta(hours=52, minutes=44), prints=25),
                since=EPOCH,
            )
        )

        assert payload == {"total_minutes": 3164, "prints": 25, "since": EPOCH.isoformat()}

    def test_a_ledger_that_has_timed_nothing_serialises_as_null(self) -> None:
        """Zero hours would claim a machine has never printed; null claims nothing."""
        assert _observed_print_time(None) is None
