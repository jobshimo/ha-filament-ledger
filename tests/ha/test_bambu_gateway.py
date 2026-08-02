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
from pathlib import Path
from typing import cast

import pytest
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er

from custom_components.filament_ledger import async_unload_entry
from custom_components.filament_ledger.domain.event import SpoolMounted, UnknownSpoolDetected
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.identifiers import SlotIndex, SpoolId, TagUid
from custom_components.filament_ledger.domain.value.location import AmsSlot, Location
from custom_components.filament_ledger.domain.value.tray_reading import TrayReading
from custom_components.filament_ledger.infrastructure.ha.bambu_gateway import BambuLabGateway
from custom_components.filament_ledger.infrastructure.ha.runtime import LedgerConfigEntry

from ..application.conftest import Ledger
from .conftest import FakeHass, Harness, a_spool, as_hass

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bambu"

REGISTRY_ROWS: list[dict[str, str]] = json.loads(
    (FIXTURES / "entity_registry.json").read_text(encoding="utf-8")
)
TRAY_ATTRIBUTES: dict[str, dict[str, object]] = json.loads(
    (FIXTURES / "tray_attributes.json").read_text(encoding="utf-8")
)

TRAY_1 = "sensor.a1_00000000testser_ams_1_bandeja_1"
TRAY_2 = "sensor.a1_00000000testser_ams_1_bandeja_2"
TRAY_4 = "sensor.a1_00000000testser_ams_1_bandeja_4"

TRAY_1_TAG = TagUid("3C45C3DB00000100")
TRAY_2_TAG = TagUid("3CDDA20200000100")
TRAY_4_TAG = TagUid("4289A97100000100")


@dataclass(frozen=True)
class FakeRegistryEntry:
    """The slice of `er.RegistryEntry` that discovery reads."""

    entity_id: str
    platform: str
    unique_id: str
    translation_key: str


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


def plant_registry(hass: FakeHass, rows: list[dict[str, str]]) -> None:
    registry = FakeEntityRegistry([FakeRegistryEntry(**row) for row in rows])
    hass.data[er.DATA_REGISTRY] = cast(er.EntityRegistry, registry)


def tray_state(entity_id: str, attributes: dict[str, object] | None = None) -> State:
    """A tray sensor state. Only availability is read off the state string itself, so the
    filament name stands in for the uncaptured native value."""
    payload = TRAY_ATTRIBUTES[entity_id] if attributes is None else attributes
    return State(entity_id, str(payload.get("name", "loaded")), payload)


def bambu_hass(rows: list[dict[str, str]] | None = None) -> FakeHass:
    """A hass holding the reference instance: registry rows planted, tray states loaded."""
    hass = FakeHass()
    plant_registry(hass, REGISTRY_ROWS if rows is None else rows)
    for entity_id in TRAY_ATTRIBUTES:
        hass.states.by_entity_id[entity_id] = tray_state(entity_id)
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
