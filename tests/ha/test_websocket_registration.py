"""Ordinary authenticated registration and read-only preview contracts."""

import pytest
import voluptuous as vol

from .conftest import Harness
from .test_websocket_api import CREATE, GET, LIST, WsClient
from .test_websocket_api import ws as websocket_fixture

ws = websocket_fixture

PREVIEW = "filament_ledger/spools/preview_registration"
OPEN = "filament_ledger/spools/open"


async def test_preview_has_no_write_or_event(ws: WsClient, harness: Harness) -> None:
    result = await ws.result_dict(
        PREVIEW, opening_basis="MEASURED_GROSS", gross_weight_g=670, core_weight_g=250
    )
    assert result == {
        "opening_weight_g": 420,
        "opening_basis": "MEASURED_GROSS",
        "gross_weight_g": 670,
        "core_weight_g": 250,
    }
    assert await ws.result_list(LIST) == []
    assert harness.ledger.events.published == []
    assert await harness.ledger.database.fetch_one("SELECT * FROM registration_request") is None


async def test_unknown_colour_and_open_have_honest_metadata(ws: WsClient) -> None:
    result = await ws.result_dict(
        CREATE,
        material="OTHER",
        material_other="Custom blend",
        colour_known=False,
        opening_weight_g=420,
    )
    spool_id = result["spool_id"]
    first = await ws.result_dict(GET, spool_id=spool_id)
    assert first["colour_rgba"] is None
    assert first["opening_basis"] == "DECLARED_NET"
    await ws.result_dict(OPEN, spool_id=spool_id)
    await ws.result_dict(OPEN, spool_id=spool_id)
    opened = await ws.result_dict(GET, spool_id=spool_id)
    assert opened["state"] == "ACTIVE"
    assert opened["movement_count"] == 1


async def test_forged_identity_and_automatic_basis_are_rejected(ws: WsClient) -> None:
    with pytest.raises(vol.Invalid):
        ws.parse(CREATE, actor_id="other-user", material="PLA", opening_weight_g=1000)
    with pytest.raises(vol.Invalid):
        ws.parse(PREVIEW, opening_basis="DETECTED_NOMINAL", opening_weight_g=1000)
    code, _ = await ws.error(PREVIEW, opening_basis="MEASURED_GROSS", gross_weight_g=670)
    assert code == "invalid_input"
    code, _ = await ws.error(
        PREVIEW,
        opening_basis="MEASURED_GROSS",
        gross_weight_g=670,
        core_weight_g=250,
        opening_weight_g=421,
    )
    assert code == "invalid_input"


async def test_authenticated_retry_and_field_errors(ws: WsClient) -> None:
    from types import SimpleNamespace

    ws.connection.user = SimpleNamespace(id="household-user", is_admin=False)  # type: ignore[attr-defined]
    payload = {
        "material": "PLA",
        "colour_known": False,
        "opening_weight_g": 420,
        "request_id": "bb2c002e-424c-4dc5-b5e0-4d3b9c8da609",
    }
    first = await ws.result_dict(CREATE, **payload)
    assert await ws.result_dict(CREATE, **payload) == first
    assert len(await ws.result_list(LIST)) == 1
    code, _ = await ws.error(CREATE, **{**payload, "opening_weight_g": 421})
    assert code == "idempotency_conflict"
    code, _ = await ws.error(
        PREVIEW, opening_basis="MEASURED_GROSS", gross_weight_g=200, core_weight_g=250
    )
    assert code == "invalid_input"
    field_error = ws.connection.messages[-1]["error"]
    assert isinstance(field_error, dict)
    assert field_error["field"] == "gross_weight_g"


async def test_guarded_assignment_reports_actual_occupant(ws: WsClient) -> None:
    from ..application.conftest import A_PRINTER

    first = await ws.result_dict(CREATE, material="PLA", colour_known=False, opening_weight_g=1000)
    second = await ws.result_dict(CREATE, material="PLA", colour_known=False, opening_weight_g=1000)
    position = {
        "printer": A_PRINTER.value,
        "ams": 2,
        "slot": 1,
        "expected_source": {"kind": "STORAGE"},
        "expected_destination_spool_id": None,
    }
    await ws.result_dict("filament_ledger/spools/mount", spool_id=first["spool_id"], **position)
    code, _ = await ws.error(
        "filament_ledger/spools/mount", spool_id=second["spool_id"], **position
    )
    assert code == "assignment_conflict"
    error = ws.connection.messages[-1]["error"]
    assert isinstance(error, dict)
    assert error["actual_destination_spool_id"] == first["spool_id"]
    assert error["actual_source"]["kind"] == "STORAGE"
