"""The AMS and the job lifecycle, read through `ha-bambulab` — the inbound printer boundary.

This is the only module in the project that touches another integration, and docs/05 §5.8
writes down exactly what it may touch: the entity registry, the state machine, state
change events, and `bambu_lab_event` on the bus. Nothing is imported from
`custom_components.bambu_lab` — its coordinator and config entry are internals with no
compatibility promise, and depending on them turns an upstream refactor into a corrupted
ledger.

Discovery never matches entity ids. The reference instance runs Spanish, so the tray
sensor is `sensor.…_ams_1_bandeja_1` (docs/12-field-notes.md); anything keyed on the
English string breaks for every user not running the developer's language. What *is*
stable is upstream's own identity: `platform == "bambu_lab"` plus the `translation_key` —
`tray` for the AMS trays, and the job sensors' keys listed in `PRINT_SENSOR_KEYS`. The
tray translation lives here and nowhere else, per docs/05 §5.8 — and so does the
per-tray-attribute translation: `AMS 1 Tray 1` becomes the tray reference for tray 1 of
AMS 1, and an `External Spool` figure is dropped with a warning, because the domain keys
usage by tray and inventing a fifth tray would be a lie with a number on it.

**The printer's serial comes off the job sensors' own `unique_id`s**, which upstream writes
as `<serial>_<translation_key>` — `00000000TESTSER_print_weight` in the frozen registry
fixture, anonymised but faithful in shape. That is the stable identity the domain's
`TrayRef` needs, read from evidence this repository already has rather than from a
`translation_key` nobody has confirmed. The tray sensors' `unique_id`s carry the serial too,
behind a model prefix whose boundary is not written down anywhere — so they are not where it
is read from, and trays fall back to `UNIDENTIFIED_PRINTER` when no job sensor resolved.

Job events are filtered by device. The bus carries `bambu_lab_event` for every machine
and the payload names only a device id, so the gateway keeps the id of the printer whose
job sensors it discovered — the trays hang off the AMS device, the job sensors off the
printer — and ignores everything else.

Two conscious limitations, both documented rather than discovered:

- **One printer, one AMS.** v1 targets a single ledger on a single machine; if the
  registry ever holds several AMS units or several printers, the first (by identity)
  wins and a warning names the ones ignored. The model can now *represent* a second
  machine; nothing here has learned to follow one. The ignored serials are kept rather
  than only logged, so the Printer tab can say out loud what the log was saying alone.
- **No late binding.** If `ha-bambulab` is not set up when this entry loads, the gateway
  constructs dormant: `current_trays()` is empty and both subscribe surfaces are logged
  no-ops. docs/05 §5.8 demands no startup-order tolerance, so watching the registry for
  entities appearing later is deliberately deferred — reloading this entry after the
  upstream integration appears re-runs discovery.

`event_print_error` is deliberately not a lifecycle edge: upstream fires it mid-print
while the job keeps running, and the error code it announces is read off the error
sensor when the *ending* event arrives.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from ...domain.error import InvalidValueError
from ...domain.port.printer_gateway import PrintListener, TrayListener
from ...domain.value.colour import Colour
from ...domain.value.grams import Grams
from ...domain.value.identifiers import (
    ABSENT_TAG_SENTINEL,
    UNIDENTIFIED_PRINTER,
    AmsIndex,
    PrinterSerial,
    SlotIndex,
    TagUid,
    TrayRef,
)
from ...domain.value.percentage import Percentage
from ...domain.value.print_event import PrintEnded, PrintEvent, PrintStarted
from ...domain.value.print_job_state import PrintJobState
from ...domain.value.tray_reading import TrayReading

if TYPE_CHECKING:
    from homeassistant.core import CALLBACK_TYPE

LOGGER = logging.getLogger(__name__)

UPSTREAM_PLATFORM = "bambu_lab"
TRAY_TRANSLATION_KEY = "tray"
_TRAY_MARKER = "_tray_"

# The job sensors, by upstream's own translation keys (docs/05 §5.8, docs/12). Resolved
# once at construction, same as the trays; the printer's device id is derived from these
# entries because the job events on the bus name only a device.
#
# Every key here was read off the reference instance's entity registry **before** it was
# frozen, which is the rule `FUTURE_PRINT_SENSOR_KEYS` below exists to explain. The last
# three joined in v1.4: `remaining_time` is what the Printer tab shows for a job in
# progress, and `start_time`/`end_time` are the machine's own answer to how long a print
# actually took, as opposed to how long Home Assistant took to notice it.
PRINT_SENSOR_KEYS = frozenset(
    {
        "print_weight",
        "print_status",
        "current_layer",
        "total_layers",
        "print_progress",
        "gcode_file_downloaded",
        "print_error",
        "remaining_time",
        "start_time",
        "end_time",
    }
)

# The one AMS this ledger follows, by its printer's own numbering. The registry's tray
# `unique_id` carries the AMS unit's *serial*, never its ordinal, and the only place an
# ordinal is ever stated is the weight sensor's `AMS 1 Tray 4` attribute keys — which is
# also the only ordinal the reference machine has ever reported (docs/12). v1 already
# dropped every other ordinal with a warning; naming that ordinal is what makes the
# behaviour representable rather than implicit.
TRACKED_AMS = AmsIndex(1)

BAMBU_LAB_EVENT = "bambu_lab_event"
EVENT_PRINT_STARTED = "event_print_started"
EVENT_PRINT_FINISHED = "event_print_finished"
EVENT_PRINT_CANCELED = "event_print_canceled"
EVENT_PRINT_FAILED = "event_print_failed"

_OUTCOMES = {
    EVENT_PRINT_FINISHED: PrintJobState.FINISHED,
    EVENT_PRINT_CANCELED: PrintJobState.CANCELLED,
    EVENT_PRINT_FAILED: PrintJobState.FAILED,
}

# The per-tray attribute keys on the weight sensor, as upstream writes them. Strings in
# an attribute dictionary with no schema and no version (docs/05 §5.8) — which is why
# the translation is fixture-tested rather than believed.
_TRAY_WEIGHT_KEY = re.compile(r"AMS (\d+) Tray (\d+)")
_EXTERNAL_SPOOL_KEY = "External Spool"

# What a job is called when the file sensor cannot say. Never blank: the review card and
# the notification both lead with the name, and an empty string reads as a rendering bug
# rather than an honest unknown.
UNKNOWN_JOB_NAME = "unknown print"

# The three sensors docs/14 §14.5 names for the Printer tab — active tray, online,
# connection mode — are **deliberately not in `PRINT_SENSOR_KEYS` yet**.
#
# Discovery here resolves by `platform` + `translation_key`, never by entity id, and the
# spec's own rule is that such a key is read off the reference instance's entity registry
# *before* the constant is frozen (docs/13 — Traps). Guessing a key would break silently
# for every user: an unmatched key discovers nothing, and the tab would report "no printer
# reported this" forever without anybody noticing it was our typo.
#
# So the Printer tab serialises all three as `null` today — the standing policy for an
# undiscovered sensor, applied honestly — and freezing them is one line each here plus one
# reader, once the keys are confirmed. `tests/fixtures/bambu/entity_registry.json` already
# carries rows whose keys read `active_tray` and `online`; `connection_mode` was never
# captured. That asymmetry is the reason the trio waits together rather than shipping two
# thirds of a verified constant.
FUTURE_PRINT_SENSOR_KEYS = frozenset({"active_tray", "online", "connection_mode"})


@dataclass(frozen=True, slots=True)
class PrinterError:
    """The error sensor as it reads right now.

    `active` is the binary state; `code` is the verbatim integer off its attributes, or
    `None` when the sensor exposes none. The two are separate facts: upstream can report
    an error without a code, and inventing one from the flag would put a searchable HMS
    quad on the screen that matches nothing.
    """

    active: bool
    code: int | None


@dataclass(frozen=True, slots=True)
class JobStatus:
    """The job sensors at the moment they are asked — the read-only glance of docs/14 §14.5.

    Every field but `name` is nullable, and null means *the sensor did not say*. That is
    the gateway's standing policy applied to display: a missing figure is not a figure of
    zero, and the tab renders a dash for each one. `name` alone has a stated fallback
    (`UNKNOWN_JOB_NAME`), because a blank job name reads as a rendering bug.

    `remaining_minutes` is the one field whose absence covers two situations, and they
    render identically on purpose: the sensor said nothing, and there is no job for it to
    say anything about. See `_remaining_minutes`.
    """

    status: str | None
    name: str
    current_layer: int | None
    total_layers: int | None
    progress: Percentage | None
    error: PrinterError | None
    remaining_minutes: int | None


class BambuLabGateway:
    """`PrinterGateway`, implemented against `ha-bambulab`'s public entity surface.

    Discovery runs once, at construction — registry reads are synchronous and the
    composition root constructs the gateway inside `async_setup_entry`, so the entity
    population is as settled as it is ever going to be without late binding.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._listeners: list[TrayListener] = []
        self._job_listeners: list[PrintListener] = []
        self._unsubscribe: CALLBACK_TYPE | None = None
        self._unsubscribe_jobs: CALLBACK_TYPE | None = None
        # Printers first: the trays need a printer to be named after, and the serial is
        # read off the job sensors (see the module docstring).
        printers = _discover_printers(hass)
        self._print_sensors = printers.sensors
        self._printer_device_id = printers.device_id
        self._printer_serial = printers.serial
        self._ignored_printers = printers.ignored
        self._entity_by_tray = _discover_trays(hass, self.tray_printer)
        self._tray_by_entity = {entity_id: tray for tray, entity_id in self._entity_by_tray.items()}

    @property
    def dormant(self) -> bool:
        """Whether discovery found no trays — `ha-bambulab` absent or not yet set up.

        The on-demand sync reads this to answer honestly: a dormant gateway has no trays
        to report, which is a different fact from four empty ones. Reloading the entry
        after the upstream integration appears re-runs discovery, per the module policy.
        """
        return not self._entity_by_tray

    @property
    def printer_serial(self) -> PrinterSerial | None:
        """The machine this ledger follows, or `None` when discovery could not name one.

        Null is the honest answer rather than the sentinel: *no printer was identified* is
        a different statement from *this printer is called UNIDENTIFIED*, and the Printer
        tab has to be able to tell the reader which of the two it is looking at.
        """
        return self._printer_serial

    @property
    def tray_printer(self) -> PrinterSerial:
        """The name every tray reference this gateway hands out carries.

        The discovered serial when there is one, and `UNIDENTIFIED_PRINTER` when there is
        not — the same name migration 0007 wrote into the rows it could not name, so a
        ledger with no discoverable printer keeps one consistent tray space instead of two
        that never meet.
        """
        return self._printer_serial if self._printer_serial is not None else UNIDENTIFIED_PRINTER

    @property
    def ignored_printers(self) -> tuple[PrinterSerial, ...]:
        """Every other printer in the registry — found, named, and not tracked.

        v1 warned about these into a log nobody reads. Keeping them lets the Printer tab
        say so where the owner will actually see it. **Nothing here follows them**; the
        list is a statement about today's behaviour, not the beginning of supporting it.
        """
        return self._ignored_printers

    @property
    def discovered(self) -> bool:
        """Whether discovery found anything at all — trays *or* job sensors.

        `dormant` above asks the narrower tray question, because the reconciliation pass
        has nothing to do without trays. The Printer tab asks the wider one: a machine
        whose job sensors resolved still has a status worth showing even if its AMS did
        not, and answering `dormant` there would hide a printer that is plainly present
        (docs/14 §14.5).
        """
        return bool(self._entity_by_tray) or self._printer_device_id is not None

    @property
    def watched_entity_ids(self) -> frozenset[str]:
        """Every entity whose change can alter what the Printer tab shows.

        Discovery already resolved these — the tray sensors and the job sensors — and the
        reconciliation pass already subscribes to the tray half. Exposing the union lets the
        panel's subscription push a new snapshot when one of *these* changes, rather than
        the panel asking again on a timer or on every unrelated thing that happens in the
        house.

        The set is what discovery found. A dormant gateway returns an empty one, and a
        subscription over nothing correctly never fires.

        `remaining_time` joining the set is what makes a countdown count down: the sensor
        changes about once a minute during a print, and each change pushes one debounced
        snapshot. That is still nothing polling — it is the machine saying so — and a
        remaining time frozen at whatever it read when the tab was opened would be the
        stalest possible figure on a page whose whole point is being current (docs/14
        §14.5, amended v1.1).
        """
        return frozenset(self._entity_by_tray.values()) | frozenset(self._print_sensors.values())

    def current_job_status(self) -> JobStatus:
        """What the printer says about the job right now.

        Read through the very same total, never-raising readers the lifecycle events use
        (`_text_state`, `_layer`, `_progress`, `_error_code`), so an unavailable sensor is
        `None` here exactly as it is there. **Reading writes nothing** — the Printer tab is
        a glance, and the sync button on Inventory remains the one mutation path.
        """
        return JobStatus(
            status=self._text_state("print_status"),
            name=self._job_name(),
            current_layer=self._layer("current_layer"),
            total_layers=self._total_layers(),
            progress=self._progress(),
            error=self._printer_error(),
            remaining_minutes=self._remaining_minutes(),
        )

    def subscribe(self, listener: TrayListener) -> None:
        """Register a listener for tray changes. Registration itself does no I/O.

        The state-change tracker is installed on the first subscription and shared by all
        of them; with no trays discovered this is a no-op, because there is nothing to
        watch and inventing entities to watch would be worse than silence.
        """
        if not self._entity_by_tray:
            LOGGER.debug("no %s tray entities found; the gateway stays dormant", UPSTREAM_PLATFORM)
            return
        self._listeners.append(listener)
        if self._unsubscribe is None:
            self._unsubscribe = async_track_state_change_event(
                self._hass, list(self._tray_by_entity), self._on_tray_state_change
            )

    def subscribe_jobs(self, listener: PrintListener) -> None:
        """Register a listener for job lifecycle events. Registration itself does no I/O.

        The bus listener is installed on the first subscription and shared. Without a
        discovered printer device there is nothing to filter the bus events against, so
        the surface stays dormant — same policy as the trays.
        """
        if self._printer_device_id is None:
            LOGGER.debug("no %s print sensors found; job events stay dormant", UPSTREAM_PLATFORM)
            return
        self._job_listeners.append(listener)
        if self._unsubscribe_jobs is None:
            self._unsubscribe_jobs = self._hass.bus.async_listen(
                BAMBU_LAB_EVENT, self._on_job_event
            )

    async def current_trays(self) -> dict[TrayRef, TrayReading]:
        """Every tray as last reported, keyed by its reference, in tray order.

        A tray whose sensor is missing, unavailable or malformed is *omitted*, never
        reported empty: absence of data and absence of a spool are different facts, and
        conflating them would unmount a spool because a sensor blinked (docs/03 §3.8).
        """
        readings: dict[TrayRef, TrayReading] = {}
        for tray, entity_id in sorted(self._entity_by_tray.items()):
            reading = _read(tray, self._hass.states.get(entity_id))
            if reading is not None:
                readings[tray] = reading
        return readings

    @callback
    def detach(self) -> None:
        """Stop listening — trays and jobs both. Idempotent, because it runs twice on a
        clean unload.

        The composition root calls it at the top of `async_unload_entry` — Home Assistant
        runs `async_on_unload` callbacks only after that function returns, and a tray
        change in the gap would reach a closed database — and the registration made with
        `entry.async_on_unload` then runs it again, kept as the safety net for the
        setup-failure paths that never reach unload.
        """
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._unsubscribe_jobs is not None:
            self._unsubscribe_jobs()
            self._unsubscribe_jobs = None
        self._listeners.clear()
        self._job_listeners.clear()

    # -- trays -------------------------------------------------------------------------

    @callback
    def _on_tray_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Runs inside Home Assistant's event loop, so it must never raise.

        `_read` is total — every malformed shape becomes `None` — and delivery happens in
        a background task, where a failing use case is logged instead of unwinding the
        bus dispatch.
        """
        tray = self._tray_by_entity.get(event.data["entity_id"])
        if tray is None:  # unreachable: the tracker watches only resolved entities
            return
        reading = _read(tray, event.data["new_state"])
        if reading is None:
            return
        self._hass.async_create_background_task(
            self._deliver(reading), name=f"filament_ledger tray {tray.slot} change"
        )

    async def _deliver(self, reading: TrayReading) -> None:
        for listener in list(self._listeners):
            try:
                await listener(reading)
            except Exception:
                LOGGER.exception("tray listener failed for %s", reading)

    # -- jobs --------------------------------------------------------------------------

    @callback
    def _on_job_event(self, event: Event[dict[str, Any]]) -> None:
        """Runs inside Home Assistant's event loop, so it must never raise.

        Every reader below is total — an unavailable sensor becomes `None`, never a zero —
        and delivery happens in a background task, same as the trays.
        """
        if event.data.get("device_id") != self._printer_device_id:
            return  # another machine — or the AMS device, which fires nothing today
        translated = self._translate_job_event(event.data.get("type"))
        if translated is None:
            return
        self._hass.async_create_background_task(
            self._deliver_job(translated), name="filament_ledger job event"
        )

    async def _deliver_job(self, event: PrintEvent) -> None:
        for listener in list(self._job_listeners):
            try:
                await listener(event)
            except Exception:
                LOGGER.exception("print listener failed for %s", type(event).__name__)

    def _translate_job_event(self, event_type: object) -> PrintEvent | None:
        """One bus event into domain terms, reading the moment's sensors.

        The figures are captured *now* because the ending is the last moment they
        describe this job: the counters reset when the next print starts.
        """
        if event_type == EVENT_PRINT_STARTED:
            return PrintStarted(
                name=self._job_name(),
                plan=self._per_tray_weights(),
                printer_started_at=self._moment("start_time"),
            )
        outcome = _OUTCOMES.get(event_type) if isinstance(event_type, str) else None
        if outcome is None:
            return None  # event_print_error and anything upstream adds later
        return PrintEnded(
            outcome=outcome,
            name=self._job_name(),
            layer_reached=self._layer("current_layer"),
            total_layers=self._total_layers(),
            progress=self._progress(),
            reported_usage=self._per_tray_weights(),
            raw_gcode_state=self._text_state("print_status"),
            raw_print_error=self._error_code(),
            printer_started_at=self._moment("start_time"),
            printer_ended_at=self._moment("end_time"),
        )

    def _sensor_state(self, key: str) -> State | None:
        entity_id = self._print_sensors.get(key)
        if entity_id is None:
            return None
        state = self._hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state

    def _job_name(self) -> str:
        state = self._sensor_state("gcode_file_downloaded")
        if state is None or not state.state.strip():
            return UNKNOWN_JOB_NAME
        return state.state

    def _text_state(self, key: str) -> str | None:
        state = self._sensor_state(key)
        return state.state if state is not None else None

    def _layer(self, key: str) -> int | None:
        state = self._sensor_state(key)
        if state is None:
            return None
        try:
            value = int(state.state)
        except ValueError:
            LOGGER.debug("%s reads %r, which is not a layer count", key, state.state)
            return None
        return value if value >= 0 else None

    def _total_layers(self) -> int | None:
        """Zero total layers is reported before a file is sliced — unknown, not a total."""
        value = self._layer("total_layers")
        return value if value is not None and value >= 1 else None

    def _remaining_minutes(self) -> int | None:
        """How much longer the job in progress has, in whole minutes — or `None`.

        **Zero is read as "no job", not as "any moment now".** Upstream parks this sensor
        at zero between prints, so a machine that finished last Tuesday reports the same
        zero as one whose last layer is going down — and of the two readings that a `0 min`
        on screen could mean, the idle one is far more often the true one and is the one
        that would be a lie about a printer nobody is standing at. The cost is the final
        sub-minute of a real print, which shows a dash instead of a countdown. That is the
        same rule `_total_layers` applies to a file that is not sliced yet: under-claim.

        A negative figure is upstream noise, and anything unparseable is dropped the way
        `_layer` drops it — this reader is total, like every other one here.
        """
        state = self._sensor_state("remaining_time")
        if state is None:
            return None
        try:
            minutes = int(state.state)
        except ValueError:
            LOGGER.debug("remaining_time reads %r, which is not a minute count", state.state)
            return None
        return minutes if minutes > 0 else None

    def _moment(self, key: str) -> datetime | None:
        """One of the printer's own timestamps, or `None` when it cannot be trusted.

        A timestamp sensor carries an ISO-8601 instant. A value that does not parse is
        dropped, and so is one carrying **no offset**: a naive datetime names a wall clock
        rather than an instant, and this boundary has no business deciding which clock. It
        would also be uncomparable with everything else the domain holds, so refusing it
        here is what keeps the readers total rather than moving the failure inward.
        """
        state = self._sensor_state(key)
        if state is None:
            return None
        try:
            moment = datetime.fromisoformat(state.state)
        except ValueError:
            LOGGER.debug("%s reads %r, which is not an ISO-8601 instant", key, state.state)
            return None
        if moment.tzinfo is None:
            LOGGER.debug("%s reads %r, which names no offset; skipped", key, state.state)
            return None
        return moment

    def _progress(self) -> Percentage | None:
        state = self._sensor_state("print_progress")
        if state is None:
            return None
        try:
            return Percentage.of(state.state)
        # PEP 758 (Python 3.14): an unparenthesized pair catches either exception.
        # This is the formatter's canonical form, not the Python 2 `except A as B`.
        except InvalidValueError, ArithmeticError:
            LOGGER.debug("print_progress reads %r, which is not a percentage", state.state)
            return None

    def _error_code(self) -> int | None:
        """The verbatim integer off the error sensor's attributes, when one is exposed.

        The binary state itself carries no code, and a code is never invented from it.
        """
        state = self._sensor_state("print_error")
        if state is None:
            return None
        code = state.attributes.get("code")
        if isinstance(code, int) and not isinstance(code, bool):
            return code
        return None

    def _printer_error(self) -> PrinterError | None:
        """The error sensor as a pair, or `None` when the sensor is absent or unavailable.

        An absent sensor is not a healthy printer — it is a printer that did not say — so
        it serialises as null rather than as `active: false`.
        """
        state = self._sensor_state("print_error")
        if state is None:
            return None
        return PrinterError(active=state.state == STATE_ON, code=self._error_code())

    def _per_tray_weights(self) -> dict[TrayRef, Grams] | None:
        """The weight sensor's per-tray figures, translated — or `None`, never a zero.

        `None` covers the whole Q4-open path: no sensor, an unavailable sensor, and
        attributes carrying no per-tray keys all mean the breakdown never materialised.
        An attribute dictionary that *does* speak the per-tray dialect translates to a
        mapping — possibly empty, which is the printer naming no AMS trays and is a
        different fact from silence (docs/04-use-cases.md UC-04).
        """
        state = self._sensor_state("print_weight")
        if state is None:
            return None
        weights: dict[TrayRef, Grams] = {}
        recognised = False
        for key, value in state.attributes.items():
            if key == _EXTERNAL_SPOOL_KEY:
                recognised = True
                # The domain keys usage by tray (docs/02 §2.3); an external-spool figure
                # has no tray to land in. Dropping it silently would be the optimistic
                # lie this project exists to prevent, so it is at least loud.
                LOGGER.warning(
                    "the printer reports %r g on the external spool; v1 tracks AMS "
                    "consumption only, so this figure is not recorded",
                    value,
                )
                continue
            match = _TRAY_WEIGHT_KEY.fullmatch(key)
            if match is None:
                continue
            recognised = True
            ams, slot = int(match.group(1)), int(match.group(2))
            if ams != TRACKED_AMS.value:
                LOGGER.warning("per-tray figure for %r ignored; v1 tracks a single AMS", key)
                continue
            grams = _weight(value)
            if grams is None:
                LOGGER.debug("per-tray figure for %r reads %r; skipped", key, value)
                continue
            try:
                tray = TrayRef(printer=self.tray_printer, ams=TRACKED_AMS, slot=SlotIndex(slot))
            except InvalidValueError:
                LOGGER.debug("per-tray key %r names a slot outside 1..4; skipped", key)
                continue
            weights[tray] = grams
        return weights if recognised else None


def _discover_trays(hass: HomeAssistant, printer: PrinterSerial) -> dict[TrayRef, str]:
    """Resolve the AMS tray sensors to entity ids, keyed by the tray each one describes.

    The rule, from the shapes captured in docs/12: `platform == "bambu_lab"` selects the
    upstream integration, `translation_key == "tray"` discriminates tray sensors from the
    printer's other sensors, and the `unique_id` suffix `_tray_<n>` carries the slot.

    `printer` and `TRACKED_AMS` complete the reference. The registry's grouping key is the
    AMS unit's serial, which is what still decides *which* unit wins when there are several;
    the ordinal it is followed under is `TRACKED_AMS`, for the reason that constant gives.
    """
    groups: dict[str, dict[TrayRef, str]] = {}
    for entry in er.async_get(hass).entities.values():
        if entry.platform != UPSTREAM_PLATFORM or entry.translation_key != TRAY_TRANSLATION_KEY:
            continue
        ams, marker, ordinal = entry.unique_id.rpartition(_TRAY_MARKER)
        if not marker or not ordinal.isdigit():
            LOGGER.debug("tray unique_id %r has no _tray_<n> suffix; skipped", entry.unique_id)
            continue
        try:
            tray = TrayRef(printer=printer, ams=TRACKED_AMS, slot=SlotIndex(int(ordinal)))
        except InvalidValueError:
            LOGGER.debug("tray unique_id %r names a slot outside 1..4; skipped", entry.unique_id)
            continue
        groups.setdefault(ams, {})[tray] = entry.entity_id
    if not groups:
        LOGGER.debug("no %s tray sensors in the entity registry", UPSTREAM_PLATFORM)
        return {}
    first = min(groups)
    if len(groups) > 1:
        LOGGER.warning(
            "multiple AMS units in the registry (%s); v1 tracks a single printer, using %s",
            sorted(groups),
            first,
        )
    return groups[first]


@dataclass(frozen=True, slots=True)
class PrinterDiscovery:
    """What the registry said about printers: the one followed, and the ones passed over.

    `serial` is `None` when nothing resolved — the honest absence, distinct from the
    sentinel a row carries when it names a printer nobody has identified yet.

    `ignored` exists because v1's warning went only to the log. The gateway still picks
    exactly one machine and still passes over the rest; keeping their names lets the
    Printer tab say so where somebody will read it.
    """

    sensors: dict[str, str]
    device_id: str | None
    serial: PrinterSerial | None
    ignored: tuple[PrinterSerial, ...]


def _discover_printers(hass: HomeAssistant) -> PrinterDiscovery:
    """Resolve the printer's job sensors, its device id, and its serial.

    The job sensors hang off the printer device — the trays hang off the AMS device — so
    these registry entries are also where the printer's device id comes from, and that id
    is what filters `bambu_lab_event` down to the machine this ledger tracks.

    The serial rides on the same entries: upstream writes each `unique_id` as
    `<serial>_<translation_key>`, so removing the key that matched leaves the serial. A
    device whose entries carry no readable serial still wins the selection if it sorts
    first — the device id is what job filtering needs, and a machine with no name is still
    the machine this ledger follows.
    """
    groups: dict[str, dict[str, str]] = {}
    serials: dict[str, PrinterSerial] = {}
    for entry in er.async_get(hass).entities.values():
        if (
            entry.platform != UPSTREAM_PLATFORM
            or entry.translation_key not in PRINT_SENSOR_KEYS
            or entry.device_id is None
        ):
            continue
        groups.setdefault(entry.device_id, {})[entry.translation_key] = entry.entity_id
        serial = _serial_of(entry.unique_id, entry.translation_key)
        if serial is not None:
            serials.setdefault(entry.device_id, serial)
    if not groups:
        LOGGER.debug("no %s print sensors in the entity registry", UPSTREAM_PLATFORM)
        return PrinterDiscovery(sensors={}, device_id=None, serial=None, ignored=())
    first = min(groups)
    if len(groups) > 1:
        LOGGER.warning(
            "multiple printers in the registry (%s); v1 tracks a single printer, using %s",
            sorted(groups),
            first,
        )
    ignored = tuple(
        serials[device_id]
        for device_id in sorted(groups)
        if device_id != first
        if device_id in serials
    )
    return PrinterDiscovery(
        sensors=groups[first],
        device_id=first,
        serial=serials.get(first),
        ignored=ignored,
    )


def _serial_of(unique_id: str, translation_key: str) -> PrinterSerial | None:
    """The machine's serial, off a job sensor's `unique_id`. `None` when the shape differs.

    Upstream's own format, verified against the frozen registry fixture:
    `00000000TESTSER_print_weight` for the key `print_weight`. Total, like every reader at
    this boundary — an upstream that changes the format leaves the ledger with an
    unidentified printer, which it already knows how to be, rather than with a crash.
    """
    suffix = f"_{translation_key}"
    if not unique_id.endswith(suffix):
        LOGGER.debug("print sensor unique_id %r does not end in %r; no serial", unique_id, suffix)
        return None
    serial = unique_id[: -len(suffix)]
    if not serial.strip():
        return None
    return PrinterSerial(serial)


def _read(tray: TrayRef, state: State | None) -> TrayReading | None:
    """Translate one tray sensor into a reading, or `None` when it cannot be trusted.

    Total by construction: every guard below covers a constructor precondition of the
    value objects, so nothing in here can raise into the caller — which is what lets
    `_on_tray_state_change` run bare inside the event loop.
    """
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    attributes = state.attributes
    empty = attributes.get("empty")
    if not isinstance(empty, bool):
        LOGGER.debug("%s reports no usable 'empty' flag (%r); reading skipped", tray, empty)
        return None
    if empty:
        # An emptied tray describes no spool. Whatever name or colour the attributes
        # still carry is a leftover of the previous occupant, not an observation.
        return TrayReading(tray=tray, tag=None, empty=True)
    return TrayReading(
        tray=tray,
        tag=_tag(attributes.get("tag_uid")),
        empty=False,
        name=_text(attributes.get("name")),
        material=_text(attributes.get("type")),
        colour=_colour(attributes.get("color")),
    )


def _tag(value: object) -> TagUid | None:
    """Sixteen zeros means nothing was read — absence, never an identity (docs/12).

    Translating the sentinel to `None` is this boundary's job; `TagUid` refusing the same
    string is the domain's backstop, not the translation.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text == ABSENT_TAG_SENTINEL:
        return None
    return TagUid(text)


def _text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _colour(value: object) -> Colour | None:
    """The printer speaks `#RRGGBBAA`; a hint that fails to parse is dropped, not fatal."""
    if not isinstance(value, str):
        return None
    try:
        return Colour.parse(value)
    except InvalidValueError:
        LOGGER.debug("unparseable colour hint %r ignored", value)
        return None


def _weight(value: object) -> Grams | None:
    """A per-tray figure: a non-negative number, or nothing. Negative consumption and
    non-numeric shapes are upstream noise, not data."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    grams = Grams.of(value)
    return None if grams.is_negative else grams
