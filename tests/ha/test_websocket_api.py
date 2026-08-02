"""The panel's only channel to the backend, driven end to end.

Every command here is dispatched the way `ActiveConnection.async_handle` dispatches it:
looked up in the table `async_register_commands` filled, validated against the schema the
decorator attached, then run against the real use cases on real SQLite. The connection is
the only fake, and all it does is catch what the handler sends back.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

import pytest
import voluptuous as vol
from homeassistant.components.websocket_api import DOMAIN as WEBSOCKET_DOMAIN
from homeassistant.core import HomeAssistant

from custom_components.filament_ledger.application.review_queue import OpenPendingReviewCommand
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    PrintJobId,
    ReviewId,
    SlotIndex,
    SpoolId,
)
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.percentage import Percentage
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.domain.value.review import ReviewReason
from custom_components.filament_ledger.infrastructure.ha.websocket_api import (
    async_register_commands,
)

from ..application.conftest import EPOCH
from .conftest import FakeHass, Harness, as_hass

LIST = "filament_ledger/spools/list"
GET = "filament_ledger/spools/get"
STOCK = "filament_ledger/stock"
CREATE = "filament_ledger/spools/create"
UPDATE = "filament_ledger/spools/update"
RECONCILE = "filament_ledger/spools/reconcile"
DISCARD = "filament_ledger/spools/discard"
ADJUST = "filament_ledger/spools/adjust"
MOUNT = "filament_ledger/spools/mount"
UNMOUNT = "filament_ledger/spools/unmount"
REVIEWS_LIST = "filament_ledger/reviews/list"
REVIEWS_APPROVE = "filament_ledger/reviews/approve"
REVIEWS_DISMISS = "filament_ledger/reviews/dismiss"


@dataclass
class Reply:
    """One websocket response, as the panel would see it."""

    result: object = None
    error: tuple[str, str] | None = None


class FakeConnection:
    """Captures what a handler sends back, in place of `ActiveConnection`."""

    def __init__(self) -> None:
        self.replies: dict[int, Reply] = {}

    def send_result(self, msg_id: int, result: object = None) -> None:
        self.replies[msg_id] = Reply(result=result)

    def send_error(self, msg_id: int, code: str, message: str) -> None:
        self.replies[msg_id] = Reply(error=(code, message))

    def async_handle_exception(self, msg: dict[str, object], err: Exception) -> None:
        """`websocket_api` routes unexpected exceptions here. A test wants the traceback,
        not a logged apology, so the fake re-raises into the awaited task."""
        raise err


@dataclass
class WsClient:
    """Dispatches exactly as `ActiveConnection.async_handle` does.

    Look the handler up in `hass.data`, validate the message against the schema the
    `websocket_command` decorator attached (`False` means "no fields beyond id and type"),
    call the registered handler, and wait for the background task it schedules.
    """

    hass: FakeHass
    connection: FakeConnection = field(default_factory=FakeConnection)
    last_id: int = 0

    def _lookup(self, command: str) -> tuple[object, object]:
        handlers = cast("dict[str, tuple[object, object]]", self.hass.data[WEBSOCKET_DOMAIN])
        return handlers[command]

    def parse(self, command: str, **payload: object) -> dict[str, object]:
        self.last_id += 1
        _handler, schema = self._lookup(command)
        msg: dict[str, object] = {"id": self.last_id, "type": command, **payload}
        if schema is False:
            if len(msg) > 2:
                raise vol.Invalid("extra keys not allowed")
            return msg
        return cast("dict[str, object]", cast(vol.Schema, schema)(msg))

    async def send(self, command: str, **payload: object) -> Reply:
        msg = self.parse(command, **payload)
        handler, _schema = self._lookup(command)
        dispatch = cast("Callable[[HomeAssistant, object, dict[str, object]], None]", handler)
        dispatch(as_hass(self.hass), self.connection, msg)
        await self.hass.drain()
        return self.connection.replies[cast(int, msg["id"])]

    async def result_dict(self, command: str, **payload: object) -> dict[str, object]:
        reply = await self.send(command, **payload)
        assert reply.error is None, f"unexpected websocket error: {reply.error}"
        return cast("dict[str, object]", reply.result)

    async def result_list(self, command: str, **payload: object) -> list[dict[str, object]]:
        reply = await self.send(command, **payload)
        assert reply.error is None, f"unexpected websocket error: {reply.error}"
        return cast("list[dict[str, object]]", reply.result)

    async def error(self, command: str, **payload: object) -> tuple[str, str]:
        reply = await self.send(command, **payload)
        assert reply.error is not None, f"expected an error, got {reply.result!r}"
        return reply.error


@pytest.fixture
def ws(harness: Harness) -> WsClient:
    async_register_commands(as_hass(harness.hass))
    return WsClient(hass=harness.hass)


async def a_created_spool(ws: WsClient, **overrides: object) -> str:
    """Register a spool through the websocket itself — the panel's own path."""
    payload: dict[str, object] = {
        "material": "PLA",
        "colour": "000000",
        "opening_weight_g": 1000,
        "core_weight_g": 250,
        "vendor": "Bambu Lab",
    } | overrides
    result = await ws.result_dict(CREATE, **payload)
    return cast(str, result["spool_id"])


