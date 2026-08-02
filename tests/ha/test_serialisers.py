"""The wire shapes, and the one place rounding is allowed to happen.

The domain deals in exact `Grams`; the panel deals in numbers a browser renders. These
tests pin the translation: which keys exist, which precision each carries, and that no
surface ever rounds before it sums.
"""

from __future__ import annotations

import pytest

from custom_components.filament_ledger.application.adjust_spool import (
    AdjustSpoolCommand,
    DiscardFilamentCommand,
    DiscardMode,
)
from custom_components.filament_ledger.application.query import Queries
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import TagUid
from custom_components.filament_ledger.infrastructure.ha.serialisers import (
    grams,
    history_line,
    spool_detail,
    spool_summary,
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
