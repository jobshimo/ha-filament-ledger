"""The `ha-bambulab` boundary, driven with payloads captured from the reference A1.

Every registry row and attribute dictionary here comes from the owner's live instance
(docs/09 §9.4, docs/12-field-notes.md) — serials anonymised, every other byte faithful.
That instance runs Spanish, which is the point: the entity ids are `…_bandeja_1`, and
discovery must resolve through upstream's `unique_id`s or it breaks for every user not
running the developer's language (docs/05 §5.8).

The state-change plumbing is real. `async_track_state_change_event` registers on the fake
bus exactly as in production, so a `state_changed` event fired here runs Home Assistant's
own tracker, filter and dispatch before the gateway sees anything. Below the gateway, the
end-to-end tests run the same real ledger the application suite uses.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er

from custom_components.filament_ledger import async_unload_entry
from custom_components.filament_ledger.domain.event import (
    ReviewOpened,
    SpoolMounted,
    UnknownSpoolDetected,
)
from custom_components.filament_ledger.domain.model.pending_review import ReviewCharge
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    UNIDENTIFIED_PRINTER,
    AmsIndex,
    PrinterSerial,
    ReelUid,
    SpoolId,
    TagUid,
)
from custom_components.filament_ledger.domain.value.location import AmsSlot, Location
from custom_components.filament_ledger.domain.value.percentage import Percentage
from custom_components.filament_ledger.domain.value.print_event import (
    PrintEnded,
    PrintEvent,
    PrintPlanObserved,
    PrintStarted,
)
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.domain.value.review import ReviewReason
from custom_components.filament_ledger.domain.value.tray_reading import TrayReading
from custom_components.filament_ledger.infrastructure.ha.bambu_gateway import (
    UNKNOWN_JOB_NAME,
    BambuLabGateway,
)
from custom_components.filament_ledger.infrastructure.ha.job_sync import JobSync
from custom_components.filament_ledger.infrastructure.ha.runtime import LedgerConfigEntry
from custom_components.filament_ledger.infrastructure.persistence.print_job_repository import (
    SqlitePrintJobRepository,
)
from custom_components.filament_ledger.infrastructure.persistence.review_repository import (
    SqliteReviewRepository,
)

from ..application.conftest import A_PRINTER, ANOTHER_PRINTER, Ledger, a_tray
from .conftest import FakeHass, Harness, a_spool, as_hass

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bambu"

REGISTRY_ROWS: list[dict[str, str]] = json.loads(
    (FIXTURES / "entity_registry.json").read_text(encoding="utf-8")
)
TRAY_ATTRIBUTES: dict[str, dict[str, object]] = json.loads(
    (FIXTURES / "tray_attributes.json").read_text(encoding="utf-8")
)
PRINT_SENSORS: dict[str, dict[str, object]] = json.loads(
    (FIXTURES / "print_sensors.json").read_text(encoding="utf-8")
)
# The weight sensor as captured at the moment a print finished — the shape that closed Q4
# (docs/12-field-notes.md, 2026-08-03): per-tray attributes populated, `AMS 1 Tray n` keys.
PRINT_SENSORS_FINISHED: dict[str, dict[str, object]] = json.loads(
    (FIXTURES / "print_sensors_finished.json").read_text(encoding="utf-8")
)

TRAY_1 = "sensor.a1_00000000testser_ams_1_bandeja_1"
TRAY_2 = "sensor.a1_00000000testser_ams_1_bandeja_2"
TRAY_4 = "sensor.a1_00000000testser_ams_1_bandeja_4"

TRAY_1_TAG = TagUid("3C45C3DB00000100")
TRAY_2_TAG = TagUid("3CDDA20200000100")
TRAY_4_TAG = TagUid("4289A97100000100")

# The reel each tray's fixture names. Distinct from the tags above on purpose, because that
# is the fact the whole identity model turns on: the chip says which side the AMS reached,
# the reel says which reel it is (docs/12-field-notes.md). Tray 3 carries the all-zero
# sentinel in the fixture and therefore has no reel here — the same absence as its tag.
TRAY_1_REEL = ReelUid("00000000000000000000000000TEST01")
TRAY_2_REEL = ReelUid("00000000000000000000000000TEST02")
TRAY_4_REEL = ReelUid("00000000000000000000000000TEST04")

WEIGHT = "sensor.a1_00000000testser_peso_de_la_impresion"
STATUS = "sensor.a1_00000000testser_estado_de_la_impresion"
CURRENT_LAYER = "sensor.a1_00000000testser_capa_actual"
TOTAL_LAYERS = "sensor.a1_00000000testser_cantidad_total_de_capas"
PROGRESS = "sensor.a1_00000000testser_progreso_de_la_impresion"
GCODE_FILE = "sensor.a1_00000000testser_archivo_gcode_descargado"
PRINT_ERROR = "binary_sensor.a1_00000000testser_error_de_la_impresion"

# The second lifecycle level, and the more specific of the two. `stage` speaks upstream's
# `printing` / `idle` / `paused_*` vocabulary where `print_status` speaks `gcode_state`'s,
# and the two go unavailable at different moments — which is the whole reason both are
# watched. Stateless in the base harness for the same reason the job-time sensors are: it
# was never in the captured `print_sensors.json`, so every test that means it plants it.
STAGE = "sensor.a1_00000000testser_estado_actual"

# The job-name fallback: `gcode_file_downloaded` speaks only at the moment a file is
# downloaded and stays `unavailable` across a Home Assistant restart, while upstream
# restores this sensor on reconnect. Deliberately absent from `print_sensors.json` for
# the v1.4 trio's reason below — stateless in the base harness, planted by the tests
# that mean it — and the value planted is the shape the reference instance publishes.
GCODE_NAME = "sensor.a1_00000000testser_nombre_del_gcode"
GCODE_NAME_VALUE = "80% + parts, ironning, 0.2mm layer,2 walls,8% infill.3mf"

# The three job-time sensors frozen in v1.4. Their `translation_key`s were read off the
# reference instance's registry before the constant was frozen (docs/13 — Traps); the
# localised entity ids follow the pattern every other row on that instance shows, and
# nothing resolves by them — discovery matches platform and key, which is the whole point.
#
# **Deliberately absent from `print_sensors.json`.** That fixture is what the reference
# instance's job sensors *held* at the moment of capture, and these three were never
# captured. Leaving them stateless makes the base harness exercise the honest path — a
# discovered sensor reporting nothing — and every test that wants a figure plants the one
# it means, which is also the only way a naive or unparseable reading can be written down.
REMAINING_TIME = "sensor.a1_00000000testser_tiempo_restante"
START_TIME = "sensor.a1_00000000testser_hora_de_inicio"
END_TIME = "sensor.a1_00000000testser_hora_de_finalizacion"

# The device ids the fixture registry carries: job events name the printer; the trays
# hang off the AMS device, which fires no job events.
PRINTER_DEVICE = "00000000000000000000000000testprn"
AMS_DEVICE = "00000000000000000000000000testams"

JOB_NAME = "381189-Rails for a shelf v2.gcode"

# A second machine, in the shapes the captured registry shows and nowhere else. **It is not
# added to the frozen fixture**: that file is what one real instance held, and inventing rows
# into it would turn evidence into wishful thinking. Built here instead, from the same
# `<serial>_<key>` job-sensor form and the same `…_AMS_<ams serial>_tray_<n>` tray form the
# capture carries, with the P1S model prefix a different machine would plausibly write.
SECOND_DEVICE = "00000000000000000000000000zzzzprn"
SECOND_AMS_DEVICE = "00000000000000000000000000zzzzams"

# Localised entity ids, like the capture's, and for its reason: nothing resolves by them.
# A second machine whose ids read English would quietly stop proving that.
SECOND_SENSORS = {
    "print_weight": "sensor.p1s_00000000otherser_peso_de_la_impresion",
    "print_status": "sensor.p1s_00000000otherser_estado_de_la_impresion",
    "current_layer": "sensor.p1s_00000000otherser_capa_actual",
    "total_layers": "sensor.p1s_00000000otherser_cantidad_total_de_capas",
}
SECOND_WEIGHT = SECOND_SENSORS["print_weight"]
SECOND_STATUS = SECOND_SENSORS["print_status"]
SECOND_TRAYS = [f"sensor.p1s_00000000otherser_ams_1_bandeja_{n}" for n in range(1, 5)]


def second_printer_rows() -> list[dict[str, str]]:
    """The second machine's registry rows — job sensors on its printer, trays on its AMS."""
    name = ANOTHER_PRINTER.value
    job = [
        {
            "entity_id": entity_id,
            "platform": "bambu_lab",
            "unique_id": f"{name}_{key}",
            "translation_key": key,
            "device_id": SECOND_DEVICE,
        }
        for key, entity_id in SECOND_SENSORS.items()
    ]
    trays = [
        {
            "entity_id": entity_id,
            "platform": "bambu_lab",
            "unique_id": f"P1S_{name}_AMS_00000000ZZZZAMS_tray_{n}",
            "translation_key": "tray",
            "device_id": SECOND_AMS_DEVICE,
        }
        for n, entity_id in enumerate(SECOND_TRAYS, start=1)
    ]
    return job + trays


def two_printer_hass(extra: list[dict[str, str]] | None = None) -> FakeHass:
    """The reference instance with a second machine beside it, both fully stated.

    The second machine's trays are reported empty, which is what makes the two sets tell
    each other apart in an assertion without a second attribute capture nobody has taken.
    """
    hass = bambu_hass(rows=REGISTRY_ROWS + second_printer_rows() + (extra or []))
    hass.states.by_entity_id[SECOND_WEIGHT] = State(SECOND_WEIGHT, "12.5", {})
    hass.states.by_entity_id[SECOND_STATUS] = State(SECOND_STATUS, "idle", {})
    for entity_id in SECOND_TRAYS:
        empty = {key: value for key, value in TRAY_ATTRIBUTES[TRAY_1].items() if key != "tag_uid"}
        hass.states.by_entity_id[entity_id] = tray_state(entity_id, {**empty, "empty": True})
    return hass


@dataclass(frozen=True)
class FakeRegistryEntry:
    """The slice of `er.RegistryEntry` that discovery reads."""

    entity_id: str
    platform: str
    unique_id: str
    translation_key: str
    device_id: str | None = None


class FakeEntityRegistry:
    """`er.async_get` returns whatever `hass.data[er.DATA_REGISTRY]` holds; planting this
    there is the same seam Home Assistant's own singleton uses."""

    def __init__(self, entries: list[FakeRegistryEntry]) -> None:
        self.entities = {entry.entity_id: entry for entry in entries}


@dataclass
class RecordingListener:
    received: list[TrayReading] = field(default_factory=list)

    async def __call__(self, reading: TrayReading) -> None:
        self.received.append(reading)


@dataclass
class RecordingPrintListener:
    received: list[PrintEvent] = field(default_factory=list)

    async def __call__(self, event: PrintEvent) -> None:
        self.received.append(event)

    @property
    def lifecycle(self) -> list[PrintEvent]:
        """Starts and endings only — the two moments a job's *row* is created or closed.

        Since 2.6.1 a third event travels the same channel: every per-tray reading the
        machine publishes mid-print is forwarded so the row carries figures before an
        ending that may never come. It is not part of the lifecycle and the scenarios that
        pin the lifecycle should not have to count it, so they read this instead.
        `TestThePlanIsPersistedWhileTheJobRuns` is where the observations themselves are
        asserted.
        """
        return [e for e in self.received if not isinstance(e, PrintPlanObserved)]


def plant_registry(hass: FakeHass, rows: list[dict[str, str]]) -> None:
    registry = FakeEntityRegistry([FakeRegistryEntry(**row) for row in rows])
    hass.data[er.DATA_REGISTRY] = cast(er.EntityRegistry, registry)


def tray_state(entity_id: str, attributes: dict[str, object] | None = None) -> State:
    """A tray sensor state. Only availability is read off the state string itself, so the
    filament name stands in for the uncaptured native value."""
    payload = TRAY_ATTRIBUTES[entity_id] if attributes is None else attributes
    return State(entity_id, str(payload.get("name", "loaded")), payload)