async def an_open_review(harness: Harness, job_id: str = "job-1") -> ReviewId:
    """A pending review for a cancelled print, opened through the real use case.

    The websocket deliberately has no command that opens a review — opening is the
    gateway's job — so the queue is seeded the way the gateway seeds it.
    """
    job = PrintJob(
        id=PrintJobId(job_id),
        name="bracket_v3.gcode.3mf",
        state=PrintJobState.CANCELLED,
        started_at=EPOCH,
        layer_reached=71,
        total_layers=209,
        progress=Percentage.of(34),
        reported_usage={SlotIndex(1): Grams.of(209)},
        raw_gcode_state="pause",
        raw_print_error=50348044,
    )
    return await harness.ledger.use_cases.open_pending_review.execute(
        OpenPendingReviewCommand(job=job, reason=ReviewReason.CANCELLED)
    )


class TestSchemasRejectMalformedMessages:
    """The adapter validates before the domain is reached, so a typo in the panel becomes
    a `vol.Invalid` the frontend can render — never a stack trace from a value object."""

    @pytest.mark.parametrize(
        ("command", "payload"),
        [
            pytest.param(GET, {}, id="get-without-a-spool-id"),
            pytest.param(GET, {"spool_id": 42}, id="get-with-a-numeric-id"),
            pytest.param(
                CREATE,
                {"colour": "000000", "opening_weight_g": 1000},
                id="create-without-a-material",
            ),
            pytest.param(
                CREATE,
                {"material": "WOOD", "colour": "000000", "opening_weight_g": 1000},
                id="create-with-an-unknown-material",
            ),
            pytest.param(
                CREATE,
                {"material": "PLA", "colour": "000000", "opening_weight_g": "a kilo"},
                id="create-with-a-textual-weight",
            ),
            pytest.param(CREATE, {"material": "PLA", "colour": "000000"}, id="create-unweighed"),
            pytest.param(UPDATE, {"label": "adrift"}, id="update-without-a-spool-id"),
            pytest.param(
                UPDATE, {"spool_id": "s", "material": "WOOD"}, id="update-with-an-unknown-material"
            ),
            pytest.param(RECONCILE, {"spool_id": "s"}, id="reconcile-without-a-reading"),
            pytest.param(
                RECONCILE,
                {"spool_id": "s", "measured_g": "heavy"},
                id="reconcile-with-a-textual-reading",
            ),
            pytest.param(
                DISCARD, {"spool_id": "s", "mode": "whole_spool"}, id="discard-without-a-reason"
            ),
            pytest.param(
                DISCARD,
                {"spool_id": "s", "mode": "halfway", "reason": "r"},
                id="discard-with-an-unknown-mode",
            ),
            pytest.param(ADJUST, {"spool_id": "s", "reason": "r"}, id="adjust-without-an-amount"),
            pytest.param(
                ADJUST,
                {"spool_id": "s", "amount_g": "much", "reason": "r"},
                id="adjust-with-a-textual-amount",
            ),
            pytest.param(MOUNT, {"spool_id": "s"}, id="mount-without-a-slot"),
            pytest.param(MOUNT, {"spool_id": "s", "slot": 0}, id="mount-below-the-first-slot"),
            pytest.param(MOUNT, {"spool_id": "s", "slot": 5}, id="mount-past-the-last-slot"),
            pytest.param(UNMOUNT, {}, id="unmount-without-a-spool-id"),
            pytest.param(LIST, {"surprise": 1}, id="list-accepts-no-fields-at-all"),
            pytest.param(STOCK, {"surprise": 1}, id="stock-accepts-no-fields-at-all"),
            pytest.param(REVIEWS_LIST, {"surprise": 1}, id="reviews-list-accepts-no-fields-at-all"),
            pytest.param(REVIEWS_APPROVE, {}, id="approve-without-a-review-id"),
            pytest.param(
                REVIEWS_APPROVE,
                {"review_id": "r", "amounts": {"9": 10}},
                id="approve-with-a-slot-past-the-last",
            ),
            pytest.param(
                REVIEWS_APPROVE,
                {"review_id": "r", "amounts": {"1": -5}},
                id="approve-with-a-negative-amount",
            ),
            pytest.param(
                REVIEWS_APPROVE,
                {"review_id": "r", "amounts": {"1": "much"}},
                id="approve-with-a-textual-amount",
            ),
            pytest.param(
                REVIEWS_APPROVE,
                {"review_id": "r", "assign": {"0": "spool"}},
                id="approve-assigning-below-the-first-slot",
            ),
            pytest.param(REVIEWS_DISMISS, {}, id="dismiss-without-a-review-id"),
        ],
    )
    def test_the_message_never_reaches_a_handler(
        self, ws: WsClient, command: str, payload: dict[str, object]
    ) -> None:
        with pytest.raises(vol.Invalid):
            ws.parse(command, **payload)


