"""The panel's only channel to the backend, driven end to end.

Every command here is dispatched the way `ActiveConnection.async_handle` dispatches it:
looked up in the table `async_register_commands` filled, validated against the schema the
decorator attached, then run against the real use cases on real SQLite. The connection is
the only fake, and all it does is catch what the handler sends back.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import cast

import pytest
import voluptuous as vol
from homeassistant.components.websocket_api import DOMAIN as WEBSOCKET_DOMAIN
from homeassistant.core import HomeAssistant

from custom_components.filament_ledger.application.detect_spool import DetectSpool
from custom_components.filament_ledger.application.review_queue import OpenPendingReviewCommand
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    PrintJobId,
    ReviewId,
    SpoolId,
    TagSource,
    TagUid,
)
from custom_components.filament_ledger.domain.value.location import AmsSlot, Storage
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.movement_type import MovementType
from custom_components.filament_ledger.domain.value.percentage import Percentage
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.domain.value.review import EstimatorKind, ReviewReason
from custom_components.filament_ledger.infrastructure.ha.bambu_gateway import BambuLabGateway
from custom_components.filament_ledger.infrastructure.ha.event_bridge import LEDGER_EVENTS
from custom_components.filament_ledger.infrastructure.ha.printer_state import ReadPrinterState
from custom_components.filament_ledger.infrastructure.ha.tray_sync import TraySync
from custom_components.filament_ledger.infrastructure.ha.websocket_api import (
    async_register_commands,
)
from custom_components.filament_ledger.infrastructure.persistence.spool_repository import (
    SqliteSpoolRepository,
)

from ..application.conftest import A_PRINTER, ANOTHER_PRINTER, EPOCH, a_tray
from .conftest import FakeHass, Harness, a_spool, as_hass

# The captured reference instance: the same registry rows and tray attributes the gateway
# suite drives, because the sync command is that gateway feeding that ledger.
from .test_bambu_gateway import (
    REGISTRY_ROWS,
    TRAY_1_TAG,
    TRAY_2,
    TRAY_ATTRIBUTES,
    plant_registry,
    second_printer_rows,
    tray_state,
)


# One tray as the panel names it on the wire: the three parts, exactly as `a_tray(1)`
# spells them in the ledger these tests build.
def tray_wire(slot: int) -> dict[str, object]:
    """One tray as the panel names it: the three parts `a_tray` spells in the ledger."""
    return {"printer": A_PRINTER.value, "ams": 1, "slot": slot}


TRAY_1_WIRE = tray_wire(1)


def _tray_of(line: dict[str, object]) -> dict[str, object]:
    """The tray a review line named, ready to be sent back with an answer attached.

    Read off the payload rather than restated, because that is what the panel does: the
    card renders the tray the review froze, and approving quotes it back.
    """
    return {key: line[key] for key in ("printer", "ams", "slot")}


LIST = "filament_ledger/spools/list"
FINISHED = "filament_ledger/spools/finished"
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
MOVEMENTS = "filament_ledger/movements"
TRAYS_SYNC = "filament_ledger/trays/sync"
STATISTICS = "filament_ledger/statistics"
SUBSCRIBE = "filament_ledger/subscribe"


@dataclass
class Reply:
    """One websocket response, as the panel would see it."""

    result: object = None
    error: tuple[str, str] | None = None


class FakeConnection:
    """Captures what a handler sends back, in place of `ActiveConnection`."""

    def __init__(self) -> None:
        self.replies: dict[int, Reply] = {}
        # What a subscription pushes, and how it is closed. The real `ActiveConnection`
        # carries both, and `handle_subscribe` uses both.
        self.messages: list[dict[str, object]] = []
        self.subscriptions: dict[int, object] = {}

    def send_message(self, message: dict[str, object]) -> None:
        self.messages.append(message)

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


async def an_open_review(
    harness: Harness, job_id: str = "job-1", raw_print_error: int = 50348044
) -> ReviewId:
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
        reported_usage={a_tray(1): Grams.of(209)},
        raw_gcode_state="pause",
        raw_print_error=raw_print_error,
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
                {"review_id": "r", "amounts": [{"slot": 9, "amount_g": 10}]},
                id="approve-with-a-slot-past-the-last",
            ),
            pytest.param(
                REVIEWS_APPROVE,
                {"review_id": "r", "amounts": [{"slot": 1, "amount_g": -5}]},
                id="approve-with-a-negative-amount",
            ),
            pytest.param(
                REVIEWS_APPROVE,
                {"review_id": "r", "amounts": [{"slot": 1, "amount_g": "much"}]},
                id="approve-with-a-textual-amount",
            ),
            pytest.param(
                REVIEWS_APPROVE,
                {"review_id": "r", "assign": [{"slot": 0, "spool_id": "spool"}]},
                id="approve-assigning-below-the-first-slot",
            ),
            pytest.param(
                REVIEWS_APPROVE,
                {"review_id": "r", "amounts": [{"slot": 1, "ams": 0, "amount_g": 10}]},
                id="approve-with-an-ams-below-the-first",
            ),
            pytest.param(
                REVIEWS_APPROVE,
                {"review_id": "r", "amounts": [{"amount_g": 10}]},
                id="approve-with-an-entry-naming-no-tray",
            ),
            pytest.param(MOUNT, {"spool_id": "s", "slot": 1, "ams": 0}, id="mount-into-ams-zero"),
            pytest.param(REVIEWS_DISMISS, {}, id="dismiss-without-a-review-id"),
            pytest.param(MOVEMENTS, {"limit": "many"}, id="movements-with-a-textual-limit"),
            pytest.param(MOVEMENTS, {"limit": 0}, id="movements-with-a-zero-limit"),
            pytest.param(
                MOVEMENTS, {"since": "yesterday"}, id="movements-since-an-unparseable-date"
            ),
            pytest.param(
                MOVEMENTS,
                {"until": "2026-08-02T12:00:00"},
                id="movements-until-a-date-with-no-offset",
            ),
            pytest.param(MOVEMENTS, {"min_g": -5}, id="movements-with-a-negative-minimum"),
            pytest.param(MOVEMENTS, {"max_g": "heavy"}, id="movements-with-a-textual-maximum"),
            pytest.param(MOVEMENTS, {"colours": "000000"}, id="movements-with-one-bare-colour"),
            pytest.param(
                MOVEMENTS,
                {"colours": ["000000"] * 65},
                id="movements-with-more-colours-than-a-palette",
            ),
            pytest.param(TRAYS_SYNC, {"surprise": 1}, id="trays-sync-accepts-no-fields-at-all"),
            pytest.param(STATISTICS, {"period": "forever"}, id="statistics-with-an-unknown-period"),
            pytest.param(STATISTICS, {"period": 30}, id="statistics-with-a-numeric-period"),
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
        assert payload["location"] == {
            "kind": "STORAGE",
            "printer": None,
            "ams": None,
            "slot": None,
            "label": "Storage",
        }
        assert payload["movement_count"] == 1
        assert payload["has_anomaly"] is False


class TestFinished:
    """The Finished tab's read: the same summary shape as the list, over the spools whose
    filament is gone — run out or thrown away — and nothing still holding any."""

    async def test_an_untouched_ledger_has_finished_nothing(self, ws: WsClient) -> None:
        assert await ws.result_list(FINISHED) == []

    async def test_depleted_and_discarded_spools_appear_and_stock_does_not(
        self, ws: WsClient
    ) -> None:
        await a_created_spool(ws, label="still printing")
        empty = await a_created_spool(ws, label="ran out")
        await ws.result_dict(ADJUST, spool_id=empty, amount_g=-1000, reason="printed it all")
        binned = await a_created_spool(ws, label="thrown away")
        await ws.result_dict(DISCARD, spool_id=binned, mode="whole_spool", reason="water damage")

        payload = await ws.result_list(FINISHED)

        assert {(entry["id"], entry["state"]) for entry in payload} == {
            (empty, "DEPLETED"),
            (binned, "DISCARDED"),
        }
        # The same serialiser as the list, so the tab renders the same cards.
        by_id = {entry["id"]: entry for entry in payload}
        assert by_id[empty]["name"] == "ran out"
        assert by_id[empty]["balance_g"] == 0
        # The Inventory read is deliberately unchanged: a depleted spool is still a real
        # object — the AMS view and the sensors keep resolving it — and the panel's
        # Inventory grid is what excludes it, not the query.
        listed = {entry["id"] for entry in await ws.result_list(LIST)}
        assert empty in listed
        assert binned not in listed


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


class TestUpdateTheTag:
    """The one field of `spools/update` where **absent and null differ** (docs/14 §14.2).

    Every other field reads null as "leave unchanged"; the tag is the only clearable one,
    so null is what clears it and omitting the key is what leaves it alone. The deviation
    is deliberate, it is stated in the schema comment, and these are the tests that keep it
    from being uniformed away by a later reader.
    """

    async def test_absent_leaves_the_tag_alone(self, ws: WsClient, harness: Harness) -> None:
        spool_id = await a_created_spool(ws, tag_uid="A1B2C3D4")

        await ws.result_dict(UPDATE, spool_id=spool_id, label="renamed")

        spool = (await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))).summary.spool
        assert spool.tag_uid == TagUid("A1B2C3D4")
        assert spool.tag_source is TagSource.MANUAL

    async def test_null_clears_the_tag_and_its_provenance(
        self, ws: WsClient, harness: Harness
    ) -> None:
        spool_id = await a_created_spool(ws, tag_uid="A1B2C3D4")

        await ws.result_dict(UPDATE, spool_id=spool_id, tag_uid=None)

        spool = (await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))).summary.spool
        assert spool.tag_uid is None
        assert spool.tag_source is None

    async def test_a_string_sets_the_tag_as_the_users_own(
        self, ws: WsClient, harness: Harness
    ) -> None:
        spool_id = await a_created_spool(ws)

        await ws.result_dict(UPDATE, spool_id=spool_id, tag_uid="A1B2C3D4")

        spool = (await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))).summary.spool
        assert spool.tag_uid == TagUid("A1B2C3D4")
        assert spool.tag_source is TagSource.MANUAL

    async def test_an_empty_string_is_refused_rather_than_read_as_a_clear(
        self, ws: WsClient
    ) -> None:
        """Blank is not a third way of saying null. `TagUid` refuses it, and the error is a
        message the panel can show rather than a silent erasure."""
        spool_id = await a_created_spool(ws, tag_uid="A1B2C3D4")

        code, _message = await ws.error(UPDATE, spool_id=spool_id, tag_uid="")
        assert code == "InvalidValueError"

    async def test_a_detected_tag_is_refused_with_its_own_error(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Criterion 5. The dialog never offers the input; the command refuses it anyway,
        and `guarded` turns the domain error into something the panel can render."""
        spool_id = await a_created_spool(ws, tag_uid="A1B2C3D4", tag_source="DETECTED")

        code, message = await ws.error(UPDATE, spool_id=spool_id, tag_uid="BEEF0001")
        assert code == "TagNotEditableError"
        assert "printer" in message

        code, _message = await ws.error(UPDATE, spool_id=spool_id, tag_uid=None)
        assert code == "TagNotEditableError"

        spool = (await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))).summary.spool
        assert spool.tag_uid == TagUid("A1B2C3D4")

    async def test_a_colliding_tag_must_be_deliberate(self, ws: WsClient, harness: Harness) -> None:
        """Criterion 7 — the same rule as UC-01, over the edit path."""
        await a_created_spool(ws, tag_uid="A1B2C3D4", label="the first one")
        spool_id = await a_created_spool(ws, label="the second one")

        code, message = await ws.error(UPDATE, spool_id=spool_id, tag_uid="A1B2C3D4")
        assert code == "DuplicateTagNotConfirmedError"
        assert "the first one" in message

        await ws.result_dict(
            UPDATE, spool_id=spool_id, tag_uid="A1B2C3D4", confirm_duplicate_tag=True
        )
        spool = (await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))).summary.spool
        assert spool.tag_uid == TagUid("A1B2C3D4")

    async def test_a_spool_registered_from_the_sync_strip_is_detected(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Criterion 9, over the command the panel's register-from-sync path calls."""
        spool_id = await a_created_spool(ws, tag_uid="A1B2C3D4", tag_source="DETECTED")

        spool = (await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))).summary.spool
        assert spool.tag_source is TagSource.DETECTED

    async def test_an_omitted_source_registers_a_tag_as_the_users_own(
        self, ws: WsClient, harness: Harness
    ) -> None:
        spool_id = await a_created_spool(ws, tag_uid="A1B2C3D4")

        spool = (await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))).summary.spool
        assert spool.tag_source is TagSource.MANUAL

    async def test_an_unknown_provenance_never_reaches_the_domain(self, ws: WsClient) -> None:
        with pytest.raises(vol.Invalid):
            ws.parse(
                CREATE,
                material="PLA",
                colour="000000",
                opening_weight_g=1000,
                tag_uid="A1B2C3D4",
                tag_source="GUESSED",
            )