def print_sensor_state(
    entity_id: str, state: str | None = None, attributes: dict[str, object] | None = None
) -> State:
    """One of the printer's job sensors, defaulting to the captured fixture shape."""
    shape = PRINT_SENSORS[entity_id]
    return State(
        entity_id,
        str(shape["state"]) if state is None else state,
        cast("dict[str, object]", shape["attributes"]) if attributes is None else attributes,
    )


def bambu_hass(rows: list[dict[str, str]] | None = None) -> FakeHass:
    """A hass holding the reference instance: registry rows planted, states loaded."""
    hass = FakeHass()
    plant_registry(hass, REGISTRY_ROWS if rows is None else rows)
    for entity_id in TRAY_ATTRIBUTES:
        hass.states.by_entity_id[entity_id] = tray_state(entity_id)
    for entity_id in PRINT_SENSORS:
        hass.states.by_entity_id[entity_id] = print_sensor_state(entity_id)
    return hass


def fire_tray_change(hass: FakeHass, entity_id: str, new_state: State | None) -> None:
    """What the state machine does on a change: record the new state, then fire the event."""
    old_state = hass.states.get(entity_id)
    if new_state is not None:
        hass.states.by_entity_id[entity_id] = new_state
    hass.bus.async_fire(
        "state_changed",
        {"entity_id": entity_id, "old_state": old_state, "new_state": new_state},
    )


#: Shape B of a flicker pair: the same sensor, the same state value, and no tray key at
#: all. Captured from the two recorder rows of one republish burst, one to four seconds
#: apart (docs/12-field-notes.md, 2026-08-08) — the shape a finish landing beside it used
#: to read as "this print consumed nothing".
WEIGHT_WITHOUT_BREAKDOWN: dict[str, object] = {
    "state_class": "total",
    "unit_of_measurement": "g",
    "device_class": "weight",
    "friendly_name": "A1_00000000TESTSER Peso de la impresion",
}


def fire_weight_change(
    hass: FakeHass,
    attributes: dict[str, object],
    entity_id: str = WEIGHT,
    state: str = "40.51",
) -> None:
    """One republish of a weight sensor, the way upstream does it — in occasional
    bursts through a print, each burst a pair of opposite shapes.

    The state value is held constant on purpose: what alternates between the two shapes
    is the *attributes*, and a gateway watching the state alone would see nothing happen.
    """
    old_state = hass.states.get(entity_id)
    new_state = State(entity_id, state, attributes)
    hass.states.by_entity_id[entity_id] = new_state
    hass.bus.async_fire(
        "state_changed",
        {"entity_id": entity_id, "old_state": old_state, "new_state": new_state},
    )


def fire_status_change(hass: FakeHass, state: str, entity_id: str = STATUS) -> None:
    """One move of the status sensor, the way the state machine makes it.

    The value is the whole payload here: `gcode_state` is a bare string and the ending is
    read off *arriving* at it, so the attributes never enter into it.
    """
    old_state = hass.states.get(entity_id)
    new_state = State(entity_id, state, {})
    hass.states.by_entity_id[entity_id] = new_state
    hass.bus.async_fire(
        "state_changed",
        {"entity_id": entity_id, "old_state": old_state, "new_state": new_state},
    )


def fire_job_event(hass: FakeHass, event_type: str, device_id: str = PRINTER_DEVICE) -> None:
    """What upstream fires: `bambu_lab_event` naming the device, the entry, the type —
    the verified v2.2.22 payload shape."""
    hass.bus.async_fire(
        "bambu_lab_event", {"device_id": device_id, "name": "A1", "type": event_type}
    )


async def located(ledger: Ledger, spool_id: SpoolId) -> Location:
    return (await ledger.use_cases.queries.detail(spool_id)).summary.spool.location


class TestDiscovery:
    async def test_trays_resolve_through_unique_ids_not_localised_names(self) -> None:
        """Four Spanish `bandeja` entities, four slots found — nothing matched a name."""
        readings = await BambuLabGateway(as_hass(bambu_hass())).current_trays()

        assert sorted(readings) == [a_tray(1), a_tray(2), a_tray(3), a_tray(4)]

    async def test_non_tray_bambu_entities_are_not_trays(self) -> None:
        """The registry rows include the printer's own sensors (`print_weight`,
        `print_status`, `active_tray`, `online`); `translation_key == "tray"` is the
        discriminator, and exactly four survive it."""
        readings = await BambuLabGateway(as_hass(bambu_hass())).current_trays()

        assert len(readings) == 4

    async def test_the_job_time_sensors_resolve_by_key_on_the_spanish_instance(self) -> None:
        """The three keys frozen in v1.4, discovered the only way anything here is.

        The entity ids read `tiempo_restante` and `hora_de_inicio`; nothing in the gateway
        contains either string. A key that resolved nothing would leave these figures null
        forever and look exactly like a printer that never reported them, which is why
        every key is read off a real registry before it is frozen (docs/13 — Traps) — and
        why this test asserts on a figure rather than on the constant.
        """
        hass = bambu_hass()
        hass.states.by_entity_id[REMAINING_TIME] = State(REMAINING_TIME, "97", {})

        gateway = BambuLabGateway(as_hass(hass))

        assert gateway.current_job_status(A_PRINTER).remaining_minutes == 97

    async def test_the_gcode_name_sensor_resolves_by_key_on_the_spanish_instance(self) -> None:
        """The job-name fallback, discovered the only way anything here is.

        The entity id reads `nombre_del_gcode` and nothing in the gateway contains that
        string. The downloaded-file sensor is planted `unavailable` — the restart shape —
        because the fallback answering is the only figure that proves the key resolved:
        an unmatched key would leave the name reading unknown forever and look exactly
        like a printer that never reported it.
        """
        hass = bambu_hass()
        hass.states.by_entity_id[GCODE_FILE] = State(GCODE_FILE, STATE_UNAVAILABLE, {})
        hass.states.by_entity_id[GCODE_NAME] = State(GCODE_NAME, GCODE_NAME_VALUE, {})

        gateway = BambuLabGateway(as_hass(hass))

        assert gateway.current_job_status(A_PRINTER).name == GCODE_NAME_VALUE

    async def test_the_first_ams_wins_when_the_registry_holds_two(self) -> None:
        """v1 tracks a single printer. Only the first unit's states exist here, so four
        readings prove the second group was never consulted."""
        second_unit = [
            {
                "entity_id": f"sensor.a1_00000000testser_ams_2_bandeja_{n}",
                "platform": "bambu_lab",
                "unique_id": f"A1_00000000TESTSER_AMS_00000000ZZZZAMS_tray_{n}",
                "translation_key": "tray",
            }
            for n in range(1, 5)
        ]

        readings = await BambuLabGateway(
            as_hass(bambu_hass(rows=REGISTRY_ROWS + second_unit))
        ).current_trays()

        assert len(readings) == 4
        assert readings[a_tray(1)].colour == Colour(0x5E, 0x43, 0xB7, 0xFF)

    async def test_the_serial_is_read_off_the_job_sensors_unique_ids(self) -> None:
        """The stable identity a tray reference needs, from evidence already frozen here.

        Upstream writes each job sensor's `unique_id` as `<serial>_<translation_key>`, so
        removing the key that matched leaves the serial. Read from the registry rather than
        from a `translation_key` nobody has confirmed on a real instance — the rule
        `FUTURE_PRINT_SENSOR_KEYS` exists to state.
        """
        gateway = BambuLabGateway(as_hass(bambu_hass()))

        assert gateway.printers == (PrinterSerial("00000000TESTSER"),)
        assert gateway.default_printer == PrinterSerial("00000000TESTSER")

    async def test_every_tray_is_named_after_the_printer_that_holds_it(self) -> None:
        """The two halves of discovery agreeing: the trays carry the serial the job
        sensors gave, so a reading and a spool's location name the same tray."""
        readings = await BambuLabGateway(as_hass(bambu_hass())).current_trays()

        assert {tray.printer for tray in readings} == {PrinterSerial("00000000TESTSER")}
        assert {tray.ams for tray in readings} == {AmsIndex(1)}

    async def test_trays_fall_back_to_the_unidentified_printer_when_no_job_sensor_resolved(
        self,
    ) -> None:
        """An AMS with no printer sensors beside it: the trays are still this ledger's
        trays, and the sentinel is exactly what a ledger with no discoverable printer
        carries in its rows. One tray space, not two."""
        trays_only = [row for row in REGISTRY_ROWS if row["translation_key"] == "tray"]
        gateway = BambuLabGateway(as_hass(bambu_hass(rows=trays_only)))

        assert gateway.printers == (UNIDENTIFIED_PRINTER,)
        assert gateway.default_printer == UNIDENTIFIED_PRINTER
        assert {tray.printer for tray in await gateway.current_trays()} == {UNIDENTIFIED_PRINTER}

    async def test_every_printer_in_the_registry_is_followed(self) -> None:
        """v2.0's whole point, and the sentence v1.4's ignored-serials card was standing in
        for: both machines resolve, in serial order, and neither is passed over."""
        gateway = BambuLabGateway(as_hass(two_printer_hass()))

        assert gateway.printers == (ANOTHER_PRINTER, A_PRINTER)
        assert gateway.unnamed_printers == 0

    async def test_each_printer_keeps_its_own_trays_and_they_share_one_mapping(self) -> None:
        """Eight trays, four per machine, in one flat mapping — and no collision, because
        each is keyed by a reference that names its printer."""
        readings = await BambuLabGateway(as_hass(two_printer_hass())).current_trays()

        assert len(readings) == 8
        assert {tray.printer for tray in readings} == {A_PRINTER, ANOTHER_PRINTER}
        assert a_tray(1, printer=A_PRINTER) in readings
        assert a_tray(1, printer=ANOTHER_PRINTER) in readings

    async def test_an_ams_is_attributed_by_the_serial_its_unique_id_mentions(self) -> None:
        """The rule, checked where it decides something.

        The tray `unique_id` reads `A1_00000000TESTSER_AMS_00000000TESTAMS_tray_1` — the
        printer's serial is in there behind a model prefix nobody has a boundary for. This
        does not parse it: it asks whether a serial the *job sensors* resolved appears in
        the string, so a swapped order would put the wrong machine's trays under a printer
        and this assertion would fail rather than pass quietly.
        """
        readings = await BambuLabGateway(as_hass(two_printer_hass())).current_trays()

        here = readings[a_tray(1, printer=A_PRINTER)]
        there = readings[a_tray(1, printer=ANOTHER_PRINTER)]
        # The captured machine's tray 1 holds the purple reel; the second machine's is empty.
        assert here.colour == Colour(0x5E, 0x43, 0xB7, 0xFF)
        assert there.empty is True

    async def test_an_ams_naming_no_discovered_printer_is_dropped_not_guessed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """With several machines an unattributable AMS has no least-wrong owner: putting
        its trays on either printer would mount somebody's spool into a machine it is not
        in. Dropped, and said out loud."""
        orphan = [
            {
                "entity_id": f"sensor.x1c_00000000thirdsr_ams_1_bandeja_{n}",
                "platform": "bambu_lab",
                "unique_id": f"X1C_00000000THIRDSR_AMS_00000000QQQQAMS_tray_{n}",
                "translation_key": "tray",
                "device_id": "00000000000000000000000000qqqqams",
            }
            for n in range(1, 5)
        ]
        hass = two_printer_hass(extra=orphan)
        for row in orphan:
            entity_id = row["entity_id"]
            hass.states.by_entity_id[entity_id] = tray_state(entity_id, TRAY_ATTRIBUTES[TRAY_1])

        with caplog.at_level(logging.WARNING):
            readings = await BambuLabGateway(as_hass(hass)).current_trays()

        assert {tray.printer for tray in readings} == {A_PRINTER, ANOTHER_PRINTER}
        assert "names none of the discovered printers" in caplog.text

    async def test_one_printer_takes_every_ams_without_consulting_the_string(self) -> None:
        """A household with one machine must not lose its trays to an upstream that
        reshapes tray `unique_id`s: there is nothing else the AMS could belong to."""
        reshaped = [
            {**row, "unique_id": f"whatever_upstream_writes_now_tray_{n}"}
            for n, row in enumerate(
                (row for row in REGISTRY_ROWS if row["translation_key"] == "tray"), start=1
            )
        ]
        rows = [row for row in REGISTRY_ROWS if row["translation_key"] != "tray"] + reshaped

        readings = await BambuLabGateway(as_hass(bambu_hass(rows=rows))).current_trays()

        assert {tray.printer for tray in readings} == {A_PRINTER}

    async def test_a_second_machine_with_no_readable_serial_is_not_followed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The sentinel names *the one machine this ledger has always followed*, so handing
        it to one of two live machines would merge two tray spaces into one and collide slot
        for slot. There is nothing else to call it — a device id is not a name a printer
        answers to — so it is counted where the Printer tab can say so."""
        nameless = [
            {
                "entity_id": f"sensor.p1s_nameless_{key}",
                "platform": "bambu_lab",
                "unique_id": f"_{key}",
                "translation_key": key,
                "device_id": SECOND_DEVICE,
            }
            for key in ("print_weight", "print_status")
        ]

        with caplog.at_level(logging.WARNING):
            gateway = BambuLabGateway(as_hass(bambu_hass(rows=REGISTRY_ROWS + nameless)))

        assert gateway.printers == (A_PRINTER,)
        assert gateway.unnamed_printers == 1
        assert "no readable serial" in caplog.text

    async def test_two_machines_leave_no_default_tray_space(self) -> None:
        """A caller naming only a slot meant the one machine in the house. With two, the
        sentence is ambiguous and the gateway declines to resolve it — the runtime turns
        that `None` into a message rather than a mount somewhere plausible."""
        assert BambuLabGateway(as_hass(two_printer_hass())).default_printer is None


class TestRemainingTime:
    """The `remaining_time` reader converting by the unit the sensor declares.

    The reference instance publishes decimal hours — measured 2026-08-09: state `"6.35"`,
    `unit_of_measurement: "h"` — a shape the original whole-minutes reader could never
    parse, so the Printer tab's remaining-time field had shown a dash on every real print.
    """

    def reading(self, state: str, attributes: dict[str, object] | None = None) -> int | None:
        hass = bambu_hass()
        hass.states.by_entity_id[REMAINING_TIME] = State(REMAINING_TIME, state, attributes or {})
        return BambuLabGateway(as_hass(hass)).current_job_status(A_PRINTER).remaining_minutes

    async def test_decimal_hours_convert_to_whole_minutes(self) -> None:
        """The reference machine's own reading: 6.35 hours is 381 minutes, not a dash."""
        assert self.reading("6.35", {"unit_of_measurement": "h"}) == 381

    async def test_a_sensor_speaking_minutes_passes_through_unconverted(self) -> None:
        assert self.reading("383", {"unit_of_measurement": "min"}) == 383

    async def test_a_sensor_speaking_seconds_rounds_to_whole_minutes(self) -> None:
        assert self.reading("300", {"unit_of_measurement": "s"}) == 5

    async def test_no_declared_unit_is_read_as_minutes_the_way_it_always_was(self) -> None:
        """The legacy shape keeps its legacy reading: minutes were the reader's original
        assumption, and an undeclared unit falls back to it rather than to a guess."""
        assert self.reading("97") == 97

    async def test_zero_hours_is_still_no_job(self) -> None:
        """The parked-at-zero rule survives conversion: `0.0 h` rounds to zero minutes,
        and zero is read as an idle machine, never as a countdown's last moment."""
        assert self.reading("0.0", {"unit_of_measurement": "h"}) is None

    async def test_an_unreadable_duration_is_dropped_not_invented(self) -> None:
        assert self.reading("soon") is None

    @pytest.mark.parametrize(
        ("state", "unit", "minutes"),
        [
            pytest.param("2", "d", 2880, id="days"),
            pytest.param("1.5", "hr", 90, id="hr-spelling"),
            pytest.param("2", "hours", 120, id="hours-spelling"),
            pytest.param("120000", "ms", 2, id="milliseconds"),
            pytest.param("120000000", "µs", 2, id="microseconds"),
        ],
    )
    async def test_every_duration_unit_home_assistant_defines_converts(
        self, state: str, unit: str, minutes: int
    ) -> None:
        """The whole `UnitOfTime` table, not just the shapes the reference machine has
        shown: upstream is free to reshape this sensor, and every unit it could legally
        declare must land on the same whole-minute figure."""
        assert self.reading(state, {"unit_of_measurement": unit}) == minutes

    async def test_a_units_spelling_is_normalised_before_it_is_looked_up(self) -> None:
        """Whitespace and case are presentation, not meaning: ` H ` is still hours."""
        assert self.reading("6.35", {"unit_of_measurement": " H "}) == 381

    async def test_a_declared_unit_this_reader_does_not_know_is_dropped_not_guessed(
        self,
    ) -> None:
        """A declared unit outside the table converts nothing: a figure read by a guessed
        unit is confidently wrong where a dash is merely silent — under-claim, the
        module's standing rule. Only a sensor declaring *nothing* keeps the legacy
        minutes reading."""
        assert self.reading("6.35", {"unit_of_measurement": "fortnights"}) is None

    @pytest.mark.parametrize(
        "state",
        [
            pytest.param("inf", id="infinity"),
            pytest.param("-inf", id="negative-infinity"),
            pytest.param("nan", id="not-a-number"),
        ],
    )
    async def test_a_figure_no_countdown_can_hold_is_skipped_not_raised(self, state: str) -> None:
        """All three parse as floats, so `float` waves them through and `round` refuses —
        the same trap `Grams.of` guards in the weight reader, and the guard is the
        difference between an honest dash and an exception."""
        assert self.reading(state, {"unit_of_measurement": "min"}) is None

    async def test_a_countdown_longer_than_a_year_is_noise_not_a_figure(self) -> None:
        """`1e30` rounds to a perfectly finite integer, so only a plausibility line
        catches it: no print outlives a year, and past that line the honest reading is
        that the sensor said nothing."""
        assert self.reading("1e30", {"unit_of_measurement": "min"}) is None