class TestWithoutARuntime:
    async def test_a_command_before_setup_is_an_error_reply_not_a_crash(
        self, ws: WsClient, harness: Harness
    ) -> None:
        harness.hass.config_entries.loaded.clear()
        code, message = await ws.error(LIST)
        assert code == "ApplicationError"
        assert "not set up" in message


class TestList:
    async def test_the_panel_sees_the_documented_summary_shape(
        self, ws: WsClient, harness: Harness
    ) -> None:
        spool_id = await a_created_spool(ws, label="PLA Basic Black")

        (payload,) = await ws.result_list(LIST)

        assert payload["id"] == spool_id
        assert payload["name"] == "PLA Basic Black"
        assert payload["vendor"] == "Bambu Lab"
        assert payload["material"] == "PLA"
        assert payload["colour"] == "#000000"
        assert payload["colour_hex8"] == "000000FF"
        assert payload["foreground"] == "#FFFFFF"
        assert payload["balance_g"] == 1000
        assert payload["percentage"] == 100
        assert payload["state"] == "SEALED"
        assert payload["confidence"] == "HIGH"
        assert payload["needs_weighing"] is False
        assert payload["location"] == {"kind": "STORAGE", "slot": None, "label": "Storage"}
        assert payload["movement_count"] == 1
        assert payload["has_anomaly"] is False


class TestDetail:
    async def test_history_reads_newest_first_with_running_balances(self, ws: WsClient) -> None:
        spool_id = await a_created_spool(ws)
        await ws.result_dict(ADJUST, spool_id=spool_id, amount_g=-100, reason="lamp shade")

        detail = await ws.result_dict(GET, spool_id=spool_id)

        history = cast("list[dict[str, object]]", detail["history"])
        assert [line["type"] for line in history] == ["MANUAL_ADJUSTMENT", "OPENING_BALANCE"]
        assert [line["balance_after_g"] for line in history] == [900, 1000]
        assert history[0]["amount_g"] == -100.0
        assert history[0]["label"] == "Adjustment"
        assert history[0]["source_label"] == "confirmed by you"

    async def test_an_unknown_spool_is_reported_not_invented(self, ws: WsClient) -> None:
        code, message = await ws.error(GET, spool_id="nope")
        assert code == "SpoolNotFoundError"
        assert "nope" in message


