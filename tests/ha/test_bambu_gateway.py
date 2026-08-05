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
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er

from custom_components.filament_ledger import async_unload_entry
from custom_components.filament_ledger.domain.event import (
    ReviewOpened,
    SpoolMounted,
    UnknownSpoolDetected,
)
from custom_components.filament_ledger.domain.model.pending_review import ReviewCharge
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import SlotIndex, SpoolId, TagUid
from custom_components.filament_ledger.domain.value.location import AmsSlot, Location
from custom_components.filament_ledger.domain.value.percentage import Percentage
from custom_components.filament_ledger.domain.value.print_event import (
    PrintEnded,
    PrintEvent,
    PrintStarted,
)
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.domain.value.review import ReviewReason
from custom_components.filament_ledger.domain.value.tray_reading import TrayReading
from custom_components.filament_ledger.infrastructure.ha.bambu_gateway import (
    UNKNOWN_JOB_NAME,
    BambuLabGateway,
)
from custom_components.filament_ledger.infrastructure.ha.runtime import LedgerConfigEntry
from custom_components.filament_ledger.infrastructure.persistence.print_job_repository import (
    SqlitePrintJobRepository,
)
from custom_components.filament_ledger.infrastructure.persistence.review_repository import (
    SqliteReviewRepository,
)

from ..application.conftest import Ledger
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

WEIGHT = "sensor.a1_00000000testser_peso_de_la_impresion"
STATUS = "sensor.a1_00000000testser_estado_de_la_impresion"
CURRENT_LAYER = "sensor.a1_00000000testser_capa_actual"
TOTAL_LAYERS = "sensor.a1_00000000testser_cantidad_total_de_capas"
PROGRESS = "sensor.a1_00000000testser_progreso_de_la_impresion"
GCODE_FILE = "sensor.a1_00000000testser_archivo_gcode_descargado"
PRINT_ERROR = "binary_sensor.a1_00000000testser_error_de_la_impresion"

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

        assert sorted(readings) == [SlotIndex(1), SlotIndex(2), SlotIndex(3), SlotIndex(4)]

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

        assert BambuLabGateway(as_hass(hass)).current_job_status().remaining_minutes == 97

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
        assert readings[SlotIndex(1)].colour == Colour(0x5E, 0x43, 0xB7, 0xFF)