class TestJobName:
    """The job-name reader falling back from the downloaded file to the gcode's name.

    `gcode_file_downloaded` publishes only when the printer downloads a file and goes
    `unavailable` across a Home Assistant restart, staying dead until the *next*
    download — so mid-print after a restart, the Printer tab and every row opened in
    that window read "unknown print" off a machine verifiably printing something. The
    `gcode_name` sensor is restored on reconnect and carries the job's name through
    exactly that gap.
    """

    def reading(self, downloaded: str, gcode_name: str | None) -> str:
        """The name `current_job_status` reads with both sensors planted as given.

        `None` for the gcode-name sensor leaves it stateless — the shape of a machine
        that never reported it — while the downloaded-file sensor is always planted
        explicitly, because every scenario here is about what it says or fails to say.
        """
        hass = bambu_hass()
        hass.states.by_entity_id[GCODE_FILE] = State(GCODE_FILE, downloaded, {})
        if gcode_name is not None:
            hass.states.by_entity_id[GCODE_NAME] = State(GCODE_NAME, gcode_name, {})
        return BambuLabGateway(as_hass(hass)).current_job_status(A_PRINTER).name

    async def test_the_gcode_name_speaks_when_the_downloaded_file_is_dead(self) -> None:
        """The restart shape: downloaded-file `unavailable`, gcode-name restored."""
        assert self.reading(STATE_UNAVAILABLE, GCODE_NAME_VALUE) == GCODE_NAME_VALUE

    async def test_both_sensors_silent_is_the_unknown_job_not_an_exception(self) -> None:
        """Only when neither sensor speaks does the reader admit it does not know."""
        assert self.reading(STATE_UNAVAILABLE, STATE_UNAVAILABLE) == UNKNOWN_JOB_NAME

    async def test_the_downloaded_file_still_wins_when_both_speak(self) -> None:
        """The `NNNN-name.gcode` form is the identity every historical row was named
        with, so while it speaks it stays the name — a fallback that outranked it would
        rename the same job between two glances."""
        assert self.reading(JOB_NAME, GCODE_NAME_VALUE) == JOB_NAME

    async def test_a_blank_downloaded_file_falls_back_not_through(self) -> None:
        """Whitespace is silence, not a name: a sensor answering `"   "` yields to the
        fallback rather than naming the job a blank string."""
        assert self.reading("   ", GCODE_NAME_VALUE) == GCODE_NAME_VALUE

    async def test_a_blank_gcode_name_is_silence_at_the_fallback_too(self) -> None:
        """The same whitespace rule, applied to the second sensor: a fallback answering
        `"   "` names nothing, and the reader admits the unknown job rather than
        passing a blank string down as a name."""
        assert self.reading(STATE_UNAVAILABLE, "   ") == UNKNOWN_JOB_NAME


class TestCurrentTrays:
    async def test_a_tagged_tray_translates_completely(self) -> None:
        """Tag, presence and every hint — the register form pre-fills from these."""
        readings = await BambuLabGateway(as_hass(bambu_hass())).current_trays()

        assert readings[a_tray(1)] == TrayReading(
            tray=a_tray(1),
            tag=TRAY_1_TAG,
            empty=False,
            reel=TRAY_1_REEL,
            name="Bambu PLA Basic",
            material="PLA",
            colour=Colour(0x5E, 0x43, 0xB7, 0xFF),
            weight=Grams.of(1000),
        )

    async def test_the_tags_own_weight_becomes_the_readings_opening_figure(self) -> None:
        """`tray_weight` is a string of grams the RFID carries, and it is what a reel of
        this product held new — so auto-registration opens a 250 g reel at 250 g rather
        than at a default that suits only the kilo spools (docs/12-field-notes.md)."""
        hass = bambu_hass()
        hass.states.by_entity_id[TRAY_2] = tray_state(
            TRAY_2, {**TRAY_ATTRIBUTES[TRAY_2], "tray_weight": "250"}
        )

        readings = await BambuLabGateway(as_hass(hass)).current_trays()

        assert readings[a_tray(2)].weight == Grams.of(250)

    async def test_a_tag_that_declines_to_say_reports_no_weight(self) -> None:
        """Tray 3's untagged reel reports `tray_weight: "0"` on the reference machine.
        Zero is the tag saying nothing, never a reel holding nothing — the register path
        reads the absence as *fall back to the configured default*."""
        readings = await BambuLabGateway(as_hass(bambu_hass())).current_trays()

        assert readings[a_tray(3)].weight is None

    @pytest.mark.parametrize(
        "unusable",
        [
            pytest.param("", id="an-empty-string"),
            pytest.param("n/a", id="a-word-where-a-number-belongs"),
            pytest.param("-500", id="a-negative-reel"),
            pytest.param("NaN", id="a-decimal-shaped-nothing"),
            pytest.param(None, id="an-absent-attribute"),
            pytest.param(True, id="a-bool-that-int-would-have-accepted"),
        ],
    )
    async def test_an_unusable_weight_is_dropped_never_fabricated(self, unusable: object) -> None:
        """The reading stays whole and the figure goes missing: `_read` is total by
        construction, and the domain refuses a non-positive opening weight anyway."""
        hass = bambu_hass()
        hass.states.by_entity_id[TRAY_2] = tray_state(
            TRAY_2, {**TRAY_ATTRIBUTES[TRAY_2], "tray_weight": unusable}
        )

        readings = await BambuLabGateway(as_hass(hass)).current_trays()

        assert readings[a_tray(2)].weight is None
        assert readings[a_tray(2)].tag == TRAY_2_TAG

    async def test_sixteen_zeros_is_an_absent_tag_not_an_identity(self) -> None:
        """Tray 3 holds a third-party or refilled spool: physically present, no readable
        tag. Treating the sentinel as identity would merge every untagged spool the owner
        ever buys into one (docs/12-field-notes.md)."""
        readings = await BambuLabGateway(as_hass(bambu_hass())).current_trays()

        assert readings[a_tray(3)].tag is None
        assert readings[a_tray(3)].empty is False

    async def test_the_rrggbbaa_colour_hint_survives_translation(self) -> None:
        """The printer speaks `#RRGGBBAA` — exactly the domain's storage format, so the
        alpha channel crosses the boundary intact."""
        readings = await BambuLabGateway(as_hass(bambu_hass())).current_trays()

        assert readings[a_tray(4)].colour == Colour(255, 255, 255, 255)

    @pytest.mark.parametrize("unusable", ["unavailable", "unknown"])
    async def test_an_unusable_tray_sensor_is_omitted_not_reported_empty(
        self, unusable: str
    ) -> None:
        """Absence of data is not absence of a spool: reporting a blinked sensor as an
        empty tray would unmount whatever the ledger has in that slot."""
        hass = bambu_hass()
        hass.states.by_entity_id[TRAY_2] = State(TRAY_2, unusable)

        readings = await BambuLabGateway(as_hass(hass)).current_trays()

        assert sorted(readings) == [a_tray(1), a_tray(3), a_tray(4)]

    async def test_a_missing_state_is_omitted(self) -> None:
        hass = bambu_hass()
        del hass.states.by_entity_id[TRAY_2]

        readings = await BambuLabGateway(as_hass(hass)).current_trays()

        assert sorted(readings) == [a_tray(1), a_tray(3), a_tray(4)]

    async def test_an_emptied_tray_drops_the_leftover_hints(self) -> None:
        """The capture holds no empty tray, so this flips the one observed flag: whatever
        name, tag and colour the attributes still carry belonged to the previous occupant
        and must not survive into the reading."""
        hass = bambu_hass()
        emptied = {**TRAY_ATTRIBUTES[TRAY_1], "empty": True}
        hass.states.by_entity_id[TRAY_1] = tray_state(TRAY_1, emptied)

        readings = await BambuLabGateway(as_hass(hass)).current_trays()

        assert readings[a_tray(1)] == TrayReading(tray=a_tray(1), tag=None, empty=True)

    async def test_malformed_attributes_omit_the_slot(self) -> None:
        """No `empty` flag means the reading cannot be trusted in either direction."""
        hass = bambu_hass()
        hass.states.by_entity_id[TRAY_1] = State(TRAY_1, "loaded", {"slot": 1})

        readings = await BambuLabGateway(as_hass(hass)).current_trays()

        assert sorted(readings) == [a_tray(2), a_tray(3), a_tray(4)]