class TestStock:
    async def test_totals_count_only_stock_and_carry_the_configured_defaults(
        self, ws: WsClient
    ) -> None:
        await a_created_spool(ws, label="kept")
        binned = await a_created_spool(ws, label="binned")
        await ws.result_dict(DISCARD, spool_id=binned, mode="whole_spool", reason="water damage")

        assert await ws.result_dict(STOCK) == {
            "total_g": 1000,
            "spool_count": 1,
            "needs_weighing": 0,
            "per_material": {"PLA": 1000},
            "defaults": {"opening_weight_g": 1000, "core_weight_g": 250},
        }


class TestCreate:
    async def test_a_spool_born_through_the_panel_gets_a_ledger_entry_and_a_refresh(
        self, ws: WsClient, harness: Harness
    ) -> None:
        spool_id = await a_created_spool(ws)

        detail = await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))
        assert detail.summary.balance == Grams.of(1000)
        assert [line.movement.type.value for line in detail.lines] == ["OPENING_BALANCE"]
        # And the entities heard about it without waiting for the next poll.
        assert harness.coordinator.refresh_count == 1
        assert harness.coordinator.data is not None
        assert [s.spool.id for s in harness.coordinator.data.spools] == [spool_id]

    async def test_the_configured_core_weight_fills_in_when_the_panel_omits_it(
        self, ws: WsClient
    ) -> None:
        result = await ws.result_dict(CREATE, material="PLA", colour="FF0000", opening_weight_g=500)
        detail = await ws.result_dict(GET, spool_id=cast(str, result["spool_id"]))
        assert detail["core_weight_g"] == 250

    async def test_an_other_material_carries_its_own_name(self, ws: WsClient) -> None:
        await a_created_spool(ws, material="OTHER", material_other="Wood-fill")
        (payload,) = await ws.result_list(LIST)
        assert payload["material"] == "Wood-fill"
        assert payload["material_kind"] == "OTHER"

    async def test_a_malformed_colour_is_refused_with_a_message(self, ws: WsClient) -> None:
        code, message = await ws.error(
            CREATE, material="PLA", colour="black", opening_weight_g=1000
        )
        assert code == "InvalidValueError"
        assert "Colour" in message

    async def test_a_duplicate_tag_must_be_deliberate_even_from_the_panel(
        self, ws: WsClient
    ) -> None:
        await a_created_spool(ws, tag_uid="A1B2C3D4")

        code, _message = await ws.error(
            CREATE, material="PLA", colour="000000", opening_weight_g=1000, tag_uid="A1B2C3D4"
        )
        assert code == "DuplicateTagNotConfirmedError"

        # Legal once it is on purpose: a Bambu tag identifies a batch, not a unit.
        second = await a_created_spool(ws, tag_uid="A1B2C3D4", confirm_duplicate_tag=True)
        assert second
        assert len(await ws.result_list(LIST)) == 2


