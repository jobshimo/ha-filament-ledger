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

from custom_components.filament_ledger.domain.value.identifiers import SpoolId
from custom_components.filament_ledger.domain.value.location import AmsSlot
from custom_components.filament_ledger.domain.value.print_event import PrintEnded, PrintStarted
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
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

from ..application.conftest import A_PRINTER, ANOTHER_PRINTER, a_tray
from .conftest import Harness, a_spool, as_hass
from .test_bambu_gateway import (
    CURRENT_LAYER,
    GCODE_FILE,
    PRINT_ERROR,
    PROGRESS,
    REGISTRY_ROWS,
    REMAINING_TIME,
    SECOND_STATUS,
    SECOND_TRAYS,
    SECOND_WEIGHT,
    STATUS,
    TOTAL_LAYERS,
    TRAY_1_TAG,
    TRAY_ATTRIBUTES,
    plant_registry,
    print_sensor_state,
    second_printer_rows,
    tray_state,
)
from .test_websocket_api import WsClient

PRINTER_STATE = "filament_ledger/printer/state"


def machine(payload: dict[str, object], index: int = 0) -> dict[str, object]:
    """One machine's section of the reply (docs/14 §14.5, amended v2.0).

    Every figure that used to sit at the top level sits in one of these, because the tab
    renders a section per machine. A household with one printer gets a one-element list and
    a tab that reads exactly as it always has, which is what `index=0` is asserting for.
    """
    return cast("list[dict[str, object]]", payload["machines"])[index]


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
        # The real read model over the harness's real ledger, so the accumulated total is
        # a sum over rows a test wrote rather than over a number a fake agreed to return.
        queries=harness.ledger.use_cases.queries,
    )


class TestDormant:
    """Absence of a printer is a fact, and the tab says so instead of spinning."""

    async def test_without_a_wired_reader_the_reply_is_the_flag_and_nothing_else(
        self, ws: WsClient
    ) -> None:
        """The harness installs no printer, exactly like a test bench.

        The reply carries the flag and `tracking`, and nothing else: a hull of nulls
        beside it would invite the panel to render seven dashes for a machine that is not
        there. `tracking` is identity rather than measurement — a ledger with no printer
        still has a tray space to mount into, and it names nobody rather than guessing.
        """
        assert await ws.result_dict(PRINTER_STATE) == {
            "dormant": True,
            "tracking": {"printers": [], "ams": 1, "unnamed": 0},
        }

    async def test_ha_bambulab_absent_reports_dormant(self, ws: WsClient, harness: Harness) -> None:
        """An empty registry: the reader exists, discovery found nothing under it."""
        wire(harness, rows=[])

        assert await ws.result_dict(PRINTER_STATE) == {
            "dormant": True,
            "tracking": {"printers": [], "ams": 1, "unnamed": 0},
        }

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
        assert machine(payload)["status"] == "finish"
        assert machine(payload)["trays"] == []