class TestSubscription:
    async def test_a_tray_change_reaches_the_listener(self) -> None:
        hass = bambu_hass()
        gateway = BambuLabGateway(as_hass(hass))
        listener = RecordingListener()
        gateway.subscribe(listener)

        fire_tray_change(hass, TRAY_4, tray_state(TRAY_4))
        await hass.drain()

        assert listener.received == [
            TrayReading(
                tray=a_tray(4),
                tag=TRAY_4_TAG,
                empty=False,
                reel=TRAY_4_REEL,
                name="Bambu PLA Matte",
                material="PLA",
                colour=Colour(255, 255, 255, 255),
                weight=Grams.of(1000),
            )
        ]

    async def test_a_change_for_an_unwatched_entity_never_arrives(self) -> None:
        """The tracker filters on entity id; the printer's weight sensor changing is not
        a tray change."""
        hass = bambu_hass()
        gateway = BambuLabGateway(as_hass(hass))
        listener = RecordingListener()
        gateway.subscribe(listener)

        fire_tray_change(
            hass,
            "sensor.a1_00000000testser_peso_de_la_impresion",
            State("sensor.a1_00000000testser_peso_de_la_impresion", "40.51"),
        )
        await hass.drain()

        assert listener.received == []

    async def test_a_tray_going_unavailable_is_not_a_tray_change(self) -> None:
        hass = bambu_hass()
        gateway = BambuLabGateway(as_hass(hass))
        listener = RecordingListener()
        gateway.subscribe(listener)

        fire_tray_change(hass, TRAY_1, State(TRAY_1, "unavailable"))
        await hass.drain()

        assert listener.received == []

    async def test_malformed_attributes_are_skipped_without_raising(self) -> None:
        """The dispatch runs inside Home Assistant's event loop; a payload upstream
        reshapes must degrade to a debug line, never an exception."""
        hass = bambu_hass()
        gateway = BambuLabGateway(as_hass(hass))
        listener = RecordingListener()
        gateway.subscribe(listener)

        fire_tray_change(hass, TRAY_1, State(TRAY_1, "loaded", {"slot": 1}))
        await hass.drain()

        assert listener.received == []

    async def test_a_failing_listener_is_contained_and_the_next_one_still_runs(self) -> None:
        hass = bambu_hass()
        gateway = BambuLabGateway(as_hass(hass))

        async def failing(reading: TrayReading) -> None:
            msg = "the ledger is unavailable"
            raise RuntimeError(msg)

        recorder = RecordingListener()
        gateway.subscribe(failing)
        gateway.subscribe(recorder)

        fire_tray_change(hass, TRAY_4, tray_state(TRAY_4))
        await hass.drain()  # would re-raise if the failure escaped the delivery task

        assert len(recorder.received) == 1

    async def test_detach_unsubscribes_from_the_bus(self) -> None:
        hass = bambu_hass()
        gateway = BambuLabGateway(as_hass(hass))
        listener = RecordingListener()
        gateway.subscribe(listener)

        gateway.detach()

        assert hass.bus.listeners == []
        fire_tray_change(hass, TRAY_4, tray_state(TRAY_4))
        await hass.drain()
        assert listener.received == []

    async def test_detach_twice_is_a_no_op(self) -> None:
        """A clean unload runs `detach` twice — once at the top of `async_unload_entry`,
        once via the `async_on_unload` safety net — and the second call must find
        nothing left to do."""
        hass = bambu_hass()
        gateway = BambuLabGateway(as_hass(hass))
        gateway.subscribe(RecordingListener())

        gateway.detach()
        gateway.detach()

        assert hass.bus.listeners == []

    async def test_without_bambu_lab_the_gateway_is_dormant(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`ha-bambulab` installed but no tray entities — same as not installed at all:
        nothing watched, nothing reported, one debug line saying so. Late binding is a
        documented non-goal; reloading the entry re-runs discovery."""
        hass = FakeHass()
        plant_registry(hass, [row for row in REGISTRY_ROWS if row["translation_key"] != "tray"])
        gateway = BambuLabGateway(as_hass(hass))
        listener = RecordingListener()

        with caplog.at_level(logging.DEBUG):
            gateway.subscribe(listener)

        assert await gateway.current_trays() == {}
        assert hass.bus.listeners == []
        assert "dormant" in caplog.text


class TestEndToEnd:
    """The gateway feeding the real ledger, the way the composition root wires it."""

    async def test_the_startup_pass_heals_the_drift_the_owner_actually_has(
        self, harness: Harness
    ) -> None:
        """Tray 1 is physically loaded while the ledger says storage, and tray 4 carries
        an unregistered PLA Matte — the exact drift on the reference instance the day
        this gateway was written. The pass mounts the known spool and reports the
        unknown tags without inventing anything."""
        plant_registry(harness.hass, REGISTRY_ROWS)
        for entity_id in TRAY_ATTRIBUTES:
            harness.hass.states.by_entity_id[entity_id] = tray_state(entity_id)
        spool_id = await a_spool(harness.ledger, tag_uid=TRAY_1_TAG)
        gateway = BambuLabGateway(as_hass(harness.hass))

        for reading in (await gateway.current_trays()).values():
            await harness.ledger.use_cases.detect_spool.execute(reading)

        assert await located(harness.ledger, spool_id) == AmsSlot(a_tray(1))
        unknown = harness.ledger.events.of(UnknownSpoolDetected)
        # Trays 2 and 4 carry unregistered tags; tray 3's unreadable tag asks for nothing.
        assert len(unknown) == 2
        assert UnknownSpoolDetected(tag_uid=TRAY_4_TAG, tray=a_tray(4)) in unknown
        assert len(await harness.ledger.use_cases.queries.overview()) == 1

    async def test_a_tray_change_with_a_known_tag_mounts_the_spool(self, harness: Harness) -> None:
        plant_registry(harness.hass, REGISTRY_ROWS)
        spool_id = await a_spool(harness.ledger, tag_uid=TRAY_2_TAG)
        gateway = BambuLabGateway(as_hass(harness.hass))
        gateway.subscribe(harness.ledger.use_cases.detect_spool.execute)

        fire_tray_change(harness.hass, TRAY_2, tray_state(TRAY_2))
        await harness.hass.drain()

        assert await located(harness.ledger, spool_id) == AmsSlot(a_tray(2))
        assert SpoolMounted(spool_id=spool_id, tray=a_tray(2)) in harness.ledger.events.published

    async def test_an_unknown_tag_is_reported_and_creates_nothing(self, harness: Harness) -> None:
        """A guessed opening weight is a fabricated number; with auto-registration off —
        this harness's wiring — the sighting becomes an event for the review queue,
        never a spool."""
        plant_registry(harness.hass, REGISTRY_ROWS)
        gateway = BambuLabGateway(as_hass(harness.hass))
        gateway.subscribe(harness.ledger.use_cases.detect_spool.execute)

        fire_tray_change(harness.hass, TRAY_4, tray_state(TRAY_4))
        await harness.hass.drain()

        assert await harness.ledger.use_cases.queries.overview() == []
        [event] = harness.ledger.events.of(UnknownSpoolDetected)
        assert event == UnknownSpoolDetected(tag_uid=TRAY_4_TAG, tray=a_tray(4))


class TestJobEventTranslation:
    """`bambu_lab_event` into domain terms, reading the moment's sensors.

    Every figure is captured off the fixture-shaped states from the reference instance.
    The per-tray attribute dialect is pinned by the live Q4 capture
    (`print_sensors_finished.json` — docs/12-field-notes.md, 2026-08-03); the shapes the
    capture does not cover (`External Spool`, a second AMS) come from the installed
    upstream source.
    """

    def subscribed(self, hass: FakeHass) -> RecordingPrintListener:
        listener = RecordingPrintListener()
        BambuLabGateway(as_hass(hass)).subscribe_jobs(listener)
        return listener

    async def test_a_start_carries_the_name_and_no_plan_when_attributes_are_empty(self) -> None:
        """The Q4-open shape, exactly as captured: the weight sensor's state populates,
        its attributes do not. The plan is `None` — never a zero."""
        hass = bambu_hass()
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_started")
        await hass.drain()

        assert listener.received == [PrintStarted(name=JOB_NAME, printer=A_PRINTER, plan=None)]

    async def test_a_republished_breakdown_translates_its_tray_keys(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`AMS 1 Tray n` becomes `a_tray(n)` here and nowhere else (docs/05 §5.8).
        The external-spool figure has no AMS slot to land in, so it is dropped loudly."""
        hass = bambu_hass()
        listener = self.subscribed(hass)
        fire_job_event(hass, "event_print_started")
        await hass.drain()

        with caplog.at_level(logging.WARNING):
            fire_weight_change(
                hass, {"AMS 1 Tray 1": 28.4, "AMS 1 Tray 2": 6.1, "External Spool": 1.2}
            )
        fire_job_event(hass, "event_print_finished")
        await hass.drain()

        ended = listener.received[-1]
        assert isinstance(ended, PrintEnded)
        assert ended.reported_usage == {a_tray(1): Grams.of("28.4"), a_tray(2): Grams.of("6.1")}
        assert "external spool" in caplog.text

    async def test_the_external_spool_warning_does_not_repeat_per_republish(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The sensor is republished repeatedly through a print. The figure is still
        dropped loudly, but *once per observation* — the same attributes again is the same
        observation, not news."""
        hass = bambu_hass()
        self.subscribed(hass)

        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                fire_weight_change(hass, {"AMS 1 Tray 1": 28.4, "External Spool": 1.2})

        assert caplog.text.count("external spool") == 1

    @pytest.mark.parametrize(
        "unusable",
        [
            pytest.param(float("inf"), id="infinity"),
            pytest.param(float("-inf"), id="negative-infinity"),
            pytest.param(1e30, id="a-figure-too-large-to-quantise"),
            pytest.param(float("nan"), id="not-a-number"),
        ],
    )
    async def test_a_figure_no_quantity_can_hold_is_skipped_not_raised(
        self, unusable: float
    ) -> None:
        """All four are floats, so a type check waves them through and `Grams.of` raises
        — `InvalidOperation` for the first three, `ValueError` for the last. This runs on
        every republish now, from a callback that promised the event loop it never
        raises, so the guard is the difference between a skipped key and an exception
        unwinding the bus dispatch."""
        hass = bambu_hass()
        listener = self.subscribed(hass)
        fire_job_event(hass, "event_print_started")
        await hass.drain()

        fire_weight_change(hass, {"AMS 1 Tray 1": unusable, "AMS 1 Tray 2": 9.4})
        fire_job_event(hass, "event_print_finished")
        await hass.drain()

        ended = listener.received[-1]
        assert isinstance(ended, PrintEnded)
        # The bad key is gone; the honest one beside it survives.
        assert ended.reported_usage == {a_tray(2): Grams.of("9.4")}

    async def test_malformed_per_tray_figures_are_skipped_not_invented(self) -> None:
        """Strings in an attribute dictionary, no schema, no version: a textual figure, a
        negative one and a second AMS are all noise — only honest rows survive."""
        hass = bambu_hass()
        listener = self.subscribed(hass)
        fire_job_event(hass, "event_print_started")
        await hass.drain()
        fire_weight_change(
            hass,
            {
                "AMS 1 Tray 1": "lots",
                "AMS 1 Tray 2": -3,
                "AMS 2 Tray 1": 7.5,
                "AMS 1 Tray 3": 5.0,
            },
        )

        fire_job_event(hass, "event_print_finished")
        await hass.drain()

        ended = listener.received[-1]
        assert isinstance(ended, PrintEnded)
        assert ended.reported_usage == {a_tray(3): Grams.of(5)}

    async def test_a_start_discards_the_previous_jobs_figures(self) -> None:
        """The measured race (docs/12-field-notes.md): upstream updates the weight sensor
        about three-quarters of a minute *after* the start event fires, so whatever stands
        on it when a job starts describes the job before. Inheriting it is how a 937-layer
        print was charged the 2.1 g of its predecessor — so the start carries no plan and
        throws the held reading away."""
        hass = bambu_hass()
        listener = self.subscribed(hass)
        fire_weight_change(hass, {"AMS 1 Tray 1": 2.1})  # the previous job's figures

        fire_job_event(hass, "event_print_started")
        await hass.drain()
        fire_job_event(hass, "event_print_finished")
        await hass.drain()

        started, ended = listener.lifecycle
        assert isinstance(started, PrintStarted)
        assert isinstance(ended, PrintEnded)
        assert started.plan is None
        # Nothing arrived during this job, so the ending reports the honest absence
        # rather than the figures still sitting on the sensor.
        assert ended.reported_usage is None

    async def test_a_shape_without_tray_keys_never_erases_one_with_them(self) -> None:
        """The flicker itself, and the whole reason the reading is held rather than
        sampled: each republish burst is a pair of rows one to four seconds apart, one
        carrying the breakdown and one carrying no tray key at all, with the state value
        unchanged. A finish landing beside the empty half of a pair used to charge a
        220-layer two-colour print nothing.

        An unavailable sensor is the same silence, and is treated the same way."""
        hass = bambu_hass()
        listener = self.subscribed(hass)
        fire_job_event(hass, "event_print_started")
        await hass.drain()

        fire_weight_change(hass, {"AMS 1 Tray 1": 31.33, "AMS 1 Tray 3": 62.38})
        fire_weight_change(hass, WEIGHT_WITHOUT_BREAKDOWN)
        fire_tray_change(hass, WEIGHT, State(WEIGHT, "unavailable"))
        fire_job_event(hass, "event_print_finished")
        await hass.drain()

        ended = listener.received[-1]
        assert isinstance(ended, PrintEnded)
        assert ended.reported_usage == {
            a_tray(1): Grams.of("31.33"),
            a_tray(3): Grams.of("62.38"),
        }

    async def test_two_machines_never_share_a_held_reading(self) -> None:
        """The state is per printer, and so is the discard: one machine starting a print
        must not blank what the other machine is holding mid-job."""
        hass = two_printer_hass()
        listener = self.subscribed(hass)
        fire_job_event(hass, "event_print_started")
        await hass.drain()
        fire_weight_change(hass, {"AMS 1 Tray 1": 38.2})
        fire_weight_change(hass, {"AMS 1 Tray 2": 9.4}, entity_id=SECOND_WEIGHT)

        # The second machine starts, which discards only its own held reading.
        fire_job_event(hass, "event_print_started", device_id=SECOND_DEVICE)
        await hass.drain()
        fire_job_event(hass, "event_print_finished")
        await hass.drain()
        fire_job_event(hass, "event_print_finished", device_id=SECOND_DEVICE)
        await hass.drain()

        endings = [event for event in listener.received if isinstance(event, PrintEnded)]
        mine, theirs = endings
        assert mine.printer == A_PRINTER
        assert mine.reported_usage == {a_tray(1): Grams.of("38.2")}
        assert theirs.printer == ANOTHER_PRINTER
        assert theirs.reported_usage is None

    async def test_detach_forgets_the_readings_it_was_holding(self) -> None:
        """A reload builds a new gateway; a plan carried across one would describe a job
        nobody is watching any more.

        The sensor is left on the breakdown-less half afterwards so what is being observed
        is the *held* reading going away: with it still held, the ending would report the
        38.2 g below whatever the sensor said."""
        hass = bambu_hass()
        gateway = BambuLabGateway(as_hass(hass))
        listener = RecordingPrintListener()
        gateway.subscribe_jobs(listener)
        fire_weight_change(hass, {"AMS 1 Tray 1": 38.2})

        gateway.detach()
        gateway.subscribe_jobs(listener)  # as a reload's fresh subscription would
        hass.states.by_entity_id[WEIGHT] = State(WEIGHT, "40.51", WEIGHT_WITHOUT_BREAKDOWN)
        fire_job_event(hass, "event_print_finished")
        await hass.drain()

        [ended] = listener.lifecycle
        assert isinstance(ended, PrintEnded)
        assert ended.reported_usage is None

    async def test_a_reload_mid_print_still_reads_the_plan_that_is_already_there(self) -> None:
        """A config-entry reload — which every options change performs — builds a fresh
        gateway, and the state tracker only ever fires for *future* changes. The plan
        published earlier in this print is sitting in `hass.states` all the same, so the
        ending reads it rather than reporting the silence that started this whole fix.

        Nothing is subscribed until after the sensor already holds the breakdown, and no
        further weight event is fired: that *is* the reload."""
        hass = bambu_hass()
        hass.states.by_entity_id[WEIGHT] = State(WEIGHT, "93.71", {"AMS 1 Tray 1": 93.71})
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_finished")
        await hass.drain()

        [ended] = listener.received
        assert isinstance(ended, PrintEnded)
        assert ended.reported_usage == {a_tray(1): Grams.of("93.71")}

    async def test_a_watched_start_with_nothing_published_reports_the_absence(self) -> None:
        """The other half of the reload rule, and the reason it is a rule rather than a
        blanket live read: this gateway *saw* the job begin, so nothing has been published
        since, so the figures still on the sensor belong to the print before this one.
        Reading them live would charge this job with its predecessor's plan — the very
        defect being fixed. `None` sends it to the review queue instead."""
        hass = bambu_hass()
        listener = self.subscribed(hass)
        # The previous job's plan, still standing on the sensor.
        fire_weight_change(hass, {"AMS 1 Tray 1": 2.1})

        fire_job_event(hass, "event_print_started")
        await hass.drain()
        fire_job_event(hass, "event_print_finished")
        await hass.drain()

        ended = listener.received[-1]
        assert isinstance(ended, PrintEnded)
        assert ended.reported_usage is None

    async def test_a_changed_external_figure_is_announced_even_when_the_trays_stand_still(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The dedupe key is the whole reading. Comparing only the tray plan would drop
        this republish as a repeat and swallow the figure the warning exists to name."""
        hass = bambu_hass()
        self.subscribed(hass)

        with caplog.at_level(logging.WARNING):
            fire_weight_change(hass, {"AMS 1 Tray 1": 28.4, "External Spool": 1.2})
            fire_weight_change(hass, {"AMS 1 Tray 1": 28.4, "External Spool": 7.5})

        assert caplog.text.count("external spool") == 2

    async def test_a_second_ams_is_named_once_per_reading_not_once_per_republish(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """One AMS per printer is tracked, and a household with two hears so — once for
        the reading, not once for every republish of it."""
        hass = bambu_hass()
        self.subscribed(hass)

        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                fire_weight_change(hass, {"AMS 1 Tray 1": 28.4, "AMS 2 Tray 1": 7.5})

        assert caplog.text.count("one AMS per printer is tracked") == 1

    async def test_a_cancellation_captures_the_moments_figures(self) -> None:
        """Layers, progress and the raw state, read at the moment the event fires —
        the counters reset when the next print starts."""
        hass = bambu_hass()
        hass.states.by_entity_id[STATUS] = print_sensor_state(STATUS, state="pause")
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_canceled")
        await hass.drain()

        assert listener.received == [
            PrintEnded(
                outcome=PrintJobState.CANCELLED,
                name=JOB_NAME,
                printer=A_PRINTER,
                layer_reached=71,
                total_layers=209,
                progress=Percentage.of(34),
                reported_usage=None,
                raw_gcode_state="pause",
                raw_print_error=None,
            )
        ]

    async def test_a_failure_carries_the_verbatim_error_code(self) -> None:
        """The classification is the event type; the code rides along verbatim so a wrong
        classification stays recoverable (docs/07 §7.7)."""
        hass = bambu_hass()
        hass.states.by_entity_id[PRINT_ERROR] = print_sensor_state(
            PRINT_ERROR, state="on", attributes={"code": 50348044}
        )
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_failed")
        await hass.drain()

        [event] = listener.received
        assert isinstance(event, PrintEnded)
        assert event.outcome is PrintJobState.FAILED
        assert event.raw_print_error == 50348044

    async def test_the_q4_capture_translates_its_tray_key(self) -> None:
        """The populated shape captured live at the moment a print finished — the capture
        that closed Q4: the state carries the total, the attributes name tray 4, and the
        gateway translates exactly what the printer said."""
        hass = bambu_hass()
        shape = PRINT_SENSORS_FINISHED[WEIGHT]
        listener = self.subscribed(hass)
        fire_job_event(hass, "event_print_started")
        await hass.drain()
        fire_weight_change(
            hass,
            cast("dict[str, object]", shape["attributes"]),
            state=str(shape["state"]),
        )

        fire_job_event(hass, "event_print_finished")
        await hass.drain()

        ended = listener.received[-1]
        assert isinstance(ended, PrintEnded)
        assert ended.outcome is PrintJobState.FINISHED
        assert ended.reported_usage == {a_tray(4): Grams.of("296.56")}

    async def test_a_finish_uses_the_last_breakdown_seen_during_the_job(self) -> None:
        """The figures climb as the print runs, so the last one seen is the one that
        describes the whole job — and the ending is not the moment it is read, because by
        then the sensor may be mid-flicker."""
        hass = bambu_hass()
        listener = self.subscribed(hass)
        fire_job_event(hass, "event_print_started")
        await hass.drain()
        fire_weight_change(hass, {"AMS 1 Tray 1": 12.0})
        fire_weight_change(hass, {"AMS 1 Tray 1": 38.2, "AMS 1 Tray 2": 9.4})

        fire_job_event(hass, "event_print_finished")
        await hass.drain()

        ended = listener.received[-1]
        assert isinstance(ended, PrintEnded)
        assert ended.outcome is PrintJobState.FINISHED
        assert ended.reported_usage == {
            a_tray(1): Grams.of("38.2"),
            a_tray(2): Grams.of("9.4"),
        }

    async def test_unavailable_sensors_become_unknown_never_zero(self) -> None:
        """Every reader is total: a blinked sensor is an absent figure, and the event
        still crosses the boundary carrying the honest unknowns (docs/03 §3.8)."""
        hass = bambu_hass()
        for entity_id in PRINT_SENSORS:
            hass.states.by_entity_id[entity_id] = State(entity_id, "unavailable")
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_canceled")
        await hass.drain()

        assert listener.received == [
            PrintEnded(outcome=PrintJobState.CANCELLED, name=UNKNOWN_JOB_NAME, printer=A_PRINTER)
        ]

    async def test_an_event_for_a_device_no_printer_owns_never_arrives(self) -> None:
        """The bus carries every machine's events, and only a device discovery resolved to
        a printer may drive this ledger. The AMS device fires nothing today and owns no job
        sensors, so it stands in for anything else on the bus."""
        hass = bambu_hass()
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_started", device_id=AMS_DEVICE)
        await hass.drain()

        assert listener.received == []

    async def test_an_event_is_translated_with_its_own_machines_sensors(self) -> None:
        """The sharpest hazard, at the boundary that creates it.

        Both machines have a weight sensor and both are populated. The second machine's
        event must read the second machine's attributes — reading whichever sensor discovery
        happened to keep would put one printer's grams on the other printer's trays, and the
        use case downstream has no way to notice.
        """
        hass = two_printer_hass()
        listener = self.subscribed(hass)
        fire_weight_change(hass, {"AMS 1 Tray 1": 38.2})
        fire_weight_change(hass, {"AMS 1 Tray 2": 9.4}, entity_id=SECOND_WEIGHT, state="9.4")

        fire_job_event(hass, "event_print_finished", device_id=SECOND_DEVICE)
        await hass.drain()

        [event] = listener.lifecycle
        assert isinstance(event, PrintEnded)
        assert event.printer == ANOTHER_PRINTER
        assert event.reported_usage == {a_tray(2, printer=ANOTHER_PRINTER): Grams.of("9.4")}

    async def test_both_machines_events_reach_the_listener_named(self) -> None:
        """One bus subscription carries the whole house; the device id is what names the
        machine, and the translated event carries that name inward."""
        hass = two_printer_hass()
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_started")
        fire_job_event(hass, "event_print_started", device_id=SECOND_DEVICE)
        await hass.drain()

        assert [event.printer for event in listener.received] == [A_PRINTER, ANOTHER_PRINTER]

    async def test_the_printers_own_start_and_end_cross_the_boundary_beside_the_figures(
        self,
    ) -> None:
        """The machine's answer to how long the print ran, translated verbatim.

        Nothing here decides what to do with the pair — the domain does. The gateway's
        only job is to hand over two instants, and it hands over `None` for anything it
        cannot read as one.
        """
        hass = bambu_hass()
        hass.states.by_entity_id[START_TIME] = State(START_TIME, "2026-08-04T09:12:00+00:00", {})
        hass.states.by_entity_id[END_TIME] = State(END_TIME, "2026-08-04T11:47:00+00:00", {})
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_finished")
        await hass.drain()

        [event] = listener.received
        assert isinstance(event, PrintEnded)
        assert event.printer_started_at == datetime(2026, 8, 4, 9, 12, tzinfo=UTC)
        assert event.printer_ended_at == datetime(2026, 8, 4, 11, 47, tzinfo=UTC)

    async def test_a_start_carries_the_machines_own_start_moment(self) -> None:
        hass = bambu_hass()
        hass.states.by_entity_id[START_TIME] = State(START_TIME, "2026-08-04T09:12:00+02:00", {})
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_started")
        await hass.drain()

        [event] = listener.received
        assert isinstance(event, PrintStarted)
        # The offset is preserved as an instant, not reinterpreted: 09:12+02:00 is 07:12 UTC.
        assert event.printer_started_at == datetime(2026, 8, 4, 7, 12, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("reading", "why"),
        [
            ("2026-08-04T09:12:00", "no offset — a wall clock, not an instant"),
            ("just now", "not a timestamp at all"),
            ("", "the sensor reported an empty string"),
        ],
    )
    async def test_a_timestamp_this_boundary_cannot_trust_is_dropped(
        self, reading: str, why: str
    ) -> None:
        """A naive datetime names a wall clock, and this boundary has no business deciding
        which one — guessing UTC would silently shift a duration by the household's offset.
        The other two are upstream noise. All three are `None`, never an invented moment."""
        hass = bambu_hass()
        hass.states.by_entity_id[START_TIME] = State(START_TIME, reading, {})
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_started")
        await hass.drain()

        [event] = listener.received
        assert isinstance(event, PrintStarted)
        assert event.printer_started_at is None, why

    async def test_a_mid_print_error_event_is_not_a_lifecycle_edge(self) -> None:
        """`event_print_error` fires while the job keeps running; the code it announces
        is read off the error sensor when the ending arrives."""
        hass = bambu_hass()
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_error")
        await hass.drain()

        assert listener.received == []

    async def test_without_print_sensors_job_events_stay_dormant(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Same policy as the trays: nothing discovered, nothing watched, one debug line.
        Reloading the entry after `ha-bambulab` appears re-runs discovery."""
        hass = FakeHass()
        plant_registry(hass, [row for row in REGISTRY_ROWS if row["translation_key"] == "tray"])
        gateway = BambuLabGateway(as_hass(hass))
        listener = RecordingPrintListener()

        with caplog.at_level(logging.DEBUG):
            gateway.subscribe_jobs(listener)

        assert hass.bus.listeners == []
        assert "dormant" in caplog.text

    async def test_detach_unsubscribes_job_events_too(self) -> None:
        hass = bambu_hass()
        gateway = BambuLabGateway(as_hass(hass))
        listener = RecordingPrintListener()
        gateway.subscribe_jobs(listener)

        gateway.detach()
        gateway.detach()  # idempotent, same as the tray half

        assert hass.bus.listeners == []
        fire_job_event(hass, "event_print_started")
        await hass.drain()
        assert listener.received == []


class TestPrintLifecycleEndToEnd:
    """The gateway feeding `TrackPrintJob` and the review queue, as the composition root
    wires it: fake bus events in, real rows and reviews out."""

    def wire(self, harness: Harness, rows: list[dict[str, str]] | None = None) -> BambuLabGateway:
        plant_registry(harness.hass, REGISTRY_ROWS if rows is None else rows)
        for entity_id in TRAY_ATTRIBUTES:
            harness.hass.states.by_entity_id[entity_id] = tray_state(entity_id)
        for entity_id in PRINT_SENSORS:
            harness.hass.states.by_entity_id[entity_id] = print_sensor_state(entity_id)
        gateway = BambuLabGateway(as_hass(harness.hass))

        async def deliver(event: PrintEvent) -> None:
            # The same shim the composition root installs: the listener's contract is
            # fire-and-forget, and the job id the use case returns is nobody's business.
            await harness.ledger.use_cases.track_print_job.execute(event)

        gateway.subscribe_jobs(deliver)
        return gateway

    async def test_a_start_becomes_a_running_job(self, harness: Harness) -> None:
        self.wire(harness)

        fire_job_event(harness.hass, "event_print_started")
        await harness.hass.drain()

        [job] = await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)
        assert job.name == JOB_NAME
        assert job.state is PrintJobState.RUNNING
        assert job.reported_usage is None  # the reference machine's Q4-open shape

    async def test_a_cancelled_print_lands_in_the_review_queue(self, harness: Harness) -> None:
        """Start to review, end to end: the plan captured at start, scaled by the layers
        reached, frozen to the spool that was mounted — and no balance touched."""
        spool_id = await a_spool(harness.ledger)
        await harness.ledger.use_cases.mount_spool.execute(spool_id, a_tray(1))
        self.wire(harness)

        fire_job_event(harness.hass, "event_print_started")
        await harness.hass.drain()
        # The plan arrives during the job, as it does on the machine: after the start.
        fire_weight_change(harness.hass, {"AMS 1 Tray 1": 209.0})
        fire_job_event(harness.hass, "event_print_canceled")
        await harness.hass.drain()

        [review] = await SqliteReviewRepository(harness.ledger.database).list_pending()
        assert review.reason is ReviewReason.CANCELLED
        assert review.estimated_usage == {a_tray(1): Grams.of(71)}
        assert review.charges == [(a_tray(1), ReviewCharge(spool_id, Grams.of(71)))]
        [event] = harness.ledger.events.of(ReviewOpened)
        assert isinstance(event, ReviewOpened)
        assert event.job_name == JOB_NAME
        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of(1000)

    async def test_a_terminal_event_after_a_restart_still_opens_a_review(
        self, harness: Harness
    ) -> None:
        """The integration restarted mid-print, so no row exists when the failure fires.
        The review must never be lost to a restart."""
        self.wire(harness)  # no started event was ever seen

        fire_job_event(harness.hass, "event_print_failed")
        await harness.hass.drain()

        [job] = await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)
        assert job.state is PrintJobState.FAILED
        [review] = await SqliteReviewRepository(harness.ledger.database).list_pending()
        assert review.job_id == job.id
        assert review.reason is ReviewReason.FAILED

    async def test_a_finished_print_deducts_automatically(self, harness: Harness) -> None:
        """UC-04 end to end (Q4, closed): the bus event fires, the gateway reads the
        populated attributes at that moment, and the ledger deducts from the mounted
        spool — no review, no decision, because the job ran to completion."""
        spool_id = await a_spool(harness.ledger)
        await harness.ledger.use_cases.mount_spool.execute(spool_id, a_tray(1))
        self.wire(harness)
        fire_job_event(harness.hass, "event_print_started")
        await harness.hass.drain()
        fire_weight_change(harness.hass, {"AMS 1 Tray 1": 38.2}, state="38.2")

        fire_job_event(harness.hass, "event_print_finished")
        await harness.hass.drain()

        [job] = await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)
        assert job.state is PrintJobState.FINISHED
        assert job.reported_usage == {a_tray(1): Grams.of("38.2")}
        assert job.consumption_recorded is True
        assert await SqliteReviewRepository(harness.ledger.database).list_pending() == []
        assert harness.ledger.events.of(ReviewOpened) == []
        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of("961.8")

    async def test_a_finish_without_per_tray_attributes_records_the_absence(
        self, harness: Harness
    ) -> None:
        """The attributes flicker (docs/12-field-notes.md): a finish can still arrive
        with the total populated and no breakdown. The job records `None` — a missing
        figure is not a figure of zero — and UC-04's missing-figure branch opens a
        review instead of deducting nothing silently."""
        self.wire(harness)
        fire_job_event(harness.hass, "event_print_started")
        await harness.hass.drain()

        fire_job_event(harness.hass, "event_print_finished")
        await harness.hass.drain()

        [job] = await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)
        assert job.state is PrintJobState.FINISHED
        assert job.reported_usage is None
        assert job.consumption_recorded is True
        [review] = await SqliteReviewRepository(harness.ledger.database).list_pending()
        assert review.job_id == job.id
        assert review.reason is ReviewReason.UNMAPPED_USAGE

    async def test_two_machines_printing_at_once_deduct_from_their_own_spools(
        self, harness: Harness
    ) -> None:
        """The whole release in one scenario, from the bus down to two balances.

        Both machines start; the second one finishes first and reports 9.4 g. Everything
        between the bus and the ledger has to hold for that figure to land on the reel in
        *that* machine's tray 2 — the device id resolving to the right printer, the weight
        sensor read being that printer's, the tray reference naming it, and the correlation
        reaching past the newest RUNNING row to the job that is actually its own.
        """
        here = a_tray(1, printer=A_PRINTER)
        there = a_tray(2, printer=ANOTHER_PRINTER)
        mine = await a_spool(harness.ledger, label="on the captured machine")
        yours = await a_spool(harness.ledger, label="on the second machine")
        await harness.ledger.use_cases.mount_spool.execute(mine, here)
        await harness.ledger.use_cases.mount_spool.execute(yours, there)
        self.wire(harness, rows=REGISTRY_ROWS + second_printer_rows())
        harness.hass.states.by_entity_id[SECOND_WEIGHT] = State(SECOND_WEIGHT, "9.4", {})

        fire_job_event(harness.hass, "event_print_started")
        await harness.hass.drain()
        fire_job_event(harness.hass, "event_print_started", device_id=SECOND_DEVICE)
        await harness.hass.drain()
        fire_weight_change(
            harness.hass, {"AMS 1 Tray 2": 9.4}, entity_id=SECOND_WEIGHT, state="9.4"
        )
        fire_job_event(harness.hass, "event_print_finished", device_id=SECOND_DEVICE)
        await harness.hass.drain()

        jobs = {
            job.printer: job
            for job in await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)
        }
        assert jobs[ANOTHER_PRINTER].state is PrintJobState.FINISHED
        assert jobs[ANOTHER_PRINTER].reported_usage == {there: Grams.of("9.4")}
        assert jobs[A_PRINTER].state is PrintJobState.RUNNING
        assert (await harness.ledger.use_cases.queries.detail(yours)).summary.balance == Grams.of(
            "990.6"
        )
        assert (await harness.ledger.use_cases.queries.detail(mine)).summary.balance == Grams.of(
            1000
        )


class TestEndingsTheBusNeverAnnounced:
    """The second ending path, end to end — the failure of 2026-08-08 and its fix.

    Measured on the reference instance that night: a seven-hour print reached `finish` at
    21:12:29 UTC after its sensors went unavailable at 21:08:13, and **no terminal
    `bambu_lab_event` was ever fired for it** — upstream guards that callback with
    `previous_gcode_state != "unknown"`, and a reconnection resets exactly that. The recorder
    holds 26 endings before it, every one announced correctly; the ledger held the
    twenty-seventh open with 248.41 g never deducted.

    These drive the recorded shapes rather than a tidied version of them: the sensor goes
    unavailable and returns already finished, and then bounces `finish → offline → finish`
    five times the way the machine did.
    """

    def wire(self, harness: Harness) -> BambuLabGateway:
        plant_registry(harness.hass, REGISTRY_ROWS)
        for entity_id in TRAY_ATTRIBUTES:
            harness.hass.states.by_entity_id[entity_id] = tray_state(entity_id)
        for entity_id in PRINT_SENSORS:
            harness.hass.states.by_entity_id[entity_id] = print_sensor_state(entity_id)
        gateway = BambuLabGateway(as_hass(harness.hass))

        async def deliver(event: PrintEvent) -> None:
            await harness.ledger.use_cases.track_print_job.execute(event)

        gateway.subscribe_jobs(deliver)
        return gateway

    async def a_running_print(self, harness: Harness, grams: str = "248.41") -> SpoolId:
        """A print under way with its plan published, and nothing announced about its end."""
        spool_id = await a_spool(harness.ledger)
        await harness.ledger.use_cases.mount_spool.execute(spool_id, a_tray(1))
        self.wire(harness)
        fire_status_change(harness.hass, "running")
        fire_job_event(harness.hass, "event_print_started")
        await harness.hass.drain()
        fire_weight_change(harness.hass, {"AMS 1 Tray 1": float(grams)}, state=grams)
        await harness.hass.drain()
        return spool_id

    async def test_a_finish_the_bus_never_announced_still_deducts(self, harness: Harness) -> None:
        """The night of 2026-08-08, replayed: unavailable across the ending, back already
        finished, not one word on the bus. The grams must still leave the spool."""
        spool_id = await self.a_running_print(harness)

        fire_status_change(harness.hass, "unavailable")
        fire_status_change(harness.hass, "finish")
        await harness.hass.drain()

        [job] = await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)
        assert job.state is PrintJobState.FINISHED
        assert job.reported_usage == {a_tray(1): Grams.of("248.41")}
        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of("751.59")

    async def test_both_signals_for_one_ending_deduct_once(self, harness: Harness) -> None:
        """The healthy print, where both routes speak within the same second. One job,
        one movement — the redundancy must cost nothing."""
        spool_id = await self.a_running_print(harness, grams="38.2")

        fire_job_event(harness.hass, "event_print_finished")
        fire_status_change(harness.hass, "finish")
        await harness.hass.drain()

        assert len(await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)) == 1
        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of("961.8")

    async def test_the_finish_offline_finish_flicker_deducts_once(self, harness: Harness) -> None:
        """What the machine actually did after that print stopped: five arrivals at
        `finish` in ten minutes. Each is a real transition; only the first has work."""
        spool_id = await self.a_running_print(harness)

        for _ in range(5):
            fire_status_change(harness.hass, "finish")
            fire_status_change(harness.hass, "offline")
        await harness.hass.drain()

        assert len(await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)) == 1
        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of("751.59")

    async def test_an_idle_machine_arriving_at_finish_mints_no_job(self, harness: Harness) -> None:
        """No print was ever running. A machine reconnecting onto its resting `finish`
        must write nothing at all — the phantom-job failure this path could have been."""
        spool_id = await a_spool(harness.ledger)
        await harness.ledger.use_cases.mount_spool.execute(spool_id, a_tray(1))
        self.wire(harness)

        fire_status_change(harness.hass, "offline")
        fire_status_change(harness.hass, "finish")
        await harness.hass.drain()

        assert await SqlitePrintJobRepository(harness.ledger.database).list_recent(10) == []
        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of(1000)

    async def test_a_print_still_running_is_left_alone(self, harness: Harness) -> None:
        """`offline` is a fact about the connection, not about the job — that print
        flickered offline and back dozens of times while printing perfectly well."""
        await self.a_running_print(harness)

        for _ in range(4):
            fire_status_change(harness.hass, "offline")
            fire_status_change(harness.hass, "running")
        await harness.hass.drain()

        [job] = await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)
        assert job.state is PrintJobState.RUNNING

    async def test_one_machines_finish_does_not_end_anothers_print(self, harness: Harness) -> None:
        """Two machines, two status sensors. An ending inferred from one must correlate
        against that one — the same rule the bus path was given in v2.0.

        Both machines report `running` here, so both get a row: the second one's was
        announced on the bus, and the first one's is inferred from its own level, which is
        what this gateway now does for a machine printing with no row to its name. The
        assertion is per machine because that is the whole claim — the first one's `finish`
        closes the first one's row and leaves the second one's print exactly where it was.
        """
        plant_registry(harness.hass, REGISTRY_ROWS + second_printer_rows())
        for entity_id in PRINT_SENSORS:
            harness.hass.states.by_entity_id[entity_id] = print_sensor_state(entity_id)
        harness.hass.states.by_entity_id[SECOND_STATUS] = State(SECOND_STATUS, "running", {})
        harness.hass.states.by_entity_id[SECOND_WEIGHT] = State(SECOND_WEIGHT, "12.5", {})
        gateway = BambuLabGateway(as_hass(harness.hass))

        async def deliver(event: PrintEvent) -> None:
            await harness.ledger.use_cases.track_print_job.execute(event)

        gateway.subscribe_jobs(deliver)
        fire_job_event(harness.hass, "event_print_started", device_id=SECOND_DEVICE)
        await harness.hass.drain()

        fire_status_change(harness.hass, "running")
        fire_status_change(harness.hass, "finish")
        await harness.hass.drain()

        by_printer = {
            job.printer: job
            for job in await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)
        }
        assert by_printer[ANOTHER_PRINTER].state is PrintJobState.RUNNING
        assert by_printer[A_PRINTER].state is PrintJobState.FINISHED


class TestStartsTheBusNeverAnnounced:
    """The *first* edge, and the failure of 2026-08-11 — the mirror of the class above.

    Upstream guards `event_print_started` with the same `previous_gcode_state != "unknown"`
    it guards the finish with, and a reconnection resets exactly that. So a machine whose
    connection drops before its own start announces nothing, the ledger opens no row, and
    the ending — whenever it arrives — finds nothing to close and is discarded as an
    inference about a print already recorded. The whole job disappears, grams and all.

    Measured on the reference instance that night: `estado_de_la_impresion` went
    `unavailable` at 22:38:28 and returned `running` at 22:38:40, and at 22:57 the machine
    was 68 % through a 291.42 g print that had **no row in the ledger at all**. The 248.41 g
    `MANUAL_ADJUSTMENT` of 2026-08-08 is the same hole, paid for by hand.

    The hard case is not opening the row. It is opening exactly one: on a healthy print the
    inference and the announcement race within the same second, and the machine bounces
    `offline → running` ten times inside one job while printing perfectly well.
    """

    def wire(self, harness: Harness) -> BambuLabGateway:
        plant_registry(harness.hass, REGISTRY_ROWS)
        for entity_id in TRAY_ATTRIBUTES:
            harness.hass.states.by_entity_id[entity_id] = tray_state(entity_id)
        for entity_id in PRINT_SENSORS:
            harness.hass.states.by_entity_id[entity_id] = print_sensor_state(entity_id)
        gateway = BambuLabGateway(as_hass(harness.hass))

        async def deliver(event: PrintEvent) -> None:
            await harness.ledger.use_cases.track_print_job.execute(event)

        gateway.subscribe_jobs(deliver)
        return gateway

    async def a_mounted_spool(self, harness: Harness) -> SpoolId:
        spool_id = await a_spool(harness.ledger)
        await harness.ledger.use_cases.mount_spool.execute(spool_id, a_tray(1))
        self.wire(harness)
        return spool_id

    async def jobs(self, harness: Harness) -> list[PrintJob]:
        return await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)

    async def test_a_start_the_bus_never_announced_opens_a_row(self, harness: Harness) -> None:
        """22:38:28 to 22:38:40, replayed: away, back already printing, not one word on
        the bus. The ledger must not be blind to that print."""
        await self.a_mounted_spool(harness)

        fire_status_change(harness.hass, "unavailable")
        fire_status_change(harness.hass, "running")
        await harness.hass.drain()

        [job] = await self.jobs(harness)
        assert job.state is PrintJobState.RUNNING

    async def test_the_print_it_opened_is_charged_when_it_ends(self, harness: Harness) -> None:
        """The whole point. A row nobody opened is worth nothing on its own — what was
        lost that night was the deduction, and this is the path that recovers it."""
        spool_id = await self.a_mounted_spool(harness)

        fire_status_change(harness.hass, "unavailable")
        fire_status_change(harness.hass, "running")
        await harness.hass.drain()
        fire_weight_change(harness.hass, {"AMS 1 Tray 1": 291.42}, state="291.42")
        await harness.hass.drain()
        fire_status_change(harness.hass, "finish")
        await harness.hass.drain()

        [job] = await self.jobs(harness)
        assert job.state is PrintJobState.FINISHED
        assert job.reported_usage == {a_tray(1): Grams.of("291.42")}
        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of("708.58")

    async def test_the_healthy_start_opens_exactly_one_row(self, harness: Harness) -> None:
        """Both signals, same moment — the shape of every ordinary print. The inference
        opens the row and the announcement must find it already there, or UC-04 deducts
        the same grams twice for the rest of time."""
        await self.a_mounted_spool(harness)

        fire_status_change(harness.hass, "running")
        fire_job_event(harness.hass, "event_print_started")
        await harness.hass.drain()

        assert len(await self.jobs(harness)) == 1

    async def test_the_announcement_first_then_the_inference_opens_one_row(
        self, harness: Harness
    ) -> None:
        """The same race, lost the other way round. Neither order may duplicate."""
        await self.a_mounted_spool(harness)

        fire_job_event(harness.hass, "event_print_started")
        fire_status_change(harness.hass, "running")
        await harness.hass.drain()

        assert len(await self.jobs(harness)) == 1

    async def test_the_offline_running_flicker_opens_one_row(self, harness: Harness) -> None:
        """What the machine actually did on 2026-08-10: ten `offline → running` arrivals
        in the thirty-six minutes of one job, printing perfectly well throughout. Every one
        is a real transition and a real re-read of the level; only the first has work."""
        await self.a_mounted_spool(harness)
        fire_status_change(harness.hass, "running")
        await harness.hass.drain()

        for _ in range(10):
            fire_status_change(harness.hass, "offline")
            fire_status_change(harness.hass, "running")
        await harness.hass.drain()

        assert len(await self.jobs(harness)) == 1

    async def test_an_idle_machine_mints_no_job(self, harness: Harness) -> None:
        """The phantom-job failure this path could have been. `idle` and `offline` are
        where a machine rests, and it rests there for days."""
        await self.a_mounted_spool(harness)

        fire_status_change(harness.hass, "offline")
        fire_status_change(harness.hass, "idle")
        await harness.hass.drain()

        assert await self.jobs(harness) == []

    async def test_the_stage_sensor_alone_can_open_a_row(self, harness: Harness) -> None:
        """The two levels fail separately, which is why both are watched. With
        `print_status` still unavailable, `stage` is the one that answers."""
        await self.a_mounted_spool(harness)

        fire_status_change(harness.hass, "unavailable")
        fire_status_change(harness.hass, "printing", entity_id=STAGE)
        await harness.hass.drain()

        [job] = await self.jobs(harness)
        assert job.state is PrintJobState.RUNNING

    async def test_a_paused_print_is_still_a_print(self, harness: Harness) -> None:
        """A machine cannot pause what it is not running, so the whole `paused_*` family
        opens a row. `paused_filament_runout` is the one an AMS ledger meets most."""
        await self.a_mounted_spool(harness)

        fire_status_change(harness.hass, "unavailable")
        fire_status_change(harness.hass, "paused_filament_runout", entity_id=STAGE)
        await harness.hass.drain()

        [job] = await self.jobs(harness)
        assert job.state is PrintJobState.RUNNING

    async def test_a_stage_that_also_happens_when_idle_mints_no_job(self, harness: Harness) -> None:
        """`heating_hotend` occurs inside a print *and* during a calibration a machine runs
        while idle, and there is no third signal here to tell those apart. Refusing it costs
        nothing — the stage reaches `printing` moments later, and this is a level that is
        read again on every transition rather than an edge missed once and gone."""
        await self.a_mounted_spool(harness)

        fire_status_change(harness.hass, "unavailable")
        fire_status_change(harness.hass, "heating_hotend", entity_id=STAGE)
        await harness.hass.drain()

        assert await self.jobs(harness) == []


class TestJobSync:
    """The startup pass: a machine that stopped while nobody was listening.

    `TestEndingsTheBusNeverAnnounced` covers the ending that arrives while the integration
    is up. This covers the one that already happened — the print finished during a restart,
    a reload, or the dropout that swallowed its event, and the row has been open ever since
    with nothing left in the system that would ever close it.

    It reads a *level*, so the phantom-job case is the one that has to be nailed hardest:
    an idle machine reads `finish` all day long.
    """

    def wire(self, harness: Harness, status: str, weight: dict[str, object] | None) -> JobSync:
        """A freshly constructed gateway over a machine already in `status` — which is what
        setup builds after a restart, with no history of how the printer got there."""
        plant_registry(harness.hass, REGISTRY_ROWS)
        for entity_id in TRAY_ATTRIBUTES:
            harness.hass.states.by_entity_id[entity_id] = tray_state(entity_id)
        for entity_id in PRINT_SENSORS:
            harness.hass.states.by_entity_id[entity_id] = print_sensor_state(entity_id)
        harness.hass.states.by_entity_id[STATUS] = State(STATUS, status, {})
        if weight is not None:
            harness.hass.states.by_entity_id[WEIGHT] = State(WEIGHT, "248.41", weight)
        return JobSync(
            gateway=BambuLabGateway(as_hass(harness.hass)),
            track_print_job=harness.ledger.use_cases.track_print_job,
        )

    async def an_open_job(self, harness: Harness) -> SpoolId:
        """The row the night of 2026-08-08 left behind: RUNNING, never charged."""
        spool_id = await a_spool(harness.ledger)
        await harness.ledger.use_cases.mount_spool.execute(spool_id, a_tray(1))
        await harness.ledger.use_cases.track_print_job.execute(
            PrintStarted(name=JOB_NAME, printer=A_PRINTER)
        )
        return spool_id

    async def test_a_job_left_open_by_a_restart_is_closed_and_charged(
        self, harness: Harness
    ) -> None:
        """The recovery this pass was written for. The machine is sitting on the finish
        nobody heard, and the plan it published during the print is still on the sensor."""
        spool_id = await self.an_open_job(harness)
        sync = self.wire(harness, "finish", {"AMS 1 Tray 1": 248.41})

        closed = await sync.execute()

        assert len(closed) == 1
        [job] = await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)
        assert job.state is PrintJobState.FINISHED
        assert job.reported_usage == {a_tray(1): Grams.of("248.41")}
        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of("751.59")

    async def test_a_machine_already_printing_at_startup_gets_a_row(self, harness: Harness) -> None:
        """The reload mid-print, which every options change performs. The machine is
        printing, its start fired in front of nobody, and `async_track_state_change_event`
        only ever fires for *future* changes — so without this pass the ledger would stay
        blind to that print until it ended, and then discard its ending too."""
        spool_id = await a_spool(harness.ledger)
        await harness.ledger.use_cases.mount_spool.execute(spool_id, a_tray(1))
        sync = self.wire(harness, "running", {"AMS 1 Tray 1": 291.42})

        reconciled = await sync.execute()

        assert len(reconciled) == 1
        [job] = await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)
        assert job.state is PrintJobState.RUNNING

    async def test_a_machine_printing_a_job_already_open_is_left_alone(
        self, harness: Harness
    ) -> None:
        """The pass runs on every startup, and most of those find a print already tracked.
        A second row here would charge one print twice, once per restart."""
        await self.an_open_job(harness)
        sync = self.wire(harness, "running", {"AMS 1 Tray 1": 291.42})

        assert await sync.execute() == []
        assert len(await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)) == 1

    async def test_an_idle_machine_with_nothing_open_writes_nothing(self, harness: Harness) -> None:
        """Every restart runs this pass, and the reference machine's captured resting
        state *is* `finish`. Minting a job here would charge a print that never ran —
        once per restart, forever."""
        spool_id = await a_spool(harness.ledger)
        await harness.ledger.use_cases.mount_spool.execute(spool_id, a_tray(1))
        sync = self.wire(harness, "finish", {"AMS 1 Tray 1": 248.41})

        assert await sync.execute() == []

        assert await SqlitePrintJobRepository(harness.ledger.database).list_recent(10) == []
        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of(1000)

    async def test_a_print_still_under_way_is_left_open(self, harness: Harness) -> None:
        """Restarting mid-print is ordinary. The row stays RUNNING and its ending arrives
        later, by whichever route speaks first."""
        spool_id = await self.an_open_job(harness)
        sync = self.wire(harness, "running", {"AMS 1 Tray 1": 248.41})

        assert await sync.execute() == []

        [job] = await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)
        assert job.state is PrintJobState.RUNNING
        detail = await harness.ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of(1000)

    async def test_a_failure_nobody_saw_opens_its_review(self, harness: Harness) -> None:
        """The pass is not only about money. An interrupted print owes the user a
        decision, and a restart must not be what loses it."""
        await self.an_open_job(harness)
        sync = self.wire(harness, "failed", {"AMS 1 Tray 1": 248.41})

        assert len(await sync.execute()) == 1

        [review] = await SqliteReviewRepository(harness.ledger.database).list_pending()
        assert review.reason is ReviewReason.FAILED

    async def test_an_unreadable_status_closes_nothing(self, harness: Harness) -> None:
        """A sensor that is unavailable has not said the print stopped. Absence of data
        is not absence of a job (docs/03 §3.8)."""
        await self.an_open_job(harness)
        sync = self.wire(harness, STATE_UNAVAILABLE, {"AMS 1 Tray 1": 248.41})

        assert await sync.execute() == []

        [job] = await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)
        assert job.state is PrintJobState.RUNNING


class TestUnload:
    """The composition root's unload path, driven against the harness."""

    async def test_unload_detaches_the_printer_before_closing_the_runtime(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Home Assistant runs `async_on_unload` callbacks only after `async_unload_entry`
        returns, so a gateway detached only that way keeps delivering tray events into a
        database the unload already closed. Unload must detach first, itself — and the
        registered callback, firing later, must find nothing left to do."""
        plant_registry(harness.hass, REGISTRY_ROWS)
        gateway = BambuLabGateway(as_hass(harness.hass))
        gateway.subscribe(harness.ledger.use_cases.detect_spool.execute)
        order: list[str] = []
        original_close = harness.runtime.async_close

        def detach() -> None:
            order.append("detach")
            gateway.detach()

        async def close() -> None:
            order.append("close")
            await original_close()

        harness.runtime.detach_printer = detach
        monkeypatch.setattr(harness.runtime, "async_close", close)
        harness.entry.async_on_unload(gateway.detach)  # the safety net, as setup wires it

        unloaded = await async_unload_entry(
            as_hass(harness.hass), cast(LedgerConfigEntry, harness.entry)
        )

        assert unloaded
        assert order == ["detach", "close"]
        assert harness.hass.bus.listeners == []
        # The safety net has not fired yet — Home Assistant runs it only after unload
        # returns — and when it does, the second detach is a no-op.
        for callback in harness.entry.unload_callbacks:
            callback()
        assert harness.hass.bus.listeners == []


class TestFiguresAreForwardedWhileThePrintRuns:
    """The gateway held every mid-print reading and forwarded none of them.

    That was sound only while every job was guaranteed an ending to report on. A connection
    that goes quiet across a finish leaves the row open and the held figures die with the
    process or are overwritten by the next print — which is how 62.23 g across three trays
    reached Home Assistant, sat in memory, and were never written anywhere the user could
    see (docs/12-field-notes.md).
    """

    def subscribed(self, hass: FakeHass) -> RecordingPrintListener:
        listener = RecordingPrintListener()
        BambuLabGateway(as_hass(hass)).subscribe_jobs(listener)
        return listener

    def observations(self, listener: RecordingPrintListener) -> list[PrintPlanObserved]:
        return [e for e in listener.received if isinstance(e, PrintPlanObserved)]

    async def test_a_reading_that_names_trays_is_forwarded(self) -> None:
        hass = bambu_hass()
        listener = self.subscribed(hass)

        fire_weight_change(hass, {"AMS 1 Tray 1": 50.82, "AMS 1 Tray 2": 7.65})
        await hass.drain()

        [observed] = self.observations(listener)
        assert observed.printer == A_PRINTER
        assert observed.plan == {
            a_tray(1): Grams.of("50.82"),
            a_tray(2): Grams.of("7.65"),
        }

    async def test_it_carries_the_job_name_so_a_lagging_row_can_be_corrected(self) -> None:
        """The file sensor is republished after the start, so the row may have opened with
        the previous print's name. Every observation carries the current one."""
        hass = bambu_hass()
        listener = self.subscribed(hass)

        fire_weight_change(hass, {"AMS 1 Tray 1": 31.33})
        await hass.drain()

        [observed] = self.observations(listener)
        assert observed.name == JOB_NAME

    async def test_a_reading_with_no_tray_keys_is_silence(self) -> None:
        """The other half of every burst. It leaves the held reading standing and must not
        travel — an empty plan written over a real one is the defect, not the fix."""
        hass = bambu_hass()
        listener = self.subscribed(hass)

        fire_weight_change(hass, {})
        await hass.drain()

        assert self.observations(listener) == []

    async def test_the_same_reading_twice_is_forwarded_once(self) -> None:
        """Upstream republishes without changing anything; the dedupe that protects the
        held reading protects the ledger's writes too."""
        hass = bambu_hass()
        listener = self.subscribed(hass)

        fire_weight_change(hass, {"AMS 1 Tray 1": 31.33})
        await hass.drain()
        fire_weight_change(hass, {"AMS 1 Tray 1": 31.33})
        await hass.drain()

        assert len(self.observations(listener)) == 1

    async def test_a_changed_reading_is_forwarded_again(self) -> None:
        """Because the row must never be further behind than the last thing the machine
        said."""
        hass = bambu_hass()
        listener = self.subscribed(hass)

        fire_weight_change(hass, {"AMS 1 Tray 1": 10.0})
        await hass.drain()
        fire_weight_change(hass, {"AMS 1 Tray 1": 31.33})
        await hass.drain()

        assert [o.plan for o in self.observations(listener)] == [
            {a_tray(1): Grams.of(10)},
            {a_tray(1): Grams.of("31.33")},
        ]