class TestUpdate:
    """`spools/update` edits what a label printer could fix — never what a scale measures."""

    async def test_it_edits_exactly_the_documented_metadata(
        self, ws: WsClient, harness: Harness
    ) -> None:
        spool_id = await a_created_spool(ws)

        result = await ws.result_dict(
            UPDATE,
            spool_id=spool_id,
            label="Rebadged",
            vendor="Polymaker",
            colour="FF0000",
            material="PETG",
            core_weight_g=180,
        )
        assert result == {"ok": True}

        spool = (await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))).summary.spool
        assert spool.label == "Rebadged"
        assert spool.vendor == "Polymaker"
        assert spool.colour == Colour.parse("FF0000")
        assert spool.material == Material.of(MaterialKind.PETG)
        assert spool.core_weight == Grams.of(180)

    async def test_it_cannot_alter_a_balance(self, ws: WsClient, harness: Harness) -> None:
        """The schema has no way to even name a balance, and the edit leaves the ledger
        untouched: balance and history are identical before and after."""
        spool_id = await a_created_spool(ws)
        await ws.result_dict(ADJUST, spool_id=spool_id, amount_g=-137.5, reason="vase")

        before = await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))
        await ws.result_dict(UPDATE, spool_id=spool_id, label="same spool", core_weight_g=500)
        after = await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))

        assert after.summary.balance == before.summary.balance
        assert [(line.movement.id, line.balance_after) for line in after.lines] == [
            (line.movement.id, line.balance_after) for line in before.lines
        ]

        for forbidden in ("balance_g", "opening_weight_g", "amount_g"):
            with pytest.raises(vol.Invalid):
                ws.parse(UPDATE, spool_id=spool_id, **{forbidden: 9000})

    async def test_null_means_leave_unchanged_not_clear(
        self, ws: WsClient, harness: Harness
    ) -> None:
        spool_id = await a_created_spool(ws, label="keep me")

        await ws.result_dict(UPDATE, spool_id=spool_id, label=None, vendor=None)

        spool = (await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))).summary.spool
        assert spool.label == "keep me"
        assert spool.vendor == "Bambu Lab"

    async def test_a_discarded_spool_cannot_be_rebadged(self, ws: WsClient) -> None:
        spool_id = await a_created_spool(ws)
        await ws.result_dict(DISCARD, spool_id=spool_id, mode="whole_spool", reason="gone")

        code, _message = await ws.error(UPDATE, spool_id=spool_id, label="zombie")
        assert code == "SpoolDiscardedError"


class TestReconcile:
    async def test_the_scale_reading_becomes_a_movement_with_the_delta_reported(
        self, ws: WsClient, harness: Harness
    ) -> None:
        spool_id = await a_created_spool(ws)

        result = await ws.result_dict(RECONCILE, spool_id=spool_id, measured_g=1224)

        # 1224 g on the scale minus the 250 g reel is 974 g of filament against a ledger
        # that said 1000 g; `includes_core` defaults to what a kitchen scale does.
        assert result == {"delta_g": -26.0, "new_balance_g": 974}
        detail = await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))
        assert detail.summary.balance == Grams.of(974)

    async def test_a_reading_can_exclude_the_reel(self, ws: WsClient) -> None:
        spool_id = await a_created_spool(ws)
        result = await ws.result_dict(
            RECONCILE, spool_id=spool_id, measured_g=974, includes_core=False
        )
        assert result == {"delta_g": -26.0, "new_balance_g": 974}

    async def test_agreement_is_a_refusal_the_panel_can_show(self, ws: WsClient) -> None:
        spool_id = await a_created_spool(ws)
        code, _message = await ws.error(RECONCILE, spool_id=spool_id, measured_g=1250)
        assert code == "NothingToRecordError"


class TestDiscard:
    async def test_a_whole_spool_write_off_leaves_active_inventory(self, ws: WsClient) -> None:
        spool_id = await a_created_spool(ws)

        assert await ws.result_dict(
            DISCARD, spool_id=spool_id, mode="whole_spool", reason="water damage"
        ) == {"ok": True}

        assert await ws.result_list(LIST) == []
        detail = await ws.result_dict(GET, spool_id=spool_id)
        assert detail["state"] == "DISCARDED"
        assert detail["balance_g"] == 0

    async def test_a_partial_discard_takes_grams_not_the_spool(self, ws: WsClient) -> None:
        spool_id = await a_created_spool(ws)

        await ws.result_dict(
            DISCARD, spool_id=spool_id, mode="partial", amount_g=8, reason="tangled section"
        )

        (payload,) = await ws.result_list(LIST)
        assert payload["balance_g"] == 992
        assert payload["state"] == "ACTIVE"


