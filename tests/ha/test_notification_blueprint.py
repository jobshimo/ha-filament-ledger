"""The shipped fixture must agree with the pinned native blueprint and automation schemas."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from homeassistant.components.automation.config import PLATFORM_SCHEMA
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.core import HomeAssistant
from homeassistant.util import yaml as yaml_util
from jinja2.nativetypes import NativeEnvironment

from .conftest import Harness

ASSET = (
    Path(__file__).parents[2] / "custom_components/filament_ledger/www/blueprints/low_stock_v1.yaml"
)


async def test_complete_native_metadata_and_substitution_contract(tmp_path: Path) -> None:
    HomeAssistant(str(tmp_path))
    contract = json.loads(ASSET.with_suffix(".contract.json").read_text())
    data = yaml_util.parse_yaml(ASSET.read_text())
    assert isinstance(data, dict)
    blueprint = Blueprint(
        data,
        expected_domain="automation",
        schema=BLUEPRINT_SCHEMA,
    )
    assert blueprint.metadata == contract["metadata"]
    assert set(contract["input"]) == set(blueprint.inputs)
    substituted = BlueprintInputs(
        blueprint, {"use_blueprint": {"path": contract["path"], "input": contract["input"]}}
    ).async_substitute()
    assert substituted == contract["substituted_config"]
    validated = PLATFORM_SCHEMA(
        {**substituted, "id": "filament_schema_test", "alias": "Schema test"}
    )
    assert validated["mode"] == "queued"
    assert validated["max"] == 100
    assert substituted["triggers"] == [
        {"trigger": "event", "event_type": "filament_ledger_low_stock"}
    ]
    selector = blueprint.inputs["spools"]["selector"]["entity"]
    assert selector["multiple"] is True
    assert selector["filter"] == [{"integration": "filament_ledger", "domain": ["binary_sensor"]}]


@pytest.mark.parametrize(
    ("selected", "entity", "remaining", "expected"),
    [
        ([], None, 99, True),
        (["binary_sensor.one"], None, 99, False),
        (["binary_sensor.one"], "binary_sensor.other", 99, False),
        (["binary_sensor.one"], "binary_sensor.one", 99, True),
        ([], "binary_sensor.one", 100, False),
        ([], None, None, False),
    ],
)
def test_scope_never_broadens_when_selected_entity_is_missing(
    selected: list[str], entity: str | None, remaining: int | None, expected: bool
) -> None:
    contract = json.loads(ASSET.with_suffix(".contract.json").read_text())
    condition = contract["substituted_config"]["conditions"][0]["value_template"]
    result = (
        NativeEnvironment()
        .from_string(condition)
        .render(
            selected_spools=selected,
            spool_id="physical-spool",
            occurrence_id="movement-id",
            remaining_g=remaining,
            threshold_g=100,
            trigger=SimpleNamespace(event=SimpleNamespace(data={"low_stock_entity_id": entity})),
        )
    )
    assert cast(object, result) is expected


async def test_native_blueprint_write_boundary_refuses_non_admin(harness: Harness) -> None:
    from types import SimpleNamespace

    from homeassistant.components.blueprint.websocket_api import async_setup
    from homeassistant.exceptions import Unauthorized

    from .conftest import as_hass
    from .test_websocket_api import WsClient

    async_setup(as_hass(harness.hass))
    client = WsClient(harness.hass)
    client.connection.user = SimpleNamespace(is_admin=False, name="Member")  # type: ignore[attr-defined]
    with pytest.raises(Unauthorized):
        await client.send("blueprint/list", domain="automation")
