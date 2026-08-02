"""The config flow: one entry, everything editable afterwards.

The flows are driven directly — `async_step_user` and `async_step_init` are called the
way the flow manager calls them, with the handful of attributes the manager would have
set. The `FakeConfigEntries` registry answers the unique-id and known-entry lookups.
"""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER, ConfigFlowContext
from homeassistant.data_entry_flow import AbortFlow, FlowResultType

from custom_components.filament_ledger.config_flow import (
    FilamentLedgerConfigFlow,
    FilamentLedgerOptionsFlow,
)
from custom_components.filament_ledger.const import (
    CONF_ANOMALY_THRESHOLD,
    CONF_DEFAULT_CORE_WEIGHT,
    CONF_DEFAULT_OPENING_WEIGHT,
    DOMAIN,
)

from .conftest import FakeConfigEntry, FakeHass, as_hass

VALID = {
    CONF_DEFAULT_OPENING_WEIGHT: 800,
    CONF_DEFAULT_CORE_WEIGHT: 180,
    CONF_ANOMALY_THRESHOLD: 20,
}


def user_flow(hass: FakeHass) -> FilamentLedgerConfigFlow:
    """A flow initialised the way the flow manager initialises one."""
    flow = FilamentLedgerConfigFlow()
    flow.hass = as_hass(hass)
    flow.handler = DOMAIN
    flow.flow_id = "user-flow"
    flow.context = ConfigFlowContext(source=SOURCE_USER)
    return flow


class TestUserStep:
    async def test_the_first_visit_shows_the_form_with_the_documented_defaults(self) -> None:
        result = await user_flow(FakeHass()).async_step_user(None)

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        schema = result["data_schema"]
        assert schema is not None
        assert schema({}) == {
            CONF_DEFAULT_OPENING_WEIGHT: 1000,
            CONF_DEFAULT_CORE_WEIGHT: 250,
            CONF_ANOMALY_THRESHOLD: 15,
        }

    async def test_valid_input_creates_the_entry_with_the_documented_fields(self) -> None:
        result = await user_flow(FakeHass()).async_step_user(dict(VALID))

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "Filament Ledger"
        assert result["data"] == VALID
        assert result["options"] == {}
        assert result["version"] == 1

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            pytest.param(CONF_DEFAULT_OPENING_WEIGHT, 0, id="an-opening-weight-of-nothing"),
            pytest.param(CONF_DEFAULT_OPENING_WEIGHT, 10001, id="an-eleven-kilo-opening"),
            pytest.param(CONF_DEFAULT_CORE_WEIGHT, -1, id="a-negative-reel"),
            pytest.param(CONF_DEFAULT_CORE_WEIGHT, 2001, id="a-two-kilo-reel"),
            pytest.param(CONF_ANOMALY_THRESHOLD, 0, id="a-zero-threshold"),
            pytest.param(CONF_ANOMALY_THRESHOLD, 101, id="a-threshold-past-everything"),
        ],
    )
    async def test_out_of_range_values_are_rejected_by_the_form(
        self, name: str, value: int
    ) -> None:
        result = await user_flow(FakeHass()).async_step_user(None)
        schema = result["data_schema"]
        assert schema is not None
        with pytest.raises(vol.Invalid):
            schema({**VALID, name: value})

    async def test_a_second_ledger_is_refused(self) -> None:
        """A second entry would be a second source of truth about the same shelf."""
        hass = FakeHass()
        hass.config_entries.by_unique_id[(DOMAIN, DOMAIN)] = FakeConfigEntry()

        with pytest.raises(AbortFlow) as refusal:
            await user_flow(hass).async_step_user(None)

        assert refusal.value.reason == "already_configured"


class TestOptionsFlow:
    def options_flow(self, hass: FakeHass, entry: FakeConfigEntry) -> FilamentLedgerOptionsFlow:
        hass.config_entries.known[entry.entry_id] = entry
        flow = FilamentLedgerOptionsFlow()
        flow.hass = as_hass(hass)
        flow.handler = entry.entry_id
        flow.flow_id = "options-flow"
        return flow

    def test_the_config_flow_hands_out_this_options_flow(self) -> None:
        flow = FilamentLedgerConfigFlow.async_get_options_flow(FakeConfigEntry())  # type: ignore[arg-type]
        assert isinstance(flow, FilamentLedgerOptionsFlow)

    async def test_the_form_opens_on_current_settings_not_factory_defaults(self) -> None:
        """A setting chosen at installation must come back editable, with options winning
        over the original data."""
        entry = FakeConfigEntry(data=dict(VALID), options={CONF_ANOMALY_THRESHOLD: 33})
        flow = self.options_flow(FakeHass(), entry)

        result = await flow.async_step_init(None)

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"
        schema = result["data_schema"]
        assert schema is not None
        assert schema({}) == {
            CONF_DEFAULT_OPENING_WEIGHT: 800,
            CONF_DEFAULT_CORE_WEIGHT: 180,
            CONF_ANOMALY_THRESHOLD: 33,
        }

    async def test_submitting_replaces_the_options(self) -> None:
        flow = self.options_flow(FakeHass(), FakeConfigEntry(data=dict(VALID)))
        revised = {**VALID, CONF_DEFAULT_CORE_WEIGHT: 200}

        result = await flow.async_step_init(dict(revised))

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"] == revised
