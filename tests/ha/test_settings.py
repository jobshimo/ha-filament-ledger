"""The Settings tab's two commands (docs/14 §14.6.4).

`settings/get` is readable by anyone — a hidden tab invites "it's broken", while a
labelled read-only one teaches the model. `settings/update` is registered with
`@websocket_api.require_admin`, the considered inverse of the panel's own
`require_admin=False`: weighing a spool is not an administrative act, and changing the
anomaly threshold for the whole household is.

The write goes through `async_update_entry`, which fires the registered update listener
and reloads the entry — the existing, only mechanism by which an option change takes
effect. The fake config-entry registry below records that call, which is the assertion
docs/14 §14.6.4's test obligation names.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
import voluptuous as vol
from homeassistant.exceptions import Unauthorized

from custom_components.filament_ledger.const import (
    CONF_ANOMALY_THRESHOLD,
    CONF_AUTO_MOUNT_ON_RFID,
    CONF_DEFAULT_CORE_WEIGHT,
    CONF_DEFAULT_OPENING_WEIGHT,
)
from custom_components.filament_ledger.infrastructure.ha.websocket_api import (
    async_register_commands,
)

from .conftest import FakeConfigEntry, Harness, as_hass
from .test_websocket_api import WsClient

GET = "filament_ledger/settings/get"
UPDATE = "filament_ledger/settings/update"


@pytest.fixture
def ws(harness: Harness) -> WsClient:
    """The panel's dispatcher over this harness, commands registered as setup does it."""
    async_register_commands(as_hass(harness.hass))
    return WsClient(hass=harness.hass)


@dataclass
class FakeUser:
    """The two fields `require_admin` reads off the connection."""

    is_admin: bool = True
    name: str = "Owner"


@dataclass
class UpdateCall:
    """One `async_update_entry`, as the registry recorded it."""

    entry: FakeConfigEntry
    options: dict[str, object]


@dataclass
class Recorder:
    """The one config-entries method the settings write touches."""

    calls: list[UpdateCall] = field(default_factory=list)

    def async_update_entry(self, entry: FakeConfigEntry, options: dict[str, object]) -> bool:
        """Home Assistant writes the options and fires the update listener, which reloads
        the entry. The fake stops at the write and records it — the reload itself belongs
        to `__init__.py`'s listener, which `test_bambu_gateway` already drives."""
        self.calls.append(UpdateCall(entry=entry, options=dict(options)))
        entry.options = dict(options)
        return True


@pytest.fixture
def recorder(harness: Harness) -> Recorder:
    """Install the recorder on the fake registry and give the entry its install-time data.

    `entry.data` alone, with `options` empty, is the state of every user who has never
    opened the options flow — which is exactly the audience the Settings tab exists for.
    """
    harness.entry.data = {
        CONF_DEFAULT_OPENING_WEIGHT: 1000,
        CONF_DEFAULT_CORE_WEIGHT: 250,
        CONF_ANOMALY_THRESHOLD: 15,
        CONF_AUTO_MOUNT_ON_RFID: True,
    }
    harness.entry.options = {}
    recorder = Recorder()
    harness.hass.config_entries.async_update_entry = recorder.async_update_entry  # type: ignore[attr-defined]
    return recorder


@pytest.fixture
def admin(ws: WsClient) -> WsClient:
    """A connection Home Assistant would call an administrator's."""
    ws.connection.user = FakeUser(is_admin=True)  # type: ignore[attr-defined]
    return ws


class TestGet:
    async def test_the_effective_options_are_data_overlaid_with_options(
        self, ws: WsClient, harness: Harness, recorder: Recorder
    ) -> None:
        """The composition root's own merge, restated (docs/14 §14.6.4, criterion 6).

        Reading `options` alone would report the install-time answers as unset for anyone
        who never opened the options flow.
        """
        harness.entry.options = {CONF_ANOMALY_THRESHOLD: 25}

        assert await ws.result_dict(GET) == {
            CONF_DEFAULT_OPENING_WEIGHT: 1000,
            CONF_DEFAULT_CORE_WEIGHT: 250,
            CONF_ANOMALY_THRESHOLD: 25,
            CONF_AUTO_MOUNT_ON_RFID: True,
        }

    async def test_an_unconfigured_entry_answers_with_the_shipped_defaults(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Never a blank and never a null: the tab has to show what is in force, and what
        is in force when nothing was chosen is `const.py`'s default."""
        harness.entry.data = {}
        harness.entry.options = {}

        assert await ws.result_dict(GET) == {
            CONF_DEFAULT_OPENING_WEIGHT: 1000,
            CONF_DEFAULT_CORE_WEIGHT: 250,
            CONF_ANOMALY_THRESHOLD: 15,
            CONF_AUTO_MOUNT_ON_RFID: True,
        }

    async def test_reading_is_not_an_administrative_act(
        self, ws: WsClient, recorder: Recorder
    ) -> None:
        """No user on the connection at all, and the read still answers. A non-admin
        seeing the values read-only is the design (docs/14 §14.6.4)."""
        assert set(await ws.result_dict(GET)) == {
            CONF_DEFAULT_OPENING_WEIGHT,
            CONF_DEFAULT_CORE_WEIGHT,
            CONF_ANOMALY_THRESHOLD,
            CONF_AUTO_MOUNT_ON_RFID,
        }


class TestUpdateSchema:
    """Bounds are the config flow's, restated — a typo must be a message, not a stack."""

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({CONF_DEFAULT_OPENING_WEIGHT: 0}, id="opening-weight-below-one"),
            pytest.param({CONF_DEFAULT_OPENING_WEIGHT: 10001}, id="opening-weight-over-range"),
            pytest.param({CONF_DEFAULT_CORE_WEIGHT: -1}, id="core-weight-negative"),
            pytest.param({CONF_DEFAULT_CORE_WEIGHT: 2001}, id="core-weight-over-range"),
            pytest.param({CONF_ANOMALY_THRESHOLD: 0}, id="threshold-below-one"),
            pytest.param({CONF_ANOMALY_THRESHOLD: 101}, id="threshold-over-a-hundred"),
            pytest.param({CONF_AUTO_MOUNT_ON_RFID: "yes"}, id="auto-mount-not-a-boolean"),
            pytest.param({CONF_ANOMALY_THRESHOLD: "a lot"}, id="threshold-not-a-number"),
            pytest.param({"unknown_option": 1}, id="a-field-the-entry-does-not-have"),
        ],
    )
    async def test_the_schema_refuses_it(self, admin: WsClient, payload: dict[str, object]) -> None:
        with pytest.raises(vol.Invalid):
            admin.parse(UPDATE, **payload)

    async def test_an_empty_update_is_legal(self, admin: WsClient, recorder: Recorder) -> None:
        """Every field is optional because the tab may send any subset — and the subset
        may be none of them, which writes the effective settings back unchanged."""
        assert await admin.result_dict(UPDATE) == {"ok": True}
        assert recorder.calls[0].options == {
            CONF_DEFAULT_OPENING_WEIGHT: 1000,
            CONF_DEFAULT_CORE_WEIGHT: 250,
            CONF_ANOMALY_THRESHOLD: 15,
            CONF_AUTO_MOUNT_ON_RFID: True,
        }


