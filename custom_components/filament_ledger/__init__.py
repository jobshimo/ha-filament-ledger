"""Filament Ledger — a Home Assistant custom integration.

The composition root. Wiring happens in exactly one place, here, during
`async_setup_entry`. Nothing else constructs a dependency, which is precisely what makes
every use case testable with in-memory fakes and no patching.

**Home Assistant is imported inside the setup functions, not at module level.** Importing
any submodule executes this file first, so a module-level `import homeassistant` here would
make the domain unimportable without Home Assistant installed — and the domain being
importable on its own is not a stylistic preference, it is the claim
docs/03-architecture.md §3.2 makes and that `tests/architecture` exists to keep true.

The CI job that installs everything *except* Home Assistant caught this on its first run.
That is the job earning its place.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .domain.value.print_event import PrintEvent
    from .domain.value.tray_reading import TrayReading
    from .infrastructure.ha.runtime import LedgerConfigEntry

LOGGER = logging.getLogger(__name__)

# Plain strings rather than the `Platform` enum, so this module needs nothing from Home
# Assistant at import time. `async_forward_entry_setups` accepts either.
PLATFORMS: list[str] = ["sensor"]

# The ledger is push-shaped: it changes when somebody does something. This interval is a
# safety net for a missed refresh, not the mechanism.
SCAN_INTERVAL = timedelta(minutes=15)


async def async_setup_entry(hass: HomeAssistant, entry: LedgerConfigEntry) -> bool:
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    from .application.adjust_spool import AdjustSpool, DiscardFilament
    from .application.delete_spool import DeleteSpool, RestoreSpool
    from .application.detect_spool import DetectSpool
    from .application.move_spool import EditSpoolDetails, MountSpool, UnmountSpool
    from .application.query import Queries
    from .application.reassign_movement import ReassignMovement
    from .application.reconcile_spool import ReconcileSpool
    from .application.record_print_consumption import RecordPrintConsumption
    from .application.register_spool import RegisterSpool
    from .application.review_queue import ApproveReview, DismissReview, OpenPendingReview
    from .application.track_print_job import TrackPrintJob
    from .application.use_cases import UseCases
    from .application.void_movement import RestoreMovement, VoidMovement
    from .const import (
        CONF_ANOMALY_THRESHOLD,
        CONF_AUTO_MOUNT_ON_RFID,
        CONF_DEFAULT_CORE_WEIGHT,
        CONF_DEFAULT_OPENING_WEIGHT,
        DATABASE_FILENAME,
        DEFAULT_ANOMALY_THRESHOLD_PCT,
        DEFAULT_AUTO_MOUNT_ON_RFID,
        DEFAULT_CORE_WEIGHT_G,
        DEFAULT_OPENING_WEIGHT_G,
        DOMAIN,
    )
    from .domain.service.anomaly_detector import AnomalyDetector
    from .domain.service.confidence_evaluator import ConfidenceEvaluator
    from .infrastructure.estimation.linear_progress_estimator import LinearProgressEstimator
    from .infrastructure.ha.bambu_gateway import BambuLabGateway
    from .infrastructure.ha.event_bridge import HomeAssistantEventBus
    from .infrastructure.ha.panel import async_register_panel
    from .infrastructure.ha.printer_state import ReadPrinterState
    from .infrastructure.ha.runtime import LedgerRuntime
    from .infrastructure.ha.services import async_register_services
    from .infrastructure.ha.tray_sync import TraySync
    from .infrastructure.ha.websocket_api import async_register_commands
    from .infrastructure.persistence.database import Database
    from .infrastructure.persistence.movement_repository import SqliteMovementRepository
    from .infrastructure.persistence.movement_void_repository import (
        SqliteMovementVoidRepository,
    )
    from .infrastructure.persistence.print_job_repository import SqlitePrintJobRepository
    from .infrastructure.persistence.printer_adoption import adopt_unidentified_trays
    from .infrastructure.persistence.review_repository import SqliteReviewRepository
    from .infrastructure.persistence.spool_repository import SqliteSpoolRepository
    from .infrastructure.system_clock import SystemClock

    settings = {**entry.data, **entry.options}

    database = await Database.open(hass.config.path(DATABASE_FILENAME), hass.async_add_executor_job)
    version = await database.migrate()
    LOGGER.debug("database at schema version %s", version)

    spools = SqliteSpoolRepository(database)
    movements = SqliteMovementRepository(database)
    # Its own repository because it is its own thing: a status record *about* a movement,
    # never a movement. That separation is what lets `MovementRepository` go on exposing
    # no update and no delete (docs/adr/0007).
    voids = SqliteMovementVoidRepository(database)
    jobs = SqlitePrintJobRepository(database)
    reviews = SqliteReviewRepository(database)
    clock = SystemClock()
    events = HomeAssistantEventBus(hass)

    threshold = Decimal(
        settings.get(CONF_ANOMALY_THRESHOLD, DEFAULT_ANOMALY_THRESHOLD_PCT)
    ) / Decimal(100)
    anomalies = AnomalyDetector(reconciliation_delta_ratio=threshold)

    queries = Queries(
        spools=spools,
        movements=movements,
        reviews=reviews,
        jobs=jobs,
        voids=voids,
        clock=clock,
        confidence=ConfidenceEvaluator(),
        anomalies=anomalies,
    )

    # Built once, used twice: the websocket approves against it indirectly, and
    # `TrackPrintJob` opens through it when the gateway reports an interrupted job.
    # `LinearProgressEstimator` stands alone until Phase 4 brings the G-code strategy
    # and a composite to choose between them.
    open_pending_review = OpenPendingReview(
        jobs, reviews, spools, LinearProgressEstimator(), clock, events, database
    )

    # UC-04, the only fully automatic deduction (Q4, closed — docs/12-field-notes.md).
    # `TrackPrintJob` hands it every FINISHED job; the review queue is its degradation
    # path for anything the printer could not attribute.
    record_print_consumption = RecordPrintConsumption(
        jobs, spools, movements, open_pending_review, clock, events, database, anomalies
    )

    # `database` is passed where a `UnitOfWork` is expected: the connection is the thing
    # that can make a multi-write sequence atomic, so it is the thing that implements it.
    use_cases = UseCases(
        register_spool=RegisterSpool(spools, movements, clock, events, database),
        reconcile_spool=ReconcileSpool(spools, movements, clock, events, database, anomalies),
        discard_filament=DiscardFilament(spools, movements, clock, events, database, anomalies),
        adjust_spool=AdjustSpool(spools, movements, clock, events, database, anomalies),
        mount_spool=MountSpool(spools, clock, events, database),
        unmount_spool=UnmountSpool(spools, events, database),
        # `auto_mount` is read here, once: an options change reloads this entry (see
        # `_reload_on_options_change` below), so the rebuilt use case always carries the
        # current setting. The printer gateway below is what drives it.
        detect_spool=DetectSpool(
            spools,
            events,
            database,
            auto_mount=bool(settings.get(CONF_AUTO_MOUNT_ON_RFID, DEFAULT_AUTO_MOUNT_ON_RFID)),
        ),
        edit_spool_details=EditSpoolDetails(spools, database),
        # What the gateway's job events drive: starts become RUNNING rows, interrupted
        # endings become reviews, and a finish goes to UC-04 for the automatic
        # deduction. Classification arrives on the event itself (Q1, closed).
        track_print_job=TrackPrintJob(
            jobs=jobs,
            open_pending_review=open_pending_review,
            record_print_consumption=record_print_consumption,
            clock=clock,
            uow=database,
        ),
        record_print_consumption=record_print_consumption,
        open_pending_review=open_pending_review,
        approve_review=ApproveReview(
            reviews, spools, movements, clock, events, database, anomalies
        ),
        dismiss_review=DismissReview(reviews, clock, events, database),
        # v1.0's corrections. Each one brackets its read-compute-write in the same unit of
        # work every other use case uses, and publishes after the commit.
        reassign_movement=ReassignMovement(
            spools, movements, voids, clock, events, database, anomalies
        ),
        void_movement=VoidMovement(spools, movements, voids, clock, events, database, anomalies),
        restore_movement=RestoreMovement(
            spools, movements, voids, clock, events, database, anomalies
        ),
        delete_spool=DeleteSpool(spools, clock, events, database),
        restore_spool=RestoreSpool(spools, events, database),
        queries=queries,
    )

    coordinator = DataUpdateCoordinator(
        hass,
        LOGGER,
        config_entry=entry,
        name=DOMAIN,
        update_interval=SCAN_INTERVAL,
        update_method=queries.snapshot,
    )

    # The inbound half of the printer boundary (docs/05 §5.8). Subscribed before the
    # reconciliation pass below, so a tray change during startup lands in the same
    # idempotent use case instead of falling into a gap.
    gateway = BambuLabGateway(hass)

    # Migration 0007 could not name the printer — it runs with a bare SQLite connection —
    # so it wrote a placeholder into every mounted spool's tray reference. Discovery has
    # just resolved the real serial, so the rows learn it here, **before** the
    # reconciliation pass below asks which spool is in which tray. Get the order wrong and
    # the pass looks up trays under the new name, finds them empty, and mounts a second
    # spool into every one of them.
    await adopt_unidentified_trays(database, gateway.printer_serial)

    # The reconciliation pass as an object: startup runs it once below, and the panel's
    # sync button and the `sync_trays` service run the very same wiring on demand.
    sync_trays = TraySync(gateway=gateway, detect_spool=use_cases.detect_spool, spools=spools)

    # The Printer tab's read-only glance over the same gateway (docs/14 §14.5). It runs no
    # use case and writes nothing — the sync above stays the one mutation path. `queries`
    # rides along for the accumulated-hours total, which is a sum over the ledger's own job
    # rows rather than anything the machine reports.
    printer = ReadPrinterState(gateway=gateway, spools=spools, queries=queries)

    entry.runtime_data = LedgerRuntime(
        database=database,
        use_cases=use_cases,
        coordinator=coordinator,
        detach_printer=gateway.detach,
        default_opening_weight_g=int(
            settings.get(CONF_DEFAULT_OPENING_WEIGHT, DEFAULT_OPENING_WEIGHT_G)
        ),
        default_core_weight_g=int(settings.get(CONF_DEFAULT_CORE_WEIGHT, DEFAULT_CORE_WEIGHT_G)),
        sync_trays=sync_trays,
        printer=printer,
    )

    async def _tray_changed(reading: TrayReading) -> None:
        await use_cases.detect_spool.execute(reading)
        # Every mutation path refreshes the overview — the services and websocket
        # commands do it explicitly, and a physical tray change is a mutation path too.
        await coordinator.async_request_refresh()

    async def _print_event(event: PrintEvent) -> None:
        await use_cases.track_print_job.execute(event)
        # A job ending can open a review, and the pending-reviews sensor rides the same
        # snapshot as the spools — so a job event is a mutation path too.
        await coordinator.async_request_refresh()

    gateway.subscribe(_tray_changed)
    gateway.subscribe_jobs(_print_event)
    # The safety net for setup-failure paths: an exception below this line still detaches.
    # A clean unload detaches earlier — see `async_unload_entry` — and `detach` is
    # idempotent, so this registration running afterwards is a no-op.
    entry.async_on_unload(gateway.detach)

    # One reconciliation pass: the printer does not replay what happened while Home
    # Assistant was off (the port's own contract), so the drift accumulated in the dark —
    # a spool loaded by hand, a reel swapped — is healed here, before the first refresh
    # reports the ledger as current. `DetectSpool` is idempotent, so replaying an
    # unchanged tray writes nothing. The per-slot outcome the pass reports is the panel's
    # business, not startup's, so it is ignored here — a handful of bounded reads is a
    # cheaper price than a second code path through the same reconciliation.
    await sync_trays.execute()

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async_register_commands(hass)
    async_register_services(hass)
    await async_register_panel(hass)

    entry.async_on_unload(entry.add_update_listener(_reload_on_options_change))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LedgerConfigEntry) -> bool:
    from .infrastructure.ha.panel import async_remove_panel

    # First, before anything is torn down: Home Assistant runs the `async_on_unload`
    # callbacks only after this function returns, and a tray change in that window would
    # schedule `DetectSpool` against the database closed below. Detaching here closes the
    # window; the registered callback running again later finds nothing left to do.
    entry.runtime_data.detach_printer()

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        # The sidebar panel belongs to this entry's lifecycle. Leaving it registered
        # would leave a dead menu item pointing at a runtime that no longer exists —
        # and setup re-registers it, so a reload comes back whole.
        async_remove_panel(hass)
        await entry.runtime_data.async_close()
    return unloaded


async def _reload_on_options_change(hass: HomeAssistant, entry: LedgerConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
