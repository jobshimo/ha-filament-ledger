"""The Printer tab's read-only command, driven against the captured reference instance.

Every payload here comes from the same fixtures the gateway suite drives (docs/09 §9.4):
the Spanish entity ids, the real tray attributes, the job sensors as the A1 reported them.
Nothing about this command is mocked below the connection — the gateway reads a real
`FakeStates`, the trays resolve through the real registry rules, and the ledger under it
is the same SQLite the application suite uses.

The command's whole claim is that **looking changes nothing** (docs/14 §14.5), so the
suite ends by proving exactly that: movement count and spool locations byte-identical
before and after, which is what distinguishes this path from `trays/sync`.
"""

from __future__ import annotations

from typing import cast

import pytest
from homeassistant.core import State

from custom_components.filament_ledger.domain.value.identifiers import SlotIndex, SpoolId
from custom_components.filament_ledger.domain.value.location import AmsSlot
from custom_components.filament_ledger.infrastructure.ha.bambu_gateway import (
    UNKNOWN_JOB_NAME,
    BambuLabGateway,
)
from custom_components.filament_ledger.infrastructure.ha.printer_state import ReadPrinterState
from custom_components.filament_ledger.infrastructure.ha.websocket_api import (
    async_register_commands,
)
from custom_components.filament_ledger.infrastructure.persistence.spool_repository import (
    SqliteSpoolRepository,
)

from .conftest import Harness, a_spool, as_hass
from .test_bambu_gateway import (
    CURRENT_LAYER,
    GCODE_FILE,
    PRINT_ERROR,
    PROGRESS,
    REGISTRY_ROWS,
    STATUS,
    TOTAL_LAYERS,
    TRAY_1_TAG,
    TRAY_ATTRIBUTES,
    plant_registry,
    print_sensor_state,
    tray_state,
)
from .test_websocket_api import WsClient

PRINTER_STATE = "filament_ledger/printer/state"


@pytest.fixture
def ws(harness: Harness) -> WsClient:
    """The panel's dispatcher over this harness, commands registered as setup does it.

    Declared here rather than imported: pytest resolves a fixture by name, so importing
    one and then naming a test parameter after it is a redefinition ruff is right to
    flag. Two lines is cheaper than the confusion.
    """
    async_register_commands(as_hass(harness.hass))
    return WsClient(hass=harness.hass)


def wire(harness: Harness, rows: list[dict[str, str]] | None = None) -> None:
    """Install the reference instance and the reader, as the composition root wires it."""
    plant_registry(harness.hass, REGISTRY_ROWS if rows is None else rows)
    for entity_id in TRAY_ATTRIBUTES:
        harness.hass.states.by_entity_id[entity_id] = tray_state(entity_id)
    for entity_id in (STATUS, CURRENT_LAYER, TOTAL_LAYERS, PROGRESS, GCODE_FILE, PRINT_ERROR):
        harness.hass.states.by_entity_id[entity_id] = print_sensor_state(entity_id)
    harness.runtime.printer = ReadPrinterState(
        gateway=BambuLabGateway(as_hass(harness.hass)),
        spools=SqliteSpoolRepository(harness.ledger.database),
    )