class TestTheEditDialogsWeightCorrection:
    """The panel's edit dialog submits two commands, in one order (docs/14 §14.2).

    The routing rule — *absolute is a reconciliation, relative is an adjustment* — is a
    rule, so it is pinned here rather than left in the one layer that has no harness. What
    the panel owns is which of the two it calls; what these tests own is that each call
    produces exactly the movement the dialog promises.
    """

    async def test_metadata_alone_writes_no_movement(self, ws: WsClient, harness: Harness) -> None:
        """Criterion 4: both correction fields empty means one command and no movement."""
        spool_id = await a_created_spool(ws)
        before = await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))

        await ws.result_dict(UPDATE, spool_id=spool_id, label="Rebadged")

        after = await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))
        assert len(after.lines) == len(before.lines) == 1
        assert after.summary.balance == before.summary.balance

    async def test_an_absolute_restatement_is_one_reconciliation_of_the_difference(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Criterion 3, first half. `includes_core` is false because the field asks for
        remaining *filament*, not for a scale reading — there is no reel to subtract."""
        spool_id = await a_created_spool(ws)
        await ws.result_dict(UPDATE, spool_id=spool_id, label="Rebadged")

        result = await ws.result_dict(
            RECONCILE,
            spool_id=spool_id,
            measured_g=840.5,
            includes_core=False,
            note="Corrected from the edit dialog",
        )

        assert result["delta_g"] == pytest.approx(-159.5)
        detail = await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))
        corrections = [
            line for line in detail.lines if line.movement.type is MovementType.RECONCILIATION
        ]
        assert len(corrections) == 1
        assert corrections[0].movement.amount == Grams.of(-159.5)
        assert corrections[0].movement.note == "Corrected from the edit dialog"
        assert detail.summary.balance == Grams.of(840.5)

    async def test_a_relative_fix_is_one_adjustment_carrying_its_reason(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Criterion 3, second half. The reason is not optional here for the reason the
        adjust dialog already prints: an unexplained adjustment is indistinguishable from
        a bug."""
        spool_id = await a_created_spool(ws)
        await ws.result_dict(UPDATE, spool_id=spool_id, label="Rebadged")

        await ws.result_dict(ADJUST, spool_id=spool_id, amount_g=-12.5, reason="spillage")

        detail = await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))
        corrections = [
            line for line in detail.lines if line.movement.type is MovementType.MANUAL_ADJUSTMENT
        ]
        assert len(corrections) == 1
        assert corrections[0].movement.amount == Grams.of(-12.5)
        assert corrections[0].movement.note == "spillage"
        assert detail.summary.balance == Grams.of(987.5)

    async def test_a_refused_correction_leaves_the_metadata_edit_standing(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Two independent writes, and the API has no transaction spanning them — so the
        dialog does not pretend it does. The edit lands; the movement does not."""
        spool_id = await a_created_spool(ws)

        await ws.result_dict(UPDATE, spool_id=spool_id, label="Rebadged")
        code, _message = await ws.error(ADJUST, spool_id=spool_id, amount_g=-12.5, reason="  ")
        assert code == "InvalidValueError"

        detail = await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))
        assert detail.summary.spool.label == "Rebadged"
        assert len(detail.lines) == 1


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

        await ws.result_dict(MOUNT, spool_id=spool_id, **tray_wire(2))
        (payload,) = await ws.result_list(LIST)
        assert payload["location"] == {
            "kind": "AMS_SLOT",
            "printer": A_PRINTER.value,
            "ams": 1,
            "slot": 2,
            "label": "AMS slot 2",
        }

        await ws.result_dict(UNMOUNT, spool_id=spool_id)
        (payload,) = await ws.result_list(LIST)
        assert payload["location"] == {
            "kind": "STORAGE",
            "printer": None,
            "ams": None,
            "slot": None,
            "label": "Storage",
        }

    async def test_a_caller_that_names_no_printer_lands_in_the_tray_space_in_use(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The compatibility that matters, and the corruption it prevents.

        An automation written before a tray had three parts sends a slot and nothing else.
        The absence is answered by the runtime — the machine this ledger follows — never by
        a bare sentinel: the migrated rows already carry the discovered serial, and
        defaulting to the sentinel would open a *second* tray space in which every slot
        looked free. Two spools in tray 1, with the widened index correctly seeing two
        different trays and looking away.
        """
        plant_registry(harness.hass, REGISTRY_ROWS)
        harness.runtime.printer = ReadPrinterState(
            gateway=BambuLabGateway(as_hass(harness.hass)),
            spools=SqliteSpoolRepository(harness.ledger.database),
            queries=harness.ledger.use_cases.queries,
        )
        spool_id = await a_created_spool(ws)

        await ws.result_dict(MOUNT, spool_id=spool_id, slot=1)

        location = (
            await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))
        ).summary.spool.location
        assert location == AmsSlot(a_tray(1))

    async def test_with_two_machines_a_slot_alone_is_refused_rather_than_guessed(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The v1 compatibility above held because there was one machine for it to mean.

        With two, *slot 1* names two trays and there is nothing to prefer between them. The
        caller gets a sentence naming both serials instead of a mount that landed somewhere
        plausible — which is the failure this whole release is about (docs/05 §5.4).
        """
        plant_registry(harness.hass, REGISTRY_ROWS + second_printer_rows())
        harness.runtime.printer = ReadPrinterState(
            gateway=BambuLabGateway(as_hass(harness.hass)),
            spools=SqliteSpoolRepository(harness.ledger.database),
            queries=harness.ledger.use_cases.queries,
        )
        spool_id = await a_created_spool(ws)

        _, message = await ws.error(MOUNT, spool_id=spool_id, slot=1)

        assert "more than one printer" in message
        assert A_PRINTER.value in message
        location = (
            await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))
        ).summary.spool.location
        assert location == Storage()

    async def test_naming_the_printer_mounts_into_that_machines_tray(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The panel always names one, because the AMS section the button was drawn in
        knows which machine's tray 1 was tapped."""
        plant_registry(harness.hass, REGISTRY_ROWS + second_printer_rows())
        harness.runtime.printer = ReadPrinterState(
            gateway=BambuLabGateway(as_hass(harness.hass)),
            spools=SqliteSpoolRepository(harness.ledger.database),
            queries=harness.ledger.use_cases.queries,
        )
        spool_id = await a_created_spool(ws)

        await ws.result_dict(MOUNT, spool_id=spool_id, printer=ANOTHER_PRINTER.value, ams=1, slot=1)

        location = (
            await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))
        ).summary.spool.location
        assert location == AmsSlot(a_tray(1, printer=ANOTHER_PRINTER))

    async def test_every_mutation_refreshes_the_entities(
        self, ws: WsClient, harness: Harness
    ) -> None:
        spool_id = await a_created_spool(ws)
        await ws.result_dict(MOUNT, spool_id=spool_id, **tray_wire(1))
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
        await ws.result_dict(MOUNT, spool_id=spool_id, **tray_wire(1))
        review_id = await an_open_review(harness)

        (payload,) = await ws.result_list(REVIEWS_LIST)

        assert payload == {
            "id": review_id,
            "job_id": "job-1",
            "job_name": "bracket_v3.gcode.3mf",
            # The reader's form of the same name: the trailing `.3mf` is the file's
            # business, and one strip never eats further than the last extension.
            "job_display_name": "bracket_v3.gcode",
            "job_state": "CANCELLED",
            "reason": "CANCELLED",
            "estimator": "LINEAR_PROGRESS",
            "opened_at": EPOCH.isoformat(),
            "layer_reached": 71,
            "total_layers": 209,
            "progress_pct": 34,
            "raw_gcode_state": "pause",
            # A decimal string, never a JSON number: HMS codes are 64-bit and a number
            # would land in the browser as a corrupted double past 2^53.
            "raw_print_error": "50348044",
            # 71 of 209 layers of a 209 g plan: 71 g, frozen to the mounted spool as one
            # charge for the whole tray.
            "estimated_total_g": 71.0,
            "lines": [
                {
                    # The tray in full: approving sends these three back, and a bare
                    # number would no longer say which tray was meant.
                    "printer": A_PRINTER.value,
                    "ams": 1,
                    "slot": 1,
                    "estimated_g": 71.0,
                    "charges": [{"spool_id": spool_id, "amount_g": 71.0}],
                }
            ],
        }

    async def test_an_unattributed_slot_travels_with_no_charges_at_all(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The row most worth showing: nothing was mounted when the review froze, and the
        panel renders the spool picker off exactly this empty list."""
        await an_open_review(harness)

        (payload,) = await ws.result_list(REVIEWS_LIST)

        assert payload["lines"] == [
            {
                "printer": A_PRINTER.value,
                "ams": 1,
                "slot": 1,
                "estimated_g": 71.0,
                "charges": [],
            }
        ]

    async def test_a_64_bit_hms_code_crosses_the_wire_as_the_exact_decimal_string(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """0x0300010000020001 — a real HMS code shape — exceeds 2^53, the largest integer
        a JSON number survives as once a browser has parsed it into a double. The wire
        carries the decimal string verbatim, so the panel's BigInt conversion is exact at
        any magnitude; the database keeps the integer."""
        await an_open_review(harness, raw_print_error=0x0300010000020001)

        (payload,) = await ws.result_list(REVIEWS_LIST)

        assert payload["raw_print_error"] == "216173881625542657"

    async def test_no_consumption_data_travels_as_estimator_none_with_all_zero_lines(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The shape the panel's distinct no-data card keys off (docs/06 §6.3): estimator
        `NONE` with every line frozen at zero. The spools were mounted, so the rows are
        attributed — only the amounts are missing."""
        spool_id = await a_created_spool(ws)
        await ws.result_dict(MOUNT, spool_id=spool_id, **tray_wire(1))
        job = PrintJob(
            id=PrintJobId("job-nodata"),
            name="bracket_v4.3mf",
            state=PrintJobState.FINISHED,
            started_at=EPOCH,
            # No layers, no percent: estimation is unavailable, and the review opens with
            # the explicit no-data flag instead of a fabricated figure.
            reported_usage={a_tray(1): Grams.of(209)},
            raw_gcode_state="finish",
        )
        await harness.ledger.use_cases.open_pending_review.execute(
            OpenPendingReviewCommand(job=job, reason=ReviewReason.UNCLASSIFIED)
        )

        (payload,) = await ws.result_list(REVIEWS_LIST)

        assert payload["estimator"] == "NONE"
        assert payload["job_state"] == "FINISHED"
        assert payload["raw_print_error"] is None
        assert payload["estimated_total_g"] == 0.0
        assert payload["lines"] == [
            {
                "printer": A_PRINTER.value,
                "ams": 1,
                "slot": 1,
                "estimated_g": 0.0,
                "charges": [{"spool_id": spool_id, "amount_g": 0.0}],
            }
        ]

    async def test_the_vocabularies_the_panel_branches_on_are_pinned(self) -> None:
        """The panel's estimator-label map covers exactly these kinds, and its card header
        branches on exactly these job states. A member added or renamed on either enum must
        arrive together with its rendering — this failing test is how that is remembered."""
        assert {kind.value for kind in EstimatorKind} == {"LINEAR_PROGRESS", "NONE"}
        assert {state.value for state in PrintJobState} == {
            "RUNNING",
            "FINISHED",
            "CANCELLED",
            "FAILED",
        }


class TestReviewsApprove:
    async def test_approval_deducts_and_refreshes(self, ws: WsClient, harness: Harness) -> None:
        spool_id = await a_created_spool(ws)
        await ws.result_dict(MOUNT, spool_id=spool_id, **tray_wire(1))
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
        """Each entry names the tray the card rendered, which is what the panel sends back."""
        spool_id = await a_created_spool(ws)
        await an_open_review(harness)
        (pending,) = await ws.result_list(REVIEWS_LIST)
        (line,) = cast("list[dict[str, object]]", pending["lines"])

        await ws.result_dict(
            REVIEWS_APPROVE,
            review_id=pending["id"],
            amounts=[{**_tray_of(line), "amount_g": 12.5}],
            assign=[{**_tray_of(line), "spool_id": spool_id}],
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

    async def test_a_tray_may_be_split_across_two_spools_in_one_approval(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """A spool emptied mid-print and was replaced in the same tray. The printer
        reported one figure for that tray; it belongs to two spools (docs/06 §6.3)."""
        emptied = await a_created_spool(ws)
        replacement = await a_created_spool(ws)
        await ws.result_dict(MOUNT, spool_id=emptied, **tray_wire(1))
        review_id = await an_open_review(harness)

        await ws.result_dict(
            REVIEWS_APPROVE,
            review_id=review_id,
            charges=[
                {
                    **TRAY_1_WIRE,
                    "charges": [
                        {"spool_id": emptied, "amount_g": 11},
                        {"spool_id": replacement, "amount_g": 60},
                    ],
                }
            ],
        )

        history = cast(
            "list[dict[str, object]]",
            (await ws.result_dict(GET, spool_id=replacement))["history"],
        )
        assert history[0]["type"] == "ESTIMATED_CONSUMPTION"
        assert history[0]["amount_g"] == -60.0
        assert await ws.result_list(REVIEWS_LIST) == []

    async def test_an_unresolved_slot_is_an_error_reply_not_a_crash(
        self, ws: WsClient, harness: Harness
    ) -> None:
        review_id = await an_open_review(harness)

        code, message = await ws.error(REVIEWS_APPROVE, review_id=review_id)

        assert code == "UnresolvedSlotError"
        assert "must add up" in message
        assert (await ws.result_list(REVIEWS_LIST)) != []  # still pending, nothing written

    async def test_a_split_that_does_not_add_up_is_an_error_reply_too(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The same refusal, reached from the other side: 11 g of a 71 g tray attributed
        leaves 60 g that came off something, and accepting it would lose them."""
        emptied = await a_created_spool(ws)
        await ws.result_dict(MOUNT, spool_id=emptied, **tray_wire(1))
        review_id = await an_open_review(harness)

        code, message = await ws.error(
            REVIEWS_APPROVE,
            review_id=review_id,
            charges=[{**TRAY_1_WIRE, "charges": [{"spool_id": emptied, "amount_g": 11}]}],
        )

        assert code == "UnresolvedSlotError"
        assert "confirms 71.0 g and charges 11.0 g" in message
        assert (await ws.result_list(REVIEWS_LIST)) != []  # still pending, nothing written

    async def test_a_double_approval_is_refused_with_the_domains_words(
        self, ws: WsClient, harness: Harness
    ) -> None:
        spool_id = await a_created_spool(ws)
        await ws.result_dict(MOUNT, spool_id=spool_id, **tray_wire(1))
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
        await ws.result_dict(MOUNT, spool_id=spool_id, **tray_wire(1))
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


class TestMovements:
    async def test_an_empty_ledger_is_an_empty_history(self, ws: WsClient) -> None:
        assert await ws.result_list(MOVEMENTS) == []

    async def test_the_panel_sees_the_documented_row_shape(self, ws: WsClient) -> None:
        """Everything the History table renders (docs/06 §6.6), in one payload, newest
        first: amounts signed at one decimal, source verbatim, the nullable trio null.

        Since docs/14 §14.4 the row also names its own subject — `movement_id`,
        `spool_id`, the entry's `direction` and whether it is `voided` — so a row action
        can be offered or withheld without a second query.
        """
        spool_id = await a_created_spool(ws, label="PLA Basic Black")
        await ws.result_dict(ADJUST, spool_id=spool_id, amount_g=-100, reason="lamp shade")

        rows = await ws.result_list(MOVEMENTS)

        assert [{k: v for k, v in row.items() if k != "movement_id"} for row in rows] == [
            {
                "occurred_at": EPOCH.isoformat(),
                "spool_id": spool_id,
                "spool_name": "PLA Basic Black",
                "spool_colour": "#000000",
                "type": "MANUAL_ADJUSTMENT",
                "amount_g": -100.0,
                "direction": "DECREASE",
                "voided": False,
                "source": "USER_CONFIRMED",
                "job_id": None,
                "job_name": None,
                "job_display_name": None,
                "review_id": None,
                "note": "lamp shade",
            },
            {
                "occurred_at": EPOCH.isoformat(),
                "spool_id": spool_id,
                "spool_name": "PLA Basic Black",
                "spool_colour": "#000000",
                "type": "OPENING_BALANCE",
                "amount_g": 1000.0,
                # An opening balance is the one entry that only ever goes one way, and
                # the row says so from its own sign rather than from its type.
                "direction": "INCREASE",
                "voided": False,
                "source": "USER_CONFIRMED",
                "job_id": None,
                "job_name": None,
                "job_display_name": None,
                "review_id": None,
                "note": "Registered",
            },
        ]
        # Distinct, non-null, and the handle every correction command takes.
        ids = [row["movement_id"] for row in rows]
        assert all(ids)
        assert len(set(ids)) == 2

    async def test_an_approved_estimate_carries_its_job_name_and_review_id(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The linkage the movement rows already store, resolved for the table: `job_id`
        becomes the name the user recognises, `review_id` travels verbatim."""
        spool_id = await a_created_spool(ws)
        await ws.result_dict(MOUNT, spool_id=spool_id, **tray_wire(1))
        review_id = await an_open_review(harness)
        await ws.result_dict(REVIEWS_APPROVE, review_id=review_id)

        newest = (await ws.result_list(MOVEMENTS))[0]

        assert {k: v for k, v in newest.items() if k != "movement_id"} == {
            "occurred_at": EPOCH.isoformat(),
            "spool_id": spool_id,
            "spool_name": "Bambu Lab PLA",
            "spool_colour": "#000000",
            "type": "ESTIMATED_CONSUMPTION",
            "amount_g": -71.0,
            "direction": "DECREASE",
            "voided": False,
            "source": "USER_CONFIRMED",
            "job_id": "job-1",
            "job_name": "bracket_v3.gcode.3mf",
            "job_display_name": "bracket_v3.gcode",
            "review_id": review_id,
            "note": "Slot 1 of a reviewed print",
        }
        assert newest["movement_id"]

    async def test_the_limit_caps_from_the_newest_end(self, ws: WsClient) -> None:
        spool_id = await a_created_spool(ws)
        await ws.result_dict(ADJUST, spool_id=spool_id, amount_g=-1, reason="older")
        await ws.result_dict(ADJUST, spool_id=spool_id, amount_g=-2, reason="newest")

        (only,) = await ws.result_list(MOVEMENTS, limit=1)

        assert only["note"] == "newest"


class TestMovementFilters:
    """The filter payload, over the wire (docs/05 §5.6, FEATURE-REQUESTS §5).

    The narrowing itself is pinned in `tests/application/test_movement_history.py`, against
    the SQL. What is verified here is the translation: what the panel sends, what reaches
    `MovementFilter`, and what a bad value comes back as.
    """

    async def test_free_text_narrows_the_history_server_side(self, ws: WsClient) -> None:
        spool_id = await a_created_spool(ws, label="PLA Basic Black")
        await ws.result_dict(ADJUST, spool_id=spool_id, amount_g=-100, reason="lamp shade")
        await ws.result_dict(ADJUST, spool_id=spool_id, amount_g=-2, reason="purge tower")

        rows = await ws.result_list(MOVEMENTS, search="lamp")

        assert [row["note"] for row in rows] == ["lamp shade"]

    async def test_a_weight_bound_is_read_as_a_magnitude(self, ws: WsClient) -> None:
        """`min_g` is a size, so the 100 g the lamp shade *took away* is over 50 and the
        2 g purge is not. The schema refuses a negative bound outright: there is no such
        thing as an entry smaller than nothing."""
        spool_id = await a_created_spool(ws)
        await ws.result_dict(ADJUST, spool_id=spool_id, amount_g=-100, reason="lamp shade")
        await ws.result_dict(ADJUST, spool_id=spool_id, amount_g=-2, reason="purge tower")

        rows = await ws.result_list(MOVEMENTS, min_g=50)

        assert [row["amount_g"] for row in rows] == [-100.0, 1000.0]

    async def test_a_colour_filter_names_the_colour_of_a_spool(self, ws: WsClient) -> None:
        """The panel sends the swatch the user clicked; the join is the server's problem."""
        await a_created_spool(ws, label="Black", colour="000000")
        await a_created_spool(ws, label="Ivory", colour="FFFFF0")

        rows = await ws.result_list(MOVEMENTS, colours=["FFFFF0"])

        assert [row["spool_name"] for row in rows] == ["Ivory"]

    async def test_a_date_bound_crosses_the_wire_as_an_offset_timestamp(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The one filter that is not a plain scalar. It arrives as ISO-8601 **with an
        offset** — the schema refuses it without one, because a wall clock names no
        instant — and it bounds the ledger inclusively."""
        spool_id = await a_created_spool(ws)
        harness.ledger.clock.advance(days=1)
        boundary = harness.ledger.clock.now()
        await ws.result_dict(ADJUST, spool_id=spool_id, amount_g=-100, reason="lamp shade")

        rows = await ws.result_list(MOVEMENTS, since=boundary.isoformat())

        assert [row["note"] for row in rows] == ["lamp shade"]

    async def test_a_message_carrying_no_filters_is_the_whole_history(self, ws: WsClient) -> None:
        """*Clear every filter*, as the wire expresses it: the panel stops sending the
        keys and the command answers with the ledger it always answered with. An emptied
        search box sends `""`, which is the same absence and is read as one."""
        spool_id = await a_created_spool(ws)
        await ws.result_dict(ADJUST, spool_id=spool_id, amount_g=-100, reason="lamp shade")

        whole = await ws.result_list(MOVEMENTS)

        assert [row["note"] for row in whole] == ["lamp shade", "Registered"]
        assert await ws.result_list(MOVEMENTS, search="") == whole
        assert await ws.result_list(MOVEMENTS, colours=[]) == whole

    async def test_a_malformed_colour_is_a_message_rather_than_a_stack_trace(
        self, ws: WsClient
    ) -> None:
        """The schema bounds what it can — types, ranges, timestamps — and hands the rest
        to the value object, whose `InvalidValueError` is a `DomainError` and so reaches
        the panel as a sentence through `guarded`."""
        code, message = await ws.error(MOVEMENTS, colours=["chartreuse"])

        assert code == "InvalidValueError"
        assert "chartreuse" in message


class TestStatistics:
    """One period's figures, computed server-side (docs/15 §15.6).

    The harness clock never moves, so every timestamp below is an exact offset from
    `EPOCH` — which is what lets these assertions pin the whole payload rather than poke
    at a key or two.
    """

    async def a_recorded_print(
        self,
        harness: Harness,
        *,
        job_id: str = "job-vase",
        name: str = "vase_final.gcode.3mf",
        used: str = "84.1",
        minutes: int = 95,
    ) -> None:
        """A completed job through UC-04 — the websocket has no command that records one,
        because recording is the gateway's job."""
        await harness.ledger.use_cases.record_print_consumption.execute(
            PrintJob(
                id=PrintJobId(job_id),
                name=name,
                state=PrintJobState.FINISHED,
                started_at=EPOCH,
                ended_at=EPOCH + timedelta(minutes=minutes),
                reported_usage={a_tray(1): Grams.of(used)},
            )
        )

    async def test_a_fresh_ledger_answers_the_documented_empty_shape(self, ws: WsClient) -> None:
        """Zeros and nulls, never absent keys: the panel branches on `empty` to choose its
        teaching state, and on `print_time` being null to omit the card entirely."""
        assert await ws.result_dict(STATISTICS) == {
            "period": "30d",
            "since": (EPOCH - timedelta(days=30)).isoformat(),
            "empty": True,
            "consumed_g": 0,
            "wasted_g": 0,
            "prints": {"finished": 0, "cancelled": 0, "failed": 0, "total": 0},
            "reviews": {"approved": 0, "dismissed": 0, "total": 0},
            "by_colour": [],
            "by_material": [],
            "top_prints": [],
            "print_time": None,
        }

    async def test_the_panel_sees_the_documented_statistics_shape(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Everything the Stats tab renders, in one payload."""
        spool_id = await a_created_spool(ws, label="PLA Basic Black")
        await ws.result_dict(MOUNT, spool_id=spool_id, **tray_wire(1))
        await self.a_recorded_print(harness)
        await ws.result_dict(
            DISCARD, spool_id=spool_id, mode="partial", reason="tangled", amount_g=12.4
        )

        assert await ws.result_dict(STATISTICS) == {
            "period": "30d",
            "since": (EPOCH - timedelta(days=30)).isoformat(),
            "empty": False,
            # 84.1 g printed and 12.4 g binned, each rounded once, on the two sides of the
            # line docs/14 §14.4.5 draws: a discard is waste, never printing.
            "consumed_g": 84,
            "wasted_g": 12,
            "prints": {"finished": 1, "cancelled": 0, "failed": 0, "total": 1},
            "reviews": {"approved": 0, "dismissed": 0, "total": 0},
            "by_colour": [{"colour": "#000000", "grams": 84}],
            "by_material": [{"material": "PLA", "grams": 84}],
            "top_prints": [
                {
                    "job_id": "job-vase",
                    "name": "vase_final.gcode.3mf",
                    "display_name": "vase_final.gcode",
                    "started_at": EPOCH.isoformat(),
                    "grams": 84,
                }
            ],
            "print_time": {"total_minutes": 95, "average_minutes": 95, "prints": 1},
        }

    async def test_the_period_is_applied_server_side(self, ws: WsClient, harness: Harness) -> None:
        """The whole point of the parameter: the browser never filters, so it never needs
        the ledger, and the visibility law stays in one testable place."""
        spool_id = await a_created_spool(ws)
        await ws.result_dict(MOUNT, spool_id=spool_id, **tray_wire(1))
        await harness.ledger.use_cases.record_print_consumption.execute(
            PrintJob(
                id=PrintJobId("job-old"),
                name="last_winter.gcode.3mf",
                state=PrintJobState.FINISHED,
                started_at=EPOCH - timedelta(days=200),
                ended_at=EPOCH - timedelta(days=200) + timedelta(minutes=60),
                reported_usage={a_tray(1): Grams.of(500)},
            )
        )

        assert (await ws.result_dict(STATISTICS))["consumed_g"] == 0
        assert (await ws.result_dict(STATISTICS, period="90d"))["consumed_g"] == 0
        assert (await ws.result_dict(STATISTICS, period="all"))["consumed_g"] == 500

    async def test_all_time_reports_no_cut_off_date(self, ws: WsClient) -> None:
        payload = await ws.result_dict(STATISTICS, period="all")

        assert payload["period"] == "all"
        assert payload["since"] is None


class TestTraysSync:
    """The startup reconciliation pass on demand: the captured gateway feeding the real
    ledger, with the per-slot outcome the panel's strip renders."""

    def wire(self, harness: Harness, auto_mount: bool = True, auto_register: bool = False) -> None:
        """Install the reference instance and the pass, as the composition root wires it.

        The harness ledger wires `DetectSpool` with auto-registration off — the captured
        trays carry full hints, and most scenarios exist to observe the reporting
        branches. A scenario that deviates on either flag builds its own, the way the
        application suite does — the fixture stays one honest wiring, not a matrix.
        """
        plant_registry(harness.hass, REGISTRY_ROWS)
        for entity_id in TRAY_ATTRIBUTES:
            harness.hass.states.by_entity_id[entity_id] = tray_state(entity_id)
        detect_spool = (
            harness.ledger.use_cases.detect_spool
            if auto_mount and not auto_register
            else DetectSpool(
                SqliteSpoolRepository(harness.ledger.database),
                harness.ledger.events,
                harness.ledger.database,
                harness.ledger.clock,
                auto_mount=auto_mount,
                register_spool=harness.ledger.use_cases.register_spool,
                default_opening_weight=Grams.of(1000),
                default_core_weight=Grams.of(250),
                auto_register=auto_register,
            )
        )
        harness.runtime.sync_trays = TraySync(
            gateway=BambuLabGateway(as_hass(harness.hass)),
            detect_spool=detect_spool,
            spools=SqliteSpoolRepository(harness.ledger.database),
        )

    async def test_without_a_wired_pass_the_reply_is_the_honest_dormant_flag(
        self, ws: WsClient
    ) -> None:
        """The harness installs no printer, exactly like a test bench — the panel gets
        the flag it renders as "no printer connected", never a spinner."""
        assert await ws.result_dict(TRAYS_SYNC) == {"dormant": True, "slots": []}

    async def test_a_dormant_gateway_reports_dormant_not_four_empty_slots(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """`ha-bambulab` installed but no tray entities: the pass exists, the gateway
        under it is dormant, and absence of a printer is not an absence of spools."""
        plant_registry(
            harness.hass, [row for row in REGISTRY_ROWS if row["translation_key"] != "tray"]
        )
        harness.runtime.sync_trays = TraySync(
            gateway=BambuLabGateway(as_hass(harness.hass)),
            detect_spool=harness.ledger.use_cases.detect_spool,
            spools=SqliteSpoolRepository(harness.ledger.database),
        )

        assert await ws.result_dict(TRAYS_SYNC) == {"dormant": True, "slots": []}

    async def test_the_pass_heals_the_ledger_and_reports_every_slot(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The reference drift, on demand: tray 1 carries a registered tag while the
        ledger says storage. The sync mounts it, reports it mounted, names the unknown
        tags, and refuses the unreadable one — and the entities hear about the mutation."""
        spool_id = await a_spool(harness.ledger, tag_uid=TRAY_1_TAG, label="PLA Basic Black")
        self.wire(harness)
        refreshes = harness.coordinator.refresh_count

        result = await ws.result_dict(TRAYS_SYNC)

        assert result["dormant"] is False
        slots = cast("list[dict[str, object]]", result["slots"])
        assert [(entry["slot"], entry["status"]) for entry in slots] == [
            (1, "mounted"),
            (2, "unknown_tag"),
            (3, "no_tag"),
            (4, "unknown_tag"),
        ]
        assert slots[0]["spool_id"] == spool_id
        assert slots[0]["spool_name"] == "PLA Basic Black"
        location = (
            await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))
        ).summary.spool.location
        assert location == AmsSlot(a_tray(1))
        assert harness.coordinator.refresh_count == refreshes + 1

    async def test_a_move_while_off_heals_before_the_new_tray_registers(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The ledger has the spool in tray 2; reality has its tag in tray 1 and tray 2
        empty. Empty trays run first, so the unmount lands before tray 1 resolves —
        processed the other way round, auto-registration would read the move as a second
        reel of the batch and mint a phantom twin."""
        spool_id = await a_spool(harness.ledger, tag_uid=TRAY_1_TAG)
        await harness.ledger.use_cases.mount_spool.execute(spool_id, a_tray(2))
        self.wire(harness, auto_register=True)
        harness.hass.states.by_entity_id[TRAY_2] = tray_state(
            TRAY_2, {**TRAY_ATTRIBUTES[TRAY_2], "empty": True}
        )

        result = await ws.result_dict(TRAYS_SYNC)

        slots = cast("list[dict[str, object]]", result["slots"])
        assert [(entry["slot"], entry["status"]) for entry in slots][:2] == [
            (1, "mounted"),
            (2, "empty"),
        ]
        twins = [
            s.spool.id
            for s in await harness.ledger.use_cases.queries.overview()
            if s.spool.tag_uid == TRAY_1_TAG
        ]
        assert twins == [spool_id]
        location = (await harness.ledger.use_cases.queries.detail(spool_id)).summary.spool.location
        assert location == AmsSlot(a_tray(1))

    async def test_an_unknown_tag_travels_with_the_register_form_hints(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Full equality on the slot the panel's Register… action feeds from: tag, name,
        material, colour and the reel's own weight, exactly as the tray reported them
        (docs/06 §6.4). The weight rides along so registering this row by hand opens the
        same balance auto-registration would have."""
        self.wire(harness)

        result = await ws.result_dict(TRAYS_SYNC)

        slots = cast("list[dict[str, object]]", result["slots"])
        assert slots[3] == {
            "printer": A_PRINTER.value,
            "ams": 1,
            "slot": 4,
            "status": "unknown_tag",
            "tag_uid": "4289A97100000100",
            "name_hint": "Bambu PLA Matte",
            "material_hint": "PLA",
            "colour_hint": "#FFFFFF",
            "weight_hint_g": 1000,
            "spool_id": None,
            "spool_name": None,
        }
        # Reported, never created: an unknown tag must not become a spool (UC-02).
        assert await ws.result_list(LIST) == []

    async def test_a_tag_that_states_no_weight_sends_no_hint(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Tray 3's untagged reel reports `tray_weight: "0"`. The hint travels null rather
        than zero, so the register form falls back to the configured default instead of
        offering a reel that holds nothing."""
        self.wire(harness)

        result = await ws.result_dict(TRAYS_SYNC)

        slots = cast("list[dict[str, object]]", result["slots"])
        assert slots[2]["weight_hint_g"] is None
        # The tagged rows still carry theirs — the null is the tag's silence, not a
        # serialiser that forgot the field.
        assert slots[0]["weight_hint_g"] == 1000

    async def test_with_auto_mount_off_a_sighting_is_reported_never_acted_on(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The `detected` row, full equality: the tag resolved to exactly one spool, the
        user asked the system not to move spools, so the pass names it and touches
        nothing — reporting `mounted` would be a lie, hiding it would waste the sighting."""
        spool_id = await a_spool(harness.ledger, tag_uid=TRAY_1_TAG, label="PLA Basic Purple")
        self.wire(harness, auto_mount=False)

        result = await ws.result_dict(TRAYS_SYNC)

        slots = cast("list[dict[str, object]]", result["slots"])
        assert slots[0] == {
            "printer": A_PRINTER.value,
            "ams": 1,
            "slot": 1,
            "status": "detected",
            "tag_uid": "3C45C3DB00000100",
            "name_hint": "Bambu PLA Basic",
            "material_hint": "PLA",
            "colour_hint": "#5E43B7",
            "weight_hint_g": 1000,
            "spool_id": spool_id,
            "spool_name": "PLA Basic Purple",
        }
        location = (
            await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))
        ).summary.spool.location
        assert location == Storage()

    async def test_an_ambiguous_tag_asks_instead_of_guessing(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Two spools from one batch legally share a tag. The pass refuses to pick — the
        row carries no spool, and neither candidate moves off the shelf."""
        first = await a_spool(harness.ledger, tag_uid=TRAY_1_TAG, label="first")
        second = await a_spool(
            harness.ledger, tag_uid=TRAY_1_TAG, label="second", confirm_duplicate_tag=True
        )
        self.wire(harness)

        result = await ws.result_dict(TRAYS_SYNC)

        slots = cast("list[dict[str, object]]", result["slots"])
        assert slots[0] == {
            "printer": A_PRINTER.value,
            "ams": 1,
            "slot": 1,
            "status": "ambiguous_tag",
            "tag_uid": "3C45C3DB00000100",
            "name_hint": "Bambu PLA Basic",
            "material_hint": "PLA",
            "colour_hint": "#5E43B7",
            "weight_hint_g": 1000,
            "spool_id": None,
            "spool_name": None,
        }
        for candidate in (first, second):
            location = (
                await harness.ledger.use_cases.queries.detail(SpoolId(candidate))
            ).summary.spool.location
            assert location == Storage()

    async def test_the_pass_is_idempotent_like_the_startup_pass_it_reuses(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Two syncs in a row: the second replays unchanged trays, writes nothing, and
        reports the same outcomes — the panel's button is safe to lean on."""
        await a_spool(harness.ledger, tag_uid=TRAY_1_TAG)
        self.wire(harness)

        first = await ws.result_dict(TRAYS_SYNC)
        second = await ws.result_dict(TRAYS_SYNC)

        assert second == first


class TestSubscribe:
    """The panel's only live channel: one subscription, and the backend pushes.

    What is pinned here is the shape of the mechanism — the current state arrives without
    being asked for, the bus listeners exist while the subscription does, and closing it
    removes every one of them. Between them they are the difference between a push and a
    poll wearing a push's clothes.

    The debounced *delivery* is Home Assistant's own `Debouncer` and needs a running timer
    to observe; what matters here is that the listeners are wired to it and unwired again.
    """

    async def test_subscribing_pushes_the_current_state_without_being_asked(
        self, ws: WsClient
    ) -> None:
        await ws.send(SUBSCRIBE)

        kinds = [
            cast("dict[str, object]", message["event"])["kind"]
            for message in ws.connection.messages
        ]
        assert "ledger" in kinds, "a panel that must fetch its first state is not subscribed"
        assert "printer" in kinds

    async def test_the_first_push_carries_everything_the_ledger_views_read(
        self, ws: WsClient
    ) -> None:
        """Five reads used to be five round trips per change, per open panel. One payload
        now, computed once on the server."""
        await ws.send(SUBSCRIBE)

        ledger = next(
            cast("dict[str, object]", m["event"])
            for m in ws.connection.messages
            if cast("dict[str, object]", m["event"])["kind"] == "ledger"
        )

        assert set(ledger) == {"kind", "spools", "stock", "reviews", "movements", "trash"}

    async def test_it_listens_for_every_ledger_event_while_subscribed(
        self, ws: WsClient, harness: Harness
    ) -> None:
        await ws.send(SUBSCRIBE)

        listening = {entry.event_type for entry in harness.hass.bus.listeners}

        assert listening >= LEDGER_EVENTS

    async def test_closing_the_subscription_removes_every_listener(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """A subscription outliving its panel keeps a read model being computed for a view
        nobody is looking at, once more per navigation away and back."""
        before = {id(entry) for entry in harness.hass.bus.listeners}
        await ws.send(SUBSCRIBE)
        unsubscribe = ws.connection.subscriptions[ws.last_id]

        cast("Callable[[], None]", unsubscribe)()

        after = {id(entry) for entry in harness.hass.bus.listeners}
        assert after == before, "the subscription left listeners behind"