class TestUpdate:
    async def test_the_write_goes_through_async_update_entry(
        self, admin: WsClient, harness: Harness, recorder: Recorder
    ) -> None:
        """The assertion docs/14 §14.6.4 names: the options reach the entry through the
        one call that fires the update listener and reloads the integration."""
        assert await admin.result_dict(UPDATE, **{CONF_DEFAULT_OPENING_WEIGHT: 750}) == {"ok": True}

        assert len(recorder.calls) == 1
        assert recorder.calls[0].entry is harness.entry
        assert recorder.calls[0].options[CONF_DEFAULT_OPENING_WEIGHT] == 750

    async def test_a_partial_update_keeps_every_option_it_did_not_name(
        self, admin: WsClient, harness: Harness, recorder: Recorder
    ) -> None:
        """The subset is merged over the *effective* settings, not over `options` alone.

        Writing only what the tab sent would silently revert every option the user had
        already changed but did not touch this time — a setting that changes meaning
        because of what a form omitted.
        """
        harness.entry.options = {CONF_ANOMALY_THRESHOLD: 25}

        await admin.result_dict(UPDATE, **{CONF_AUTO_MOUNT_ON_RFID: False})

        assert recorder.calls[0].options == {
            CONF_DEFAULT_OPENING_WEIGHT: 1000,
            CONF_DEFAULT_CORE_WEIGHT: 250,
            CONF_ANOMALY_THRESHOLD: 25,
            CONF_AUTO_MOUNT_ON_RFID: False,
        }

    async def test_the_new_value_is_what_the_next_read_reports(
        self, admin: WsClient, recorder: Recorder
    ) -> None:
        """Observable in behaviour, not just in the call log: the value the register
        dialog seeds its default weight from is the one that just changed."""
        await admin.result_dict(UPDATE, **{CONF_DEFAULT_OPENING_WEIGHT: 750})

        assert (await admin.result_dict(GET))[CONF_DEFAULT_OPENING_WEIGHT] == 750

    async def test_the_command_carries_no_extra_keys_into_the_options(
        self, admin: WsClient, recorder: Recorder
    ) -> None:
        """`id` and `type` are envelope, not configuration. A leak would put them on the
        entry, where the options flow would then show them back to the user."""
        await admin.result_dict(UPDATE, **{CONF_ANOMALY_THRESHOLD: 40})

        assert "id" not in recorder.calls[0].options
        assert "type" not in recorder.calls[0].options


class TestAdminGate:
    async def test_a_non_admin_update_is_refused_by_the_framework(
        self, ws: WsClient, recorder: Recorder
    ) -> None:
        """`Unauthorized` is raised before the handler is ever scheduled — Home Assistant
        turns it into an `unauthorized` error, and nothing reaches the entry."""
        ws.connection.user = FakeUser(is_admin=False)  # type: ignore[attr-defined]

        with pytest.raises(Unauthorized):
            await ws.send(UPDATE, **{CONF_ANOMALY_THRESHOLD: 40})

        assert recorder.calls == []

    async def test_a_connection_with_no_user_is_refused(
        self, ws: WsClient, recorder: Recorder
    ) -> None:
        ws.connection.user = None  # type: ignore[attr-defined]

        with pytest.raises(Unauthorized):
            await ws.send(UPDATE, **{CONF_ANOMALY_THRESHOLD: 40})

        assert recorder.calls == []

    async def test_the_read_is_not_gated(self, ws: WsClient, recorder: Recorder) -> None:
        """The considered asymmetry: seeing the values is not an administrative act, and
        hiding them would teach the household that the panel is broken."""
        ws.connection.user = FakeUser(is_admin=False)  # type: ignore[attr-defined]

        assert (await ws.result_dict(GET))[CONF_ANOMALY_THRESHOLD] == 15