class TestCurrentTrays:
    async def test_a_tagged_tray_translates_completely(self) -> None:
        """Tag, presence and every hint — the register form pre-fills from these."""
        readings = await BambuLabGateway(as_hass(bambu_hass())).current_trays()

        assert readings[SlotIndex(1)] == TrayReading(
            slot=SlotIndex(1),
            tag=TRAY_1_TAG,
            empty=False,
            name="Bambu PLA Basic",
            material="PLA",
            colour=Colour(0x5E, 0x43, 0xB7, 0xFF),
        )

    async def test_sixteen_zeros_is_an_absent_tag_not_an_identity(self) -> None:
        """Tray 3 holds a third-party or refilled spool: physically present, no readable
        tag. Treating the sentinel as identity would merge every untagged spool the owner
        ever buys into one (docs/12-field-notes.md)."""
        readings = await BambuLabGateway(as_hass(bambu_hass())).current_trays()

        assert readings[SlotIndex(3)].tag is None
        assert readings[SlotIndex(3)].empty is False

    async def test_the_rrggbbaa_colour_hint_survives_translation(self) -> None:
        """The printer speaks `#RRGGBBAA` — exactly the domain's storage format, so the
        alpha channel crosses the boundary intact."""
        readings = await BambuLabGateway(as_hass(bambu_hass())).current_trays()

        assert readings[SlotIndex(4)].colour == Colour(255, 255, 255, 255)

    @pytest.mark.parametrize("unusable", ["unavailable", "unknown"])
    async def test_an_unusable_tray_sensor_is_omitted_not_reported_empty(
        self, unusable: str
    ) -> None:
        """Absence of data is not absence of a spool: reporting a blinked sensor as an
        empty tray would unmount whatever the ledger has in that slot."""
        hass = bambu_hass()
        hass.states.by_entity_id[TRAY_2] = State(TRAY_2, unusable)

        readings = await BambuLabGateway(as_hass(hass)).current_trays()

        assert sorted(readings) == [SlotIndex(1), SlotIndex(3), SlotIndex(4)]

    async def test_a_missing_state_is_omitted(self) -> None:
        hass = bambu_hass()
        del hass.states.by_entity_id[TRAY_2]

        readings = await BambuLabGateway(as_hass(hass)).current_trays()

        assert sorted(readings) == [SlotIndex(1), SlotIndex(3), SlotIndex(4)]

    async def test_an_emptied_tray_drops_the_leftover_hints(self) -> None:
        """The capture holds no empty tray, so this flips the one observed flag: whatever
        name, tag and colour the attributes still carry belonged to the previous occupant
        and must not survive into the reading."""
        hass = bambu_hass()
        emptied = {**TRAY_ATTRIBUTES[TRAY_1], "empty": True}
        hass.states.by_entity_id[TRAY_1] = tray_state(TRAY_1, emptied)

        readings = await BambuLabGateway(as_hass(hass)).current_trays()

        assert readings[SlotIndex(1)] == TrayReading(slot=SlotIndex(1), tag=None, empty=True)

    async def test_malformed_attributes_omit_the_slot(self) -> None:
        """No `empty` flag means the reading cannot be trusted in either direction."""
        hass = bambu_hass()
        hass.states.by_entity_id[TRAY_1] = State(TRAY_1, "loaded", {"slot": 1})

        readings = await BambuLabGateway(as_hass(hass)).current_trays()

        assert sorted(readings) == [SlotIndex(2), SlotIndex(3), SlotIndex(4)]


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
                slot=SlotIndex(4),
                tag=TRAY_4_TAG,
                empty=False,
                name="Bambu PLA Matte",
                material="PLA",
                colour=Colour(255, 255, 255, 255),
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

        assert await located(harness.ledger, spool_id) == AmsSlot(SlotIndex(1))
        unknown = harness.ledger.events.of(UnknownSpoolDetected)
        # Trays 2 and 4 carry unregistered tags; tray 3's unreadable tag asks for nothing.
        assert len(unknown) == 2
        assert UnknownSpoolDetected(tag_uid=TRAY_4_TAG, slot=SlotIndex(4)) in unknown
        assert len(await harness.ledger.use_cases.queries.overview()) == 1

    async def test_a_tray_change_with_a_known_tag_mounts_the_spool(self, harness: Harness) -> None:
        plant_registry(harness.hass, REGISTRY_ROWS)
        spool_id = await a_spool(harness.ledger, tag_uid=TRAY_2_TAG)
        gateway = BambuLabGateway(as_hass(harness.hass))
        gateway.subscribe(harness.ledger.use_cases.detect_spool.execute)

        fire_tray_change(harness.hass, TRAY_2, tray_state(TRAY_2))
        await harness.hass.drain()

        assert await located(harness.ledger, spool_id) == AmsSlot(SlotIndex(2))
        assert SpoolMounted(spool_id=spool_id, slot=SlotIndex(2)) in harness.ledger.events.published

    async def test_an_unknown_tag_is_reported_and_creates_nothing(self, harness: Harness) -> None:
        """A guessed opening weight is a fabricated number; the sighting becomes an event
        for the review queue, never a spool."""
        plant_registry(harness.hass, REGISTRY_ROWS)
        gateway = BambuLabGateway(as_hass(harness.hass))
        gateway.subscribe(harness.ledger.use_cases.detect_spool.execute)

        fire_tray_change(harness.hass, TRAY_4, tray_state(TRAY_4))
        await harness.hass.drain()

        assert await harness.ledger.use_cases.queries.overview() == []
        [event] = harness.ledger.events.of(UnknownSpoolDetected)
        assert event == UnknownSpoolDetected(tag_uid=TRAY_4_TAG, slot=SlotIndex(4))


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

        assert listener.received == [PrintStarted(name=JOB_NAME, plan=None)]

    async def test_a_start_translates_the_per_tray_plan_when_upstream_carries_it(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`AMS 1 Tray n` becomes `SlotIndex(n)` here and nowhere else (docs/05 §5.8).
        The external-spool figure has no AMS slot to land in, so it is dropped loudly."""
        hass = bambu_hass()
        hass.states.by_entity_id[WEIGHT] = print_sensor_state(
            WEIGHT,
            attributes={"AMS 1 Tray 1": 28.4, "AMS 1 Tray 2": 6.1, "External Spool": 1.2},
        )
        listener = self.subscribed(hass)

        with caplog.at_level(logging.WARNING):
            fire_job_event(hass, "event_print_started")
            await hass.drain()

        [event] = listener.received
        assert isinstance(event, PrintStarted)
        assert event.plan == {SlotIndex(1): Grams.of("28.4"), SlotIndex(2): Grams.of("6.1")}
        assert "external spool" in caplog.text

    async def test_malformed_per_tray_figures_are_skipped_not_invented(self) -> None:
        """Strings in an attribute dictionary, no schema, no version: a textual figure, a
        negative one and a second AMS are all noise — only honest rows survive."""
        hass = bambu_hass()
        hass.states.by_entity_id[WEIGHT] = print_sensor_state(
            WEIGHT,
            attributes={
                "AMS 1 Tray 1": "lots",
                "AMS 1 Tray 2": -3,
                "AMS 2 Tray 1": 7.5,
                "AMS 1 Tray 3": 5.0,
            },
        )
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_started")
        await hass.drain()

        [event] = listener.received
        assert isinstance(event, PrintStarted)
        assert event.plan == {SlotIndex(3): Grams.of(5)}

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
        hass.states.by_entity_id[WEIGHT] = print_sensor_state(
            WEIGHT,
            state=str(shape["state"]),
            attributes=cast("dict[str, object]", shape["attributes"]),
        )
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_finished")
        await hass.drain()

        [event] = listener.received
        assert isinstance(event, PrintEnded)
        assert event.outcome is PrintJobState.FINISHED
        assert event.reported_usage == {SlotIndex(4): Grams.of("296.56")}

    async def test_a_finish_captures_the_final_per_tray_figures(self) -> None:
        hass = bambu_hass()
        hass.states.by_entity_id[WEIGHT] = print_sensor_state(
            WEIGHT, attributes={"AMS 1 Tray 1": 38.2, "AMS 1 Tray 2": 9.4}
        )
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_finished")
        await hass.drain()

        [event] = listener.received
        assert isinstance(event, PrintEnded)
        assert event.outcome is PrintJobState.FINISHED
        assert event.reported_usage == {
            SlotIndex(1): Grams.of("38.2"),
            SlotIndex(2): Grams.of("9.4"),
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
            PrintEnded(outcome=PrintJobState.CANCELLED, name=UNKNOWN_JOB_NAME)
        ]

    async def test_an_event_for_another_device_never_arrives(self) -> None:
        """The bus carries every machine's events; only the printer whose sensors were
        discovered may drive this ledger. The AMS device stands in for a second printer."""
        hass = bambu_hass()
        listener = self.subscribed(hass)

        fire_job_event(hass, "event_print_started", device_id=AMS_DEVICE)
        await hass.drain()

        assert listener.received == []

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

    def wire(self, harness: Harness) -> BambuLabGateway:
        plant_registry(harness.hass, REGISTRY_ROWS)
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
        await harness.ledger.use_cases.mount_spool.execute(spool_id, SlotIndex(1))
        self.wire(harness)
        harness.hass.states.by_entity_id[WEIGHT] = State(WEIGHT, "40.51", {"AMS 1 Tray 1": 209.0})

        fire_job_event(harness.hass, "event_print_started")
        await harness.hass.drain()
        fire_job_event(harness.hass, "event_print_canceled")
        await harness.hass.drain()

        [review] = await SqliteReviewRepository(harness.ledger.database).list_pending()
        assert review.reason is ReviewReason.CANCELLED
        assert review.estimated_usage == {SlotIndex(1): Grams.of(71)}
        assert review.charges == [(SlotIndex(1), ReviewCharge(spool_id, Grams.of(71)))]
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
        await harness.ledger.use_cases.mount_spool.execute(spool_id, SlotIndex(1))
        self.wire(harness)
        fire_job_event(harness.hass, "event_print_started")
        await harness.hass.drain()
        harness.hass.states.by_entity_id[WEIGHT] = State(WEIGHT, "38.2", {"AMS 1 Tray 1": 38.2})

        fire_job_event(harness.hass, "event_print_finished")
        await harness.hass.drain()

        [job] = await SqlitePrintJobRepository(harness.ledger.database).list_recent(10)
        assert job.state is PrintJobState.FINISHED
        assert job.reported_usage == {SlotIndex(1): Grams.of("38.2")}
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