class TestPopulated:
    """The captured A1, mid-glance."""

    @pytest.fixture(autouse=True)
    def _wired(self, harness: Harness) -> None:
        wire(harness)

    async def test_the_job_figures_are_the_sensors_verbatim(self, ws: WsClient) -> None:
        payload = await ws.result_dict(PRINTER_STATE)

        assert payload["dormant"] is False
        assert machine(payload)["status"] == "finish"
        assert machine(payload)["current_layer"] == 71
        assert machine(payload)["total_layers"] == 209
        assert machine(payload)["progress_pct"] == 34
        assert machine(payload)["job_name"] == "381189-Rails for a shelf v2.gcode"

    async def test_the_payload_carries_exactly_the_documented_keys(self, ws: WsClient) -> None:
        """A pin on the shape docs/14 §14.5 specifies, so a field cannot quietly vanish.

        The measured figures moved inside `machines` in v2.0; `observed_print_time` did not,
        because it is the ledger's sum across every machine and no machine's own.
        """
        payload = await ws.result_dict(PRINTER_STATE)

        assert set(payload) == {"dormant", "tracking", "machines", "observed_print_time"}
        assert set(machine(payload)) == {
            "printer",
            "status",
            "progress_pct",
            "current_layer",
            "total_layers",
            "job_name",
            "remaining_minutes",
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

        assert machine(payload)["online"] is None
        assert machine(payload)["connection_mode"] is None
        assert machine(payload)["active_tray"] is None

    async def test_the_error_is_the_binary_state_with_no_invented_code(self, ws: WsClient) -> None:
        """The captured sensor reads `off` and exposes no `code` attribute.

        The two are separate facts: a printer can report an error without a code, and
        deriving one from the flag would put a searchable HMS quad on screen that matches
        nothing.
        """
        payload = await ws.result_dict(PRINTER_STATE)

        assert machine(payload)["error"] == {"active": False, "code": None}

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

        error = cast("dict[str, object]", machine(payload)["error"])
        assert error == {"active": True, "code": str(code)}
        assert int(cast(str, error["code"])) == code

    async def test_the_trays_carry_the_sync_shape(self, ws: WsClient) -> None:
        """`trays` reuses the per-slot shape of `trays/sync`, computed read-only."""
        payload = await ws.result_dict(PRINTER_STATE)
        trays = cast("list[dict[str, object]]", machine(payload)["trays"])

        assert [tray["slot"] for tray in trays] == [1, 2, 3, 4]
        assert set(trays[0]) == {
            "printer",
            "ams",
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
        await harness.ledger.use_cases.mount_spool.execute(spool_id, a_tray(1))

        payload = await ws.result_dict(PRINTER_STATE)
        trays = cast("list[dict[str, object]]", machine(payload)["trays"])

        assert trays[0]["status"] == "mounted"
        assert trays[0]["spool_id"] == spool_id


class TestRemainingTime:
    """The one figure the tab shows only while something is printing (docs/14 §14.5)."""

    @pytest.fixture(autouse=True)
    def _wired(self, harness: Harness) -> None:
        wire(harness)

    async def test_a_running_job_reports_the_minutes_it_has_left(
        self, ws: WsClient, harness: Harness
    ) -> None:
        harness.hass.states.by_entity_id[REMAINING_TIME] = State(REMAINING_TIME, "97", {})

        assert machine(await ws.result_dict(PRINTER_STATE))["remaining_minutes"] == 97

    async def test_an_idle_printers_zero_is_no_job_rather_than_any_moment_now(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Upstream parks the sensor at zero between prints, so `0` reads identically on a
        machine that finished last Tuesday and one on its final layer. Rendering `0 min`
        would claim a print is about to end on a printer nobody is standing at; a dash
        claims nothing, and the cost is the last sub-minute of a real print."""
        harness.hass.states.by_entity_id[REMAINING_TIME] = State(REMAINING_TIME, "0", {})

        assert machine(await ws.result_dict(PRINTER_STATE))["remaining_minutes"] is None

    async def test_a_sensor_reporting_nothing_at_all_is_null_not_zero(self, ws: WsClient) -> None:
        """The base fixture never captured this sensor's state, which is the honest shape
        of a discovered entity that has not reported: null, exactly as for any other."""
        assert machine(await ws.result_dict(PRINTER_STATE))["remaining_minutes"] is None

    @pytest.mark.parametrize("reading", ["-5", "soon", "97.0", ""])
    async def test_a_reading_that_is_not_a_minute_count_is_dropped(
        self, ws: WsClient, harness: Harness, reading: str
    ) -> None:
        """Upstream noise, in every shape it has: a negative countdown, a word, a decimal
        the reader does not accept, and an empty string. Every one is an absent figure."""
        harness.hass.states.by_entity_id[REMAINING_TIME] = State(REMAINING_TIME, reading, {})

        assert machine(await ws.result_dict(PRINTER_STATE))["remaining_minutes"] is None


class TestObservedPrintTime:
    """This ledger's own hours, labelled as this ledger's own (docs/14 §14.5)."""

    @pytest.fixture(autouse=True)
    def _wired(self, harness: Harness) -> None:
        wire(harness)

    async def test_a_ledger_that_has_timed_nothing_reports_no_total(self, ws: WsClient) -> None:
        """Null rather than zero hours. A fresh install has not watched the printer for no
        time; it has not watched it at all, and the tab renders nothing rather than a
        card claiming a machine has never printed."""
        assert (await ws.result_dict(PRINTER_STATE))["observed_print_time"] is None

    async def test_the_total_carries_the_prints_and_the_day_it_starts_from(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The three figures travel together, because the total alone is an odometer.

        `ha-bambulab` reports no lifetime hours, so this can only ever be a sum over what
        this ledger recorded — and a number presented without the day it started counting
        would be read as the machine's own.
        """
        first = harness.ledger.clock.now()
        await _a_timed_job(harness, "one.3mf", minutes=90)
        harness.ledger.clock.advance(days=2)
        await _a_timed_job(harness, "two.3mf", minutes=30)

        observed = (await ws.result_dict(PRINTER_STATE))["observed_print_time"]

        assert observed == {
            "total_minutes": 120,
            "prints": 2,
            "since": first.isoformat(),
        }

    async def test_a_job_the_ledger_could_not_time_still_dates_the_total(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """`since` answers *when did this ledger start watching*, and a print whose
        duration could not be measured was still watched. Dating the total from the first
        *measurable* job would quietly shorten the window the figure covers."""
        first = harness.ledger.clock.now()
        await _a_timed_job(harness, "lost-its-start.3mf", minutes=0)
        harness.ledger.clock.advance(days=1)
        await _a_timed_job(harness, "real.3mf", minutes=45)

        observed = cast(
            "dict[str, object]", (await ws.result_dict(PRINTER_STATE))["observed_print_time"]
        )

        assert observed["prints"] == 1
        assert observed["total_minutes"] == 45
        assert observed["since"] == first.isoformat()


async def _a_timed_job(harness: Harness, name: str, *, minutes: int) -> None:
    """One print, started and ended through the real use case on the harness's clock.

    `minutes=0` writes the row a restart leaves behind — both timestamps the same moment —
    which is the row that must not be counted as a zero-length print.
    """
    await harness.ledger.use_cases.track_print_job.execute(
        PrintStarted(name=name, printer=A_PRINTER)
    )
    harness.ledger.clock.advance(minutes=minutes)
    await harness.ledger.use_cases.track_print_job.execute(
        PrintEnded(outcome=PrintJobState.FINISHED, name=name, printer=A_PRINTER)
    )


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

        assert machine(payload)["progress_pct"] is None
        assert machine(payload)["current_layer"] == 71
        assert machine(payload)["status"] == "finish"

    async def test_a_missing_sensor_nulls_its_field(self, ws: WsClient, harness: Harness) -> None:
        del harness.hass.states.by_entity_id[STATUS]

        payload = await ws.result_dict(PRINTER_STATE)

        assert machine(payload)["status"] is None
        assert machine(payload)["total_layers"] == 209

    async def test_a_missing_error_sensor_is_null_not_a_healthy_printer(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """An absent sensor is a printer that did not say, which is a different fact from
        a printer reporting no error — so it is null, not `active: false`."""
        del harness.hass.states.by_entity_id[PRINT_ERROR]

        assert machine(await ws.result_dict(PRINTER_STATE))["error"] is None

    async def test_a_blank_job_name_falls_back_rather_than_rendering_empty(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The one field with a stated fallback (docs/14 §14.5): an empty string reads as
        a rendering bug rather than as an honest unknown."""
        harness.hass.states.by_entity_id[GCODE_FILE] = State(GCODE_FILE, "   ", {})

        assert machine(await ws.result_dict(PRINTER_STATE))["job_name"] == UNKNOWN_JOB_NAME

    async def test_zero_total_layers_reads_as_unknown_not_as_a_total(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """Reported before a file is sliced. Zero total layers is not a total."""
        harness.hass.states.by_entity_id[TOTAL_LAYERS] = State(TOTAL_LAYERS, "0", {})

        assert machine(await ws.result_dict(PRINTER_STATE))["total_layers"] is None


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


class TestTracking:
    """Which machines this ledger follows, said where somebody will read it.

    v1.4 warned about a second printer into a log, then named it on a card as *found and not
    tracked*. v2.0 follows it instead, so what is left to report is the list itself — and a
    machine whose serial could not be read, which is the one case that still cannot be
    followed and the one worth a bug report.
    """

    async def test_the_followed_machine_is_named(self, ws: WsClient, harness: Harness) -> None:
        wire(harness)

        tracking = cast("dict[str, object]", (await ws.result_dict(PRINTER_STATE))["tracking"])

        assert tracking == {"printers": ["00000000TESTSER"], "ams": 1, "unnamed": 0}

    async def test_a_machine_with_no_readable_serial_is_counted_not_followed(
        self, ws: WsClient, harness: Harness
    ) -> None:
        """The one thing left that this version passes over, and it says so rather than
        leaving it in a log nobody looks at."""
        nameless = [
            {
                "entity_id": f"sensor.p1s_nameless_{key}",
                "platform": "bambu_lab",
                "unique_id": f"_{key}",
                "translation_key": key,
                "device_id": "00000000000000000000000000zzzzprn",
            }
            for key in ("print_weight", "print_status")
        ]
        wire(harness, rows=REGISTRY_ROWS + nameless)

        tracking = cast("dict[str, object]", (await ws.result_dict(PRINTER_STATE))["tracking"])

        assert tracking == {"printers": ["00000000TESTSER"], "ams": 1, "unnamed": 1}


class TestTwoMachines:
    """The tab with a second printer, which is what the release is for."""

    @pytest.fixture(autouse=True)
    def _wired(self, harness: Harness) -> None:
        wire(harness, rows=REGISTRY_ROWS + second_printer_rows())
        harness.hass.states.by_entity_id[SECOND_STATUS] = State(SECOND_STATUS, "idle", {})
        harness.hass.states.by_entity_id[SECOND_WEIGHT] = State(SECOND_WEIGHT, "0", {})
        for entity_id in SECOND_TRAYS:
            harness.hass.states.by_entity_id[entity_id] = tray_state(
                entity_id,
                {
                    **TRAY_ATTRIBUTES["sensor.a1_00000000testser_ams_1_bandeja_1"],
                    "empty": True,
                    "tag_uid": None,
                },
            )

    async def test_both_machines_are_tracked_in_serial_order(self, ws: WsClient) -> None:
        payload = await ws.result_dict(PRINTER_STATE)

        assert payload["tracking"] == {
            "printers": [ANOTHER_PRINTER.value, A_PRINTER.value],
            "ams": 1,
            "unnamed": 0,
        }

    async def test_each_machine_gets_its_own_section_with_its_own_figures(
        self, ws: WsClient
    ) -> None:
        """A section each, not a picker: the person this tab is for is standing at one of
        their printers, and a selector would make them identify it by serial first."""
        payload = await ws.result_dict(PRINTER_STATE)

        assert [
            entry["printer"] for entry in cast("list[dict[str, object]]", payload["machines"])
        ] == [
            ANOTHER_PRINTER.value,
            A_PRINTER.value,
        ]
        assert machine(payload, 0)["status"] == "idle"
        assert machine(payload, 1)["status"] == "finish"

    async def test_a_machines_trays_are_its_own(self, ws: WsClient) -> None:
        """Matching on the slot number alone would put the other machine's tray 3 under
        this one, which is the confusion this release exists to end."""
        payload = await ws.result_dict(PRINTER_STATE)

        for index, serial in enumerate((ANOTHER_PRINTER.value, A_PRINTER.value)):
            trays = cast("list[dict[str, object]]", machine(payload, index)["trays"])
            assert {tray["printer"] for tray in trays} == {serial}
            assert [tray["slot"] for tray in trays] == [1, 2, 3, 4]

    async def test_one_total_covers_every_machine(self, ws: WsClient, harness: Harness) -> None:
        """`observed_print_time` stays outside the sections. The rows written before this
        ledger recorded which machine ran a job name none, so a per-machine split would file
        real hours under a heading nobody could read."""
        await _a_timed_job(harness, "one.3mf", minutes=90)

        payload = await ws.result_dict(PRINTER_STATE)

        assert cast("dict[str, object]", payload["observed_print_time"])["total_minutes"] == 90


async def _ledger_snapshot(harness: Harness) -> list[tuple[str, int, str]]:
    """Every spool with its movement count and location — the two things a sync moves."""
    return sorted(
        (summary.spool.id, summary.movement_count, str(summary.spool.location))
        for summary in await harness.ledger.use_cases.queries.overview()
    )
