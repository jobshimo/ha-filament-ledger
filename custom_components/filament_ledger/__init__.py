"""Filament Ledger — a Home Assistant custom integration.

The composition root. Wiring happens in exactly one place, here, during
`async_setup_entry`. Nothing else constructs a dependency, which is precisely what makes
every use case testable with in-memory fakes and no patching.

Read it from the inside out: the domain knows nothing about this file, the application layer
knows only the ports, and this is the only module that knows Home Assistant, SQLite and the
file system all exist at once.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .application.adjust_spool import AdjustSpool, DiscardFilament
from .application.move_spool import EditSpoolDetails, MountSpool, UnmountSpool
from .application.query import Queries, SpoolSummary
from .application.reconcile_spool import ReconcileSpool
from .application.register_spool import RegisterSpool
from .application.use_cases import UseCases
from .const import (
    CONF_ANOMALY_THRESHOLD,
    CONF_DEFAULT_CORE_WEIGHT,
    CONF_DEFAULT_OPENING_WEIGHT,
    DATABASE_FILENAME,
    DEFAULT_ANOMALY_THRESHOLD_PCT,
    DEFAULT_CORE_WEIGHT_G,
    DEFAULT_OPENING_WEIGHT_G,
    DOMAIN,
)
from .domain.service.anomaly_detector import AnomalyDetector
from .domain.service.confidence_evaluator import ConfidenceEvaluator
from .infrastructure.ha.event_bridge import HomeAssistantEventBus
from .infrastructure.ha.panel import async_register_panel
from .infrastructure.ha.runtime import LedgerConfigEntry, LedgerRuntime
from .infrastructure.ha.services import async_register_services
from .infrastructure.ha.websocket_api import async_register_commands
from .infrastructure.persistence.database import Database
from .infrastructure.persistence.movement_repository import SqliteMovementRepository
from .infrastructure.persistence.spool_repository import SqliteSpoolRepository
from .infrastructure.system_clock import SystemClock

LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# The ledger is push-shaped: it changes when somebody does something. This interval is a
# safety net for a missed refresh, not the mechanism.
SCAN_INTERVAL = timedelta(minutes=15)


async def async_setup_entry(hass: HomeAssistant, entry: LedgerConfigEntry) -> bool:
    settings = {**entry.data, **entry.options}

    database = await Database.open(hass.config.path(DATABASE_FILENAME), hass.async_add_executor_job)
    version = await database.migrate()
    LOGGER.debug("database at schema version %s", version)

    spools = SqliteSpoolRepository(database)
    movements = SqliteMovementRepository(database)
    clock = SystemClock()
    events = HomeAssistantEventBus(hass)

    threshold = Decimal(
        settings.get(CONF_ANOMALY_THRESHOLD, DEFAULT_ANOMALY_THRESHOLD_PCT)
    ) / Decimal(100)
    anomalies = AnomalyDetector(reconciliation_delta_ratio=threshold)
    confidence = ConfidenceEvaluator()

    queries = Queries(
        spools=spools, movements=movements, confidence=confidence, anomalies=anomalies
    )

    use_cases = UseCases(
        register_spool=RegisterSpool(spools, movements, clock, events),
        reconcile_spool=ReconcileSpool(spools, movements, clock, events, anomalies),
        discard_filament=DiscardFilament(spools, movements, clock, events, anomalies),
        adjust_spool=AdjustSpool(spools, movements, clock, events, anomalies),
        mount_spool=MountSpool(spools, clock, events),
        unmount_spool=UnmountSpool(spools, events),
        edit_spool_details=EditSpoolDetails(spools),
        queries=queries,
    )

    coordinator: DataUpdateCoordinator[list[SpoolSummary]] = DataUpdateCoordinator(
        hass,
        LOGGER,
        config_entry=entry,
        name=DOMAIN,
        update_interval=SCAN_INTERVAL,
        update_method=queries.overview,
    )

    entry.runtime_data = LedgerRuntime(
        database=database,
        use_cases=use_cases,
        coordinator=coordinator,
        default_opening_weight_g=int(
            settings.get(CONF_DEFAULT_OPENING_WEIGHT, DEFAULT_OPENING_WEIGHT_G)
        ),
        default_core_weight_g=int(settings.get(CONF_DEFAULT_CORE_WEIGHT, DEFAULT_CORE_WEIGHT_G)),
    )

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async_register_commands(hass)
    async_register_services(hass)
    await async_register_panel(hass)

    entry.async_on_unload(entry.add_update_listener(_reload_on_options_change))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LedgerConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_close()
    return unloaded


async def _reload_on_options_change(hass: HomeAssistant, entry: LedgerConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