class TestDormant:
    """Absence of a printer is a fact, and the tab says so instead of spinning."""

    async def test_without_a_wired_reader_the_reply_is_the_flag_and_nothing_else(
        self, ws: WsClient
    ) -> None:
        """The harness installs no printer, exactly like a test bench.

        The reply carries **only** the flag: a hull of nulls beside it would invite the
        panel to render seven dashes for a machine that is not there.
        """
        assert await ws.result_dict(PRINTER_STATE) == {"dormant": True}

    async def test_ha_bambulab_absent_reports_dormant(self, ws: WsClient, harness: Harness) -> None:
        """An empty registry: the reader exists, discovery found nothing under it."""
        wire(harness, rows=[])

        assert await ws.result_dict(PRINTER_STATE) == {"dormant": True}

    async def test_a_printer_without_an_ams_is_not_dormant(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The wider question, deliberately (docs/14 §14.5).

        `dormant` on the gateway asks about *trays*, because the sync pass has nothing to
        do without them. A machine whose job sensors resolved still has a status worth
        showing, and answering dormant here would hide a printer that is plainly present.
        """
        wire(harness, rows=[row for row in REGISTRY_ROWS if row["translation_key"] != "tray"])

        payload = await ws.result_dict(PRINTER_STATE)

        assert payload["dormant"] is False
        assert payload["status"] == "finish"
        assert payload["trays"] == []


class TestPopulated:
    """The captured A1, mid-glance."""

    @pytest.fixture(autouse=True)
    def _wired(self, harness: Harness) -> None:
        wire(harness)

    async def test_the_job_figures_are_the_sensors_verbatim(self, ws: WsClient) -> None:
        payload = await ws.result_dict(PRINTER_STATE)

        assert payload["dormant"] is False
        assert payload["status"] == "finish"
        assert payload["current_layer"] == 71
        assert payload["total_layers"] == 209
        assert payload["progress_pct"] == 34
        assert payload["job_name"] == "381189-Rails for a shelf v2.gcode"

    async def test_the_payload_carries_exactly_the_documented_keys(self, ws: WsClient) -> None:
        """A pin on the shape docs/14 §14.5 specifies, so a field cannot quietly vanish."""
        assert set(await ws.result_dict(PRINTER_STATE)) == {
            "dormant",
            "status",
            "progress_pct",
            "current_layer",
            "total_layers",
            "job_name",
            "error",
            "online",
            "connection_mode",
            "active_tray",
            "trays",
        }

    async def test_the_unverified_sensors_serialise_as_null_never_as_a_guess(
        self, ws: WsClient
    ) -> None:
        """Online, connection mode and active tray are **not discovered yet**.

        Their upstream `translation_key`s have to be read off a real instance before the
        constant is frozen (docs/13 — Traps): discovery matches on the key, so a guessed
        one discovers nothing and would report "the printer never said" forever without
        anybody suspecting it was our typo. Null is the honest answer in the meantime, and
        it is the gateway's standing policy for an undiscovered sensor.
        """
        payload = await ws.result_dict(PRINTER_STATE)

        assert payload["online"] is None
        assert payload["connection_mode"] is None
        assert payload["active_tray"] is None

    async def test_the_error_is_the_binary_state_with_no_invented_code(self, ws: WsClient) -> None:
        """The captured sensor reads `off` and exposes no `code` attribute.

        The two are separate facts: a printer can report an error without a code, and
        deriving one from the flag would put a searchable HMS quad on screen that matches
        nothing.
        """
        assert (await ws.result_dict(PRINTER_STATE))["error"] == {"active": False, "code": None}

    async def test_an_active_error_crosses_the_wire_as_a_decimal_string(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """HMS codes are 64-bit — 0x0300010000020001 already exceeds 2^53 — and a JSON
        number lands in JavaScript as a double, corrupting the code before the panel's
        `hms()` could format it. The panel owns the formatting; the wire owns the digits.
        """
        code = 216172782120927489
        harness.hass.states.by_entity_id[PRINT_ERROR] = State(PRINT_ERROR, "on", {"code": code})

        payload = await ws.result_dict(PRINTER_STATE)

        assert payload["error"] == {"active": True, "code": str(code)}
        assert int(cast(str, cast(dict[str, object], payload["error"])["code"])) == code

    async def test_the_trays_carry_the_sync_shape(self, ws: WsClient) -> None:
        """`trays` reuses the per-slot shape of `trays/sync`, computed read-only."""
        payload = await ws.result_dict(PRINTER_STATE)
        trays = cast("list[dict[str, object]]", payload["trays"])

        assert [tray["slot"] for tray in trays] == [1, 2, 3, 4]
        assert set(trays[0]) == {
            "slot",
            "status",
            "tag_uid",
            "name_hint",
            "material_hint",
            "colour_hint",
            "spool_id",
            "spool_name",
        }

    async def test_a_known_tag_reads_as_mounted_without_the_pass_having_run(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The outcome is *derived* by re-reading the ledger, exactly as the sync strip's
        is — but nothing detected it into place. Here the spool is already in the slot."""
        spool_id = await a_spool(harness.ledger, tag_uid=TRAY_1_TAG)
        await harness.ledger.use_cases.mount_spool.execute(spool_id, SlotIndex(1))

        payload = await ws.result_dict(PRINTER_STATE)
        trays = cast("list[dict[str, object]]", payload["trays"])

        assert trays[0]["status"] == "mounted"
        assert trays[0]["spool_id"] == spool_id


class TestUnavailableSensors:
    """A missing figure is not a figure of zero (docs/04 UC-04 step 2, applied to display)."""

    @pytest.fixture(autouse=True)
    def _wired(self, harness: Harness) -> None:
        wire(harness)

    async def test_one_unavailable_sensor_nulls_its_field_and_no_other(
        self, ws: WsClient, harness: Harness
    ) -> None:
        harness.hass.states.by_entity_id[PROGRESS] = State(PROGRESS, "unavailable", {})

        payload = await ws.result_dict(PRINTER_STATE)

        assert payload["progress_pct"] is None
        assert payload["current_layer"] == 71
        assert payload["status"] == "finish"

    async def test_a_missing_sensor_nulls_its_field(self, ws: WsClient, harness: Harness) -> None:
        del harness.hass.states.by_entity_id[STATUS]

        payload = await ws.result_dict(PRINTER_STATE)

        assert payload["status"] is None
        assert payload["total_layers"] == 209

    async def test_a_missing_error_sensor_is_null_not_a_healthy_printer(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """An absent sensor is a printer that did not say, which is a different fact from
        a printer reporting no error — so it is null, not `active: false`."""
        del harness.hass.states.by_entity_id[PRINT_ERROR]

        assert (await ws.result_dict(PRINTER_STATE))["error"] is None

    async def test_a_blank_job_name_falls_back_rather_than_rendering_empty(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The one field with a stated fallback (docs/14 §14.5): an empty string reads as
        a rendering bug rather than as an honest unknown."""
        harness.hass.states.by_entity_id[GCODE_FILE] = State(GCODE_FILE, "   ", {})

        assert (await ws.result_dict(PRINTER_STATE))["job_name"] == UNKNOWN_JOB_NAME

    async def test_zero_total_layers_reads_as_unknown_not_as_a_total(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Reported before a file is sliced. Zero total layers is not a total."""
        harness.hass.states.by_entity_id[TOTAL_LAYERS] = State(TOTAL_LAYERS, "0", {})

        assert (await ws.result_dict(PRINTER_STATE))["total_layers"] is None


class TestReadingWritesNothing:
    """The claim that separates this path from `trays/sync` (docs/14 §14.5, criterion 6)."""

    async def test_the_ledger_is_byte_identical_before_and_after(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Tray 1 carries a tag this spool owns, and the spool is deliberately left in
        storage. `trays/sync` would mount it; a glance must not.
        """
        wire(harness)
        spool_id = await a_spool(harness.ledger, tag_uid=TRAY_1_TAG)
        before = await _ledger_snapshot(harness)

        payload = await ws.result_dict(PRINTER_STATE)

        assert payload["dormant"] is False
        assert await _ledger_snapshot(harness) == before
        detail = await harness.ledger.use_cases.queries.detail(SpoolId(spool_id))
        assert not isinstance(detail.summary.spool.location, AmsSlot)

    async def test_an_unknown_tag_registers_nothing(self, ws: WsClient, harness: Harness) -> None:
        """Every tray in the fixture carries a tag no spool owns. Reading them writes no
        spool, no movement and no review — the tab reports, it does not repair."""
        wire(harness)
        before = await _ledger_snapshot(harness)

        await ws.result_dict(PRINTER_STATE)

        assert await _ledger_snapshot(harness) == before


async def _ledger_snapshot(harness: Harness) -> list[tuple[str, int, str]]:
    """Every spool with its movement count and location — the two things a sync moves."""
    return sorted(
        (summary.spool.id, summary.movement_count, str(summary.spool.location))
        for summary in await harness.ledger.use_cases.queries.overview()
    )
