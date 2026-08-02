"""The Home Assistant adapter layer, driven against a real ledger and a hand-rolled hass.

`pytest-homeassistant-custom-component` cannot even be imported on Windows (it drags in
`fcntl`), and nothing here needs it: the adapters touch a narrow slice of Home Assistant —
`hass.data`, the service registry, the websocket command table, the event bus — and each
slice is faked to the contract read from the installed Home Assistant source.

Everything *below* the adapters is real. The same `build_ledger` the application suite
uses wires actual use cases onto an actual SQLite file, so every assertion about a payload
here is an assertion about the actual ledger, not about a mock of our own code.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
import voluptuous as vol
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import Event, HassJob, HomeAssistant, ServiceCall, State
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util.hass_dict import HassDict

from custom_components.filament_ledger.application.query import LedgerSnapshot
from custom_components.filament_ledger.application.register_spool import RegisterSpoolCommand
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import SpoolId
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.infrastructure.ha.runtime import LedgerRuntime
from custom_components.filament_ledger.infrastructure.persistence.database import run_inline

from ..application.conftest import Ledger, build_ledger


@dataclass
class BusListener:
    """One registration made through `async_listen`, exactly as the bus stores it."""

    event_type: str
    listener: Callable[[Event[dict[str, object]]], None]
    event_filter: Callable[[dict[str, object]], bool] | None


class FakeBus:
    """The slices of the Home Assistant event bus the adapters touch.

    `async_fire` records for assertions *and* dispatches to listeners the way the real
    bus does — filter on the event data first, then the listener with a real `Event` —
    which is what lets `async_track_state_change_event` run its production machinery
    against this fake.
    """

    def __init__(self) -> None:
        self.fired: list[tuple[str, dict[str, object] | None]] = []
        self.listeners: list[BusListener] = []

    def async_fire(self, event_type: str, event_data: dict[str, object] | None = None) -> None:
        self.fired.append((event_type, event_data))
        event = Event(event_type, event_data or {})
        for entry in list(self.listeners):
            if entry.event_type != event_type:
                continue
            if entry.event_filter is not None and not entry.event_filter(event.data):
                continue
            entry.listener(event)

    def async_listen(
        self,
        event_type: str,
        listener: Callable[[Event[dict[str, object]]], None],
        event_filter: Callable[[dict[str, object]], bool] | None = None,
    ) -> Callable[[], None]:
        entry = BusListener(event_type, listener, event_filter)
        self.listeners.append(entry)

        def remove() -> None:
            self.listeners.remove(entry)

        return remove

    def named(self, event_type: str) -> list[dict[str, object] | None]:
        return [data for name, data in self.fired if name == event_type]


class FakeStates:
    """The state-machine slice the printer gateway reads: `get` by entity id."""

    def __init__(self) -> None:
        self.by_entity_id: dict[str, State] = {}

    def get(self, entity_id: str) -> State | None:
        return self.by_entity_id.get(entity_id)


@dataclass
class FakeConfigEntry:
    """What the adapters read off a config entry: `runtime_data`, identity, settings."""

    entry_id: str = "ledger-entry"
    source: str = "user"
    data: dict[str, object] = field(default_factory=dict)
    options: dict[str, object] = field(default_factory=dict)
    runtime_data: LedgerRuntime | None = None
    unload_callbacks: list[Callable[[], None]] = field(default_factory=list)

    def async_on_unload(self, func: Callable[[], None]) -> Callable[[], None]:
        self.unload_callbacks.append(func)
        return func


class FakeFlowProgress:
    """`ConfigFlow.async_set_unique_id` asks the flow manager about flows in progress."""

    def async_progress_by_handler(
        self,
        handler: str,
        include_uninitialized: bool = False,
        match_context: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        return []


class FakeConfigEntries:
    """The registry surface the adapters and the config flow read."""

    def __init__(self) -> None:
        self.loaded: list[FakeConfigEntry] = []
        self.known: dict[str, FakeConfigEntry] = {}
        self.by_unique_id: dict[tuple[str, str], FakeConfigEntry] = {}
        self.flow = FakeFlowProgress()
        self.unloaded_platforms: list[tuple[str, list[str]]] = []

    def async_loaded_entries(self, domain: str) -> list[FakeConfigEntry]:
        return list(self.loaded)

    async def async_unload_platforms(
        self, entry: FakeConfigEntry, platforms: Iterable[str]
    ) -> bool:
        """`async_unload_entry` forwards platform teardown here; the fake only records
        that it happened and reports success."""
        self.unloaded_platforms.append((entry.entry_id, list(platforms)))
        return True

    def async_get_known_entry(self, entry_id: str) -> FakeConfigEntry:
        return self.known[entry_id]

    def async_entry_for_domain_unique_id(
        self, domain: str, unique_id: str
    ) -> FakeConfigEntry | None:
        return self.by_unique_id.get((domain, unique_id))


class FakeServices:
    """The service registry surface `services.py` touches: register once, look up later."""

    def __init__(self) -> None:
        self.registered: dict[
            tuple[str, str], tuple[Callable[[ServiceCall], Awaitable[None]], vol.Schema]
        ] = {}

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.registered

    def async_register(
        self,
        domain: str,
        service: str,
        service_func: Callable[[ServiceCall], Awaitable[None]],
        schema: vol.Schema | None = None,
    ) -> None:
        assert schema is not None, "every ledger service declares a schema"
        self.registered[(domain, service)] = (service_func, schema)


@dataclass
class FakeHttp:
    """Records static path registrations instead of mounting them on a router."""

    static_paths: list[StaticPathConfig] = field(default_factory=list)

    async def async_register_static_paths(self, configs: Iterable[StaticPathConfig]) -> None:
        self.static_paths.extend(configs)


class FakeHass:
    """The minimum HomeAssistant surface the adapter layer actually touches.

    `data` is a real `HassDict` because that is the contract: Home Assistant's own
    `frontend` and `websocket_api` modules index it directly, and these tests let them.
    """

    def __init__(self) -> None:
        self.data = HassDict()
        self.bus = FakeBus()
        self.states = FakeStates()
        self.services = FakeServices()
        self.config_entries = FakeConfigEntries()
        self.http = FakeHttp()
        self.background_tasks: list[asyncio.Task[None]] = []

    def async_run_hass_job(
        self, job: HassJob[[Event[dict[str, object]]], object], event: Event[dict[str, object]]
    ) -> None:
        """Where the state-change tracker dispatches each matched event. The gateway's
        action is a `@callback`, so production runs it synchronously in the loop — and
        so does this."""
        job.target(event)

    def async_create_background_task(
        self, target: Coroutine[object, object, None], name: str, eager_start: bool = True
    ) -> asyncio.Task[None]:
        """Where `websocket_api.async_response` schedules every handler in production."""
        task = asyncio.get_running_loop().create_task(target)
        self.background_tasks.append(task)
        return task

    async def drain(self) -> None:
        """Wait for every scheduled websocket handler, the way the event loop would."""
        while self.background_tasks:
            await self.background_tasks.pop(0)


def as_hass(fake: FakeHass) -> HomeAssistant:
    """The adapters annotate `HomeAssistant` but only ever touch the surface `FakeHass`
    provides; this cast is the suite saying so out loud, in exactly one place."""
    return cast(HomeAssistant, fake)


@dataclass
class StubCoordinator:
    """`DataUpdateCoordinator`, reduced to what the runtime and the entities touch.

    `async_request_refresh` refreshes *immediately* through the real queries. The
    production debouncer is a scheduling concern; these tests are about what a refresh
    produces and who hears about it.
    """

    update: Callable[[], Awaitable[LedgerSnapshot]]
    data: LedgerSnapshot | None = None
    last_update_success: bool = True
    refresh_count: int = 0
    listeners: list[Callable[[], None]] = field(default_factory=list)

    async def async_request_refresh(self) -> None:
        self.refresh_count += 1
        self.data = await self.update()
        for listener in list(self.listeners):
            listener()

    def async_add_listener(
        self, update_callback: Callable[[], None], context: object = None
    ) -> Callable[[], None]:
        self.listeners.append(update_callback)

        def unsubscribe() -> None:
            self.listeners.remove(update_callback)

        return unsubscribe


@dataclass
class Harness:
    """A fake hass with one real ledger installed, exactly as `async_setup_entry` leaves it."""

    hass: FakeHass
    ledger: Ledger
    runtime: LedgerRuntime
    entry: FakeConfigEntry
    coordinator: StubCoordinator


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    ledger = await build_ledger(tmp_path, run_inline)
    coordinator = StubCoordinator(update=ledger.use_cases.queries.snapshot)
    runtime = LedgerRuntime(
        database=ledger.database,
        use_cases=ledger.use_cases,
        coordinator=cast(DataUpdateCoordinator[LedgerSnapshot], coordinator),
        # No printer gateway in the harness by default; tests that wire one replace this.
        detach_printer=lambda: None,
        default_opening_weight_g=1000,
        default_core_weight_g=250,
    )
    # Published exactly as the composition root publishes it: on the entry, which the
    # adapters resolve through `hass.config_entries.async_loaded_entries(DOMAIN)`.
    entry = FakeConfigEntry(runtime_data=runtime)
    hass = FakeHass()
    hass.config_entries.loaded.append(entry)
    hass.config_entries.known[entry.entry_id] = entry
    yield Harness(hass=hass, ledger=ledger, runtime=runtime, entry=entry, coordinator=coordinator)
    await ledger.database.close()


async def a_spool(ledger: Ledger, **overrides: object) -> SpoolId:
    """Register a spool through the real use case: 1000 g of black PLA on a 250 g reel."""
    settings: dict[str, object] = {
        "material": Material.of(MaterialKind.PLA),
        "colour": Colour.parse("000000"),
        "opening_weight": Grams.of(1000),
        "core_weight": Grams.of(250),
        "vendor": "Bambu Lab",
    } | overrides
    command = RegisterSpoolCommand(**settings)  # type: ignore[arg-type]
    return await ledger.use_cases.register_spool.execute(command)