class TestAdjust:
    async def test_an_adjustment_lands_in_the_ledger(self, ws: WsClient, harness: Harness) -> None:
        spool_id = await a_created_spool(ws)

        assert await ws.result_dict(
            ADJUST, spool_id=spool_id, amount_g=-162, reason="lamp_shade"
        ) == {"ok": True}

        detail = await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))
        assert detail.summary.balance == Grams.of(838)
        assert detail.lines[0].movement.type.value == "MANUAL_ADJUSTMENT"

    async def test_adjusting_a_discarded_spool_is_an_error_reply_not_a_crash(
        self, ws: WsClient
    ) -> None:
        """The `@guarded` contract: a domain refusal crosses the websocket as an error the
        panel can show, carrying the domain's own words."""
        spool_id = await a_created_spool(ws)
        await ws.result_dict(DISCARD, spool_id=spool_id, mode="whole_spool", reason="gone")

        code, message = await ws.error(ADJUST, spool_id=spool_id, amount_g=-1, reason="no")

        assert code == "SpoolDiscardedError"
        assert "discarded" in message


class TestMountAndUnmount:
    async def test_a_mounted_spool_reports_its_slot_to_the_panel(self, ws: WsClient) -> None:
        spool_id = await a_created_spool(ws)

        await ws.result_dict(MOUNT, spool_id=spool_id, slot=2)
        (payload,) = await ws.result_list(LIST)
        assert payload["location"] == {"kind": "AMS_SLOT", "slot": 2, "label": "AMS slot 2"}

        await ws.result_dict(UNMOUNT, spool_id=spool_id)
        (payload,) = await ws.result_list(LIST)
        assert payload["location"] == {"kind": "STORAGE", "slot": None, "label": "Storage"}

    async def test_every_mutation_refreshes_the_entities(
        self, ws: WsClient, harness: Harness
    ) -> None:
        spool_id = await a_created_spool(ws)
        await ws.result_dict(MOUNT, spool_id=spool_id, slot=1)
        await ws.result_dict(UNMOUNT, spool_id=spool_id)
        assert harness.coordinator.refresh_count == 3


class TestReviewsList:
    async def test_an_empty_queue_is_an_empty_list(self, ws: WsClient) -> None:
        assert await ws.result_list(REVIEWS_LIST) == []

    async def test_the_panel_sees_the_documented_review_card_shape(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Everything the card in docs/06 §6.3 renders, in one payload: the job's name
        and raw error, the named estimator, the frozen per-line facts."""
        spool_id = await a_created_spool(ws)
        await ws.result_dict(MOUNT, spool_id=spool_id, slot=1)
        review_id = await an_open_review(harness)

        (payload,) = await ws.result_list(REVIEWS_LIST)

        assert payload == {
            "id": review_id,
            "job_id": "job-1",
            "job_name": "bracket_v3.gcode.3mf",
            "job_state": "CANCELLED",
            "reason": "CANCELLED",
            "estimator": "LINEAR_PROGRESS",
            "opened_at": EPOCH.isoformat(),
            "layer_reached": 71,
            "total_layers": 209,
            "progress_pct": 34,
            "raw_gcode_state": "pause",
            "raw_print_error": 50348044,
            # 71 of 209 layers of a 209 g plan: 71 g, frozen to the mounted spool.
            "estimated_total_g": 71.0,
            "lines": [{"slot": 1, "estimated_g": 71.0, "spool_id": spool_id}],
        }

    async def test_an_unattributed_slot_travels_with_a_null_spool(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The row most worth showing: nothing was mounted when the review froze, and the
        panel renders the spool picker off exactly this null."""
        await an_open_review(harness)

        (payload,) = await ws.result_list(REVIEWS_LIST)

        assert payload["lines"] == [{"slot": 1, "estimated_g": 71.0, "spool_id": None}]


class TestReviewsApprove:
    async def test_approval_deducts_and_refreshes(self, ws: WsClient, harness: Harness) -> None:
        spool_id = await a_created_spool(ws)
        await ws.result_dict(MOUNT, spool_id=spool_id, slot=1)
        review_id = await an_open_review(harness)
        refreshes = harness.coordinator.refresh_count

        result = await ws.result_dict(REVIEWS_APPROVE, review_id=review_id)

        assert result == {"ok": True}
        (payload,) = await ws.result_list(LIST)
        assert payload["balance_g"] == 929
        assert await ws.result_list(REVIEWS_LIST) == []
        assert harness.coordinator.refresh_count == refreshes + 1

    async def test_the_users_numbers_override_and_assignments_resolve(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """JSON object keys arrive as strings; the schema reads them as slot indices."""
        spool_id = await a_created_spool(ws)
        await an_open_review(harness)
        (pending,) = await ws.result_list(REVIEWS_LIST)

        await ws.result_dict(
            REVIEWS_APPROVE,
            review_id=pending["id"],
            amounts={"1": 12.5},
            assign={"1": spool_id},
            note="weighed the waste",
        )

        (payload,) = await ws.result_list(LIST)
        assert payload["balance_g"] == 988  # 1000 − 12.5, rounded once
        history = cast(
            "list[dict[str, object]]",
            (await ws.result_dict(GET, spool_id=spool_id))["history"],
        )
        assert history[0]["type"] == "ESTIMATED_CONSUMPTION"
        assert history[0]["amount_g"] == -12.5

    async def test_an_unresolved_slot_is_an_error_reply_not_a_crash(
        self, ws: WsClient, harness: Harness
    ) -> None:
        review_id = await an_open_review(harness)

        code, message = await ws.error(REVIEWS_APPROVE, review_id=review_id)

        assert code == "UnresolvedSlotError"
        assert "no spool" in message
        assert (await ws.result_list(REVIEWS_LIST)) != []  # still pending, nothing written

    async def test_a_double_approval_is_refused_with_the_domains_words(
        self, ws: WsClient, harness: Harness
    ) -> None:
        spool_id = await a_created_spool(ws)
        await ws.result_dict(MOUNT, spool_id=spool_id, slot=1)
        review_id = await an_open_review(harness)
        await ws.result_dict(REVIEWS_APPROVE, review_id=review_id)

        code, _message = await ws.error(REVIEWS_APPROVE, review_id=review_id)

        assert code == "ReviewAlreadyResolvedError"
        (payload,) = await ws.result_list(LIST)
        assert payload["balance_g"] == 929  # deducted exactly once

    async def test_an_unknown_review_is_reported_not_invented(self, ws: WsClient) -> None:
        code, message = await ws.error(REVIEWS_APPROVE, review_id="nope")
        assert code == "ReviewNotFoundError"
        assert "nope" in message


class TestReviewsDismiss:
    async def test_dismissal_resolves_without_touching_a_balance(
        self, ws: WsClient, harness: Harness
    ) -> None:
        spool_id = await a_created_spool(ws)
        await ws.result_dict(MOUNT, spool_id=spool_id, slot=1)
        review_id = await an_open_review(harness)

        result = await ws.result_dict(
            REVIEWS_DISMISS, review_id=review_id, note="failed on the first layer"
        )

        assert result == {"ok": True}
        assert await ws.result_list(REVIEWS_LIST) == []
        (payload,) = await ws.result_list(LIST)
        assert payload["balance_g"] == 1000

    async def test_a_dismissed_review_stays_dismissed(self, ws: WsClient, harness: Harness) -> None:
        review_id = await an_open_review(harness)
        await ws.result_dict(REVIEWS_DISMISS, review_id=review_id)

        code, _message = await ws.error(REVIEWS_DISMISS, review_id=review_id)

        assert code == "ReviewAlreadyResolvedError"

    async def test_an_unknown_review_cannot_be_dismissed(self, ws: WsClient) -> None:
        code, _message = await ws.error(REVIEWS_DISMISS, review_id="nope")
        assert code == "ReviewNotFoundError"
