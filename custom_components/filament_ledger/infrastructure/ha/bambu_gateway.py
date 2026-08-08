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

**A printer's serial comes off its job sensors' own `unique_id`s**, which upstream writes
as `<serial>_<translation_key>` — `00000000TESTSER_print_weight` in the frozen registry
fixture, anonymised but faithful in shape. That is the stable identity the domain's
`TrayRef` needs, read from evidence this repository already has rather than from a
`translation_key` nobody has confirmed.

**A tray is attributed to the machine its own `unique_id` mentions.** The tray sensors hang
off the AMS device, not the printer, and their `unique_id` reads
`A1_00000000TESTSER_AMS_00000000TESTAMS_tray_1` — the printer's serial is in there, behind a
model prefix whose boundary is written down nowhere. v1 refused to *parse* that string for a
serial, and it was right to: the boundary is a guess. Asking whether a serial the job sensors
already resolved **appears in** it is a different question, answered against the same frozen
fixture, and it needs no format at all. `_printer_of` states the fallbacks — with one printer
every AMS is its, with none they take the reserved `UNIDENTIFIED` serial, and with several an
AMS naming no discovered machine is dropped rather than guessed at.

Job events are filtered by device. The bus carries `bambu_lab_event` for every machine and
the payload names only a device id, so the gateway keeps a device-id-to-serial map built by
the same discovery — the trays hang off the AMS device, the job sensors off the printer — and
an event whose device is in neither is somebody else's. **The serial travels on the
translated event**, because the use case that receives it correlates an ending against the
running job *of that machine* and cannot recover from anywhere else which machine spoke.

Three conscious limitations, documented rather than discovered:

- **One AMS per printer.** If a machine's registry entries describe several AMS units, the
  first (by identity) wins and a warning names the rest. `TRACKED_AMS` says why the ordinal
  is a constant; the reference instance has never reported another.
- **A machine has to be nameable to be followed.** With one printer an unreadable serial is
  answered by `UNIDENTIFIED_PRINTER`, which is exactly what such a ledger's rows carry. With
  several it is not answered at all: the sentinel means *the one machine this ledger has
  always followed*, and handing it to two live machines would merge two tray spaces into
  one. `_resolve_names` skips them, loudly, and the Printer tab counts them.
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

# The one AMS this ledger follows **per printer**, by that printer's own numbering. The
# registry's tray `unique_id` carries the AMS unit's *serial*, never its ordinal, and the
# only place an ordinal is ever stated is the weight sensor's `AMS 1 Tray 4` attribute
# keys — which is also the only ordinal the reference machine has ever reported (docs/12).
# v1 already dropped every other ordinal with a warning; naming that ordinal is what makes
# the behaviour representable rather than implicit.
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
        discovery = _discover(hass)
        self._printers = {printer.serial: printer for printer in discovery.printers}
        self._unnamed_printers = discovery.unnamed
        # The two indexes every hot path reads: a bus event names a device, and a state
        # change names an entity. Both are built once here rather than searched per event.
        self._printer_by_device = {
            printer.device_id: printer.serial
            for printer in discovery.printers
            if printer.device_id is not None
        }
        self._entity_by_tray = {
            tray: entity_id
            for printer in discovery.printers
            for tray, entity_id in printer.trays.items()
        }
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
    def printers(self) -> tuple[PrinterSerial, ...]:
        """Every machine this ledger follows, in one canonical order.

        Ordered by serial rather than by the order the registry happened to yield, so the
        AMS view's sections and the Printer tab's do not reshuffle between restarts. A
        household with one machine gets a one-element tuple; a ledger with no discoverable
        printer gets an empty one, which is *no machine was identified* rather than *one
        machine called UNIDENTIFIED* — a distinction the Printer tab renders differently.
        """
        return tuple(sorted(self._printers))

    @property
    def default_printer(self) -> PrinterSerial | None:
        """The machine a caller that named no printer means — or `None` when nobody can say.

        Exactly one followed machine is the only case with an unambiguous answer, and it is
        the case every automation written against v1 was written in: naming a serial for the
        only machine in the house would be ceremony rather than precision. With several,
        there is no such thing as *the* printer and the absence is refused rather than
        resolved (`LedgerRuntime.tray_printer`, docs/05 §5.4).

        With none followed the answer is `UNIDENTIFIED_PRINTER` — the name migration 0007
        wrote into the rows it could not name — so a printerless ledger keeps one consistent
        tray space instead of two that never meet.
        """
        if not self._printers:
            return UNIDENTIFIED_PRINTER
        if len(self._printers) > 1:
            return None
        return next(iter(self._printers))

    @property
    def unnamed_printers(self) -> int:
        """How many machines were found and passed over for having no readable serial.

        Zero on every instance whose upstream writes `unique_id`s the documented way, which
        is every instance this repository has evidence of. It is reported rather than only
        logged for the reason the ignored serials were in v1.4: a machine the ledger is not
        following is exactly the fact a log will not tell anybody.
        """
        return self._unnamed_printers

    @property
    def discovered(self) -> bool:
        """Whether discovery found anything at all — trays *or* job sensors.

        `dormant` above asks the narrower tray question, because the reconciliation pass
        has nothing to do without trays. The Printer tab asks the wider one: a machine
        whose job sensors resolved still has a status worth showing even if its AMS did
        not, and answering `dormant` there would hide a printer that is plainly present
        (docs/14 §14.5).
        """
        return bool(self._entity_by_tray) or bool(self._printer_by_device)

    @property
    def watched_entity_ids(self) -> frozenset[str]:
        """Every entity whose change can alter what the Printer tab shows.

        Discovery already resolved these — every machine's tray sensors and job sensors —
        and the reconciliation pass already subscribes to the tray half. Exposing the union
        lets the panel's subscription push a new snapshot when one of *these* changes, rather
        than the panel asking again on a timer or on every unrelated thing that happens in
        the house.

        The set is what discovery found. A dormant gateway returns an empty one, and a
        subscription over nothing correctly never fires.

        `remaining_time` joining the set is what makes a countdown count down: the sensor
        changes about once a minute during a print, and each change pushes one debounced
        snapshot. That is still nothing polling — it is the machine saying so — and a
        remaining time frozen at whatever it read when the tab was opened would be the
        stalest possible figure on a page whose whole point is being current (docs/14
        §14.5, amended v1.1).
        """
        return frozenset(self._entity_by_tray.values()) | frozenset(
            entity_id
            for printer in self._printers.values()
            for entity_id in printer.sensors.values()
        )

    def current_job_status(self, printer: PrinterSerial) -> JobStatus:
        """What one machine says about its job right now.

        Read through the very same total, never-raising readers the lifecycle events use
        (`_text_state`, `_layer`, `_progress`, `_error_code`), so an unavailable sensor is
        `None` here exactly as it is there. **Reading writes nothing** — the Printer tab is
        a glance, and the sync button on Inventory remains the one mutation path.

        A serial this gateway does not follow reads as a machine that reported nothing,
        rather than raising: every reader below resolves through `_sensor_state`, which
        answers `None` for a sensor it cannot find, and there is no shape of *unknown
        printer* that is more honest than *said nothing*.
        """
        return JobStatus(
            status=self._text_state(printer, "print_status"),
            name=self._job_name(printer),
            current_layer=self._layer(printer, "current_layer"),
            total_layers=self._total_layers(printer),
            progress=self._progress(printer),
            error=self._printer_error(printer),
            remaining_minutes=self._remaining_minutes(printer),
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

        The bus listener is installed on the first subscription and shared by every machine:
        one `bambu_lab_event` subscription carries the whole house, and the device id on each
        event is what says which printer spoke. Without a discovered printer device there is
        nothing to resolve those events against, so the surface stays dormant — same policy
        as the trays.
        """
        if not self._printer_by_device:
            LOGGER.debug("no %s print sensors found; job events stay dormant", UPSTREAM_PLATFORM)
            return
        self._job_listeners.append(listener)
        if self._unsubscribe_jobs is None:
            self._unsubscribe_jobs = self._hass.bus.async_listen(
                BAMBU_LAB_EVENT, self._on_job_event
            )

    async def current_trays(self) -> dict[TrayRef, TrayReading]:
        """Every machine's trays as last reported, keyed by reference, in tray order.

        One mapping across every followed printer rather than one per machine: `TrayRef`
        orders by printer first, so the flat mapping is already grouped and every caller —
        the reconciliation pass, the Printer tab — reads it the way it always did.

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

        The device id is what says which machine spoke, and it is the only thing on the
        payload that does. An id in no followed machine's place is passed over — an AMS
        device, which fires nothing today, or a printer discovery could not name.

        Every reader below is total — an unavailable sensor becomes `None`, never a zero —
        and delivery happens in a background task, same as the trays.
        """
        device_id = event.data.get("device_id")
        printer = self._printer_by_device.get(device_id) if isinstance(device_id, str) else None
        if printer is None:
            return
        translated = self._translate_job_event(printer, event.data.get("type"))
        if translated is None:
            return
        self._hass.async_create_background_task(
            self._deliver_job(translated), name=f"filament_ledger job event on {printer}"
        )

    async def _deliver_job(self, event: PrintEvent) -> None:
        for listener in list(self._job_listeners):
            try:
                await listener(event)
            except Exception:
                LOGGER.exception("print listener failed for %s", type(event).__name__)

    def _translate_job_event(self, printer: PrinterSerial, event_type: object) -> PrintEvent | None:
        """One machine's bus event into domain terms, reading that machine's sensors.

        Every figure comes from the sensors of the printer the device id resolved to, which
        is the whole of what makes two machines printing at once safe: a reading taken from
        whichever job sensor discovery happened to keep would put one printer's layer count
        and one printer's grams on the other printer's job.

        The figures are captured *now* because the ending is the last moment they
        describe this job: the counters reset when the next print starts.
        """
        if event_type == EVENT_PRINT_STARTED:
            return PrintStarted(
                name=self._job_name(printer),
                printer=printer,
                plan=self._per_tray_weights(printer),
                printer_started_at=self._moment(printer, "start_time"),
            )
        outcome = _OUTCOMES.get(event_type) if isinstance(event_type, str) else None
        if outcome is None:
            return None  # event_print_error and anything upstream adds later
        return PrintEnded(
            outcome=outcome,
            name=self._job_name(printer),
            printer=printer,
            layer_reached=self._layer(printer, "current_layer"),
            total_layers=self._total_layers(printer),
            progress=self._progress(printer),
            reported_usage=self._per_tray_weights(printer),
            raw_gcode_state=self._text_state(printer, "print_status"),
            raw_print_error=self._error_code(printer),
            printer_started_at=self._moment(printer, "start_time"),
            printer_ended_at=self._moment(printer, "end_time"),
        )

    def _sensor_state(self, printer: PrinterSerial, key: str) -> State | None:
        discovered = self._printers.get(printer)
        entity_id = discovered.sensors.get(key) if discovered is not None else None
        if entity_id is None:
            return None
        state = self._hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state

    def _job_name(self, printer: PrinterSerial) -> str:
        state = self._sensor_state(printer, "gcode_file_downloaded")
        if state is None or not state.state.strip():
            return UNKNOWN_JOB_NAME
        return state.state

    def _text_state(self, printer: PrinterSerial, key: str) -> str | None:
        state = self._sensor_state(printer, key)
        return state.state if state is not None else None

    def _layer(self, printer: PrinterSerial, key: str) -> int | None:
        state = self._sensor_state(printer, key)
        if state is None:
            return None
        try:
            value = int(state.state)
        except ValueError:
            LOGGER.debug("%s reads %r, which is not a layer count", key, state.state)
            return None
        return value if value >= 0 else None

    def _total_layers(self, printer: PrinterSerial) -> int | None:
        """Zero total layers is reported before a file is sliced — unknown, not a total."""
        value = self._layer(printer, "total_layers")
        return value if value is not None and value >= 1 else None

    def _remaining_minutes(self, printer: PrinterSerial) -> int | None:
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
        state = self._sensor_state(printer, "remaining_time")
        if state is None:
            return None
        try:
            minutes = int(state.state)
        except ValueError:
            LOGGER.debug("remaining_time reads %r, which is not a minute count", state.state)
            return None
        return minutes if minutes > 0 else None

    def _moment(self, printer: PrinterSerial, key: str) -> datetime | None:
        """One of the printer's own timestamps, or `None` when it cannot be trusted.

        A timestamp sensor carries an ISO-8601 instant. A value that does not parse is
        dropped, and so is one carrying **no offset**: a naive datetime names a wall clock
        rather than an instant, and this boundary has no business deciding which clock. It
        would also be uncomparable with everything else the domain holds, so refusing it
        here is what keeps the readers total rather than moving the failure inward.
        """
        state = self._sensor_state(printer, key)
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

    def _progress(self, printer: PrinterSerial) -> Percentage | None:
        state = self._sensor_state(printer, "print_progress")
        if state is None:
            return None
        try:
            return Percentage.of(state.state)
        # PEP 758 (Python 3.14): an unparenthesized pair catches either exception.
        # This is the formatter's canonical form, not the Python 2 `except A as B`.
        except InvalidValueError, ArithmeticError:
            LOGGER.debug("print_progress reads %r, which is not a percentage", state.state)
            return None

    def _error_code(self, printer: PrinterSerial) -> int | None:
        """The verbatim integer off the error sensor's attributes, when one is exposed.

        The binary state itself carries no code, and a code is never invented from it.
        """
        state = self._sensor_state(printer, "print_error")
        if state is None:
            return None
        code = state.attributes.get("code")
        if isinstance(code, int) and not isinstance(code, bool):
            return code
        return None

    def _printer_error(self, printer: PrinterSerial) -> PrinterError | None:
        """The error sensor as a pair, or `None` when the sensor is absent or unavailable.

        An absent sensor is not a healthy printer — it is a printer that did not say — so
        it serialises as null rather than as `active: false`.
        """
        state = self._sensor_state(printer, "print_error")
        if state is None:
            return None
        return PrinterError(active=state.state == STATE_ON, code=self._error_code(printer))

    def _per_tray_weights(self, printer: PrinterSerial) -> dict[TrayRef, Grams] | None:
        """The weight sensor's per-tray figures, translated — or `None`, never a zero.

        `None` covers the whole Q4-open path: no sensor, an unavailable sensor, and
        attributes carrying no per-tray keys all mean the breakdown never materialised.
        An attribute dictionary that *does* speak the per-tray dialect translates to a
        mapping — possibly empty, which is the printer naming no AMS trays and is a
        different fact from silence (docs/04-use-cases.md UC-04).
        """
        state = self._sensor_state(printer, "print_weight")
        if state is None:
            return None
        weights: dict[TrayRef, Grams] = {}
        recognised = False
        for key, value in state.attributes.items():
            if key == _EXTERNAL_SPOOL_KEY:
                recognised = True
                # The domain keys usage by tray (docs/02 §2.3); an external-spool figure
                # has no tray to land in. A spool on the direct feed now has a *location*
                # that names its machine, which is a different question from a consumption
                # figure having a tray to be deducted through — so this stays dropped, and
                # dropping it silently would be the optimistic lie this project exists to
                # prevent.
                LOGGER.warning(
                    "printer %s reports %r g on the external spool; this ledger tracks AMS "
                    "consumption only, so the figure is not recorded",
                    printer,
                    value,
                )
                continue
            match = _TRAY_WEIGHT_KEY.fullmatch(key)
            if match is None:
                continue
            recognised = True
            ams, slot = int(match.group(1)), int(match.group(2))
            if ams != TRACKED_AMS.value:
                LOGGER.warning(
                    "per-tray figure for %r on printer %s ignored; one AMS per printer is tracked",
                    key,
                    printer,
                )
                continue
            grams = _weight(value)
            if grams is None:
                LOGGER.debug("per-tray figure for %r reads %r; skipped", key, value)
                continue
            try:
                tray = TrayRef(printer=printer, ams=TRACKED_AMS, slot=SlotIndex(slot))
            except InvalidValueError:
                LOGGER.debug("per-tray key %r names a slot outside 1..4; skipped", key)
                continue
            weights[tray] = grams
        return weights if recognised else None


@dataclass(frozen=True, slots=True)
class DiscoveredPrinter:
    """One machine, as the entity registry describes it.

    `device_id` is `None` for a machine assembled out of trays alone — an AMS whose printer
    sensors did not resolve. Such a machine has no job events to hear and no status to show,
    but it still holds the trays this ledger mounts spools into, which is why it is a
    printer here rather than a special case everywhere else.
    """

    serial: PrinterSerial
    device_id: str | None
    sensors: dict[str, str]
    trays: dict[TrayRef, str]


@dataclass(frozen=True, slots=True)
class PrinterDiscovery:
    """Every machine the registry describes, and how many it could not name.

    `printers` is ordered by serial, which is the order every surface downstream renders in.
    `unnamed` counts machines whose job sensors resolved but whose serial did not, and which
    are therefore not followed — see the module docstring for why that is not the sentinel's
    job to cover.
    """

    printers: tuple[DiscoveredPrinter, ...]
    unnamed: int


def _discover(hass: HomeAssistant) -> PrinterDiscovery:
    """Read the whole registry once and assemble the machines out of it.

    Printers first, because a tray is named after the machine that holds it and the serial
    only exists on the job sensors. Trays second, attributed to those names.
    """
    sensors_by_device, serial_by_device = _job_sensors(hass)
    names = _resolve_names(sensors_by_device, serial_by_device)
    trays = _discover_trays(hass, tuple(sorted(set(names.values()))))
    printers = [
        DiscoveredPrinter(
            serial=serial,
            device_id=device_id,
            sensors=sensors_by_device[device_id],
            trays=trays.pop(serial, {}),
        )
        for device_id, serial in sorted(names.items(), key=lambda pair: pair[1])
    ]
    # Whatever trays are left name a machine with no job sensors: an AMS discovered on its
    # own, under the reserved serial `_discover_trays` gives it. It is a printer this ledger
    # mounts into, so it is one of these too.
    printers.extend(
        DiscoveredPrinter(serial=serial, device_id=None, sensors={}, trays=entities)
        for serial, entities in sorted(trays.items())
    )
    return PrinterDiscovery(
        printers=tuple(sorted(printers, key=lambda printer: printer.serial)),
        unnamed=len(sensors_by_device) - len(names),
    )


def _job_sensors(hass: HomeAssistant) -> tuple[dict[str, dict[str, str]], dict[str, PrinterSerial]]:
    """The job sensors, grouped by the device they hang off, and each device's serial.

    The job sensors hang off the printer device — the trays hang off the AMS device — so
    these registry entries are also where a printer's device id comes from, and that id is
    what resolves a `bambu_lab_event` to the machine that fired it.

    The serial rides on the same entries: upstream writes each `unique_id` as
    `<serial>_<translation_key>`, so removing the key that matched leaves the serial.
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
    return groups, serials


def _resolve_names(
    sensors_by_device: dict[str, dict[str, str]], serial_by_device: dict[str, PrinterSerial]
) -> dict[str, PrinterSerial]:
    """Which device is which machine — the followed set, by device id.

    **One printer whose serial did not resolve keeps the reserved sentinel**, which is v1's
    behaviour and is exactly what such a ledger's rows already carry: one machine, one tray
    space, one name for it whatever that name turns out to be.

    **Several, and an unnamed one is not followed.** The sentinel's whole argument is that a
    single-printer ledger has exactly one machine for it to mean; giving it to one of two
    live machines would put two printers' trays into one tray space, where they would collide
    slot for slot. There is nothing else to call the machine — a device id is a random
    identifier, not a name a printer answers to — so it is passed over, loudly, and counted
    where the Printer tab can say so.

    A serial claimed by two devices is the same collision in a different costume, and takes
    the same answer: the first device by id keeps the name.
    """
    if not sensors_by_device:
        return {}
    if len(sensors_by_device) == 1:
        device_id = next(iter(sensors_by_device))
        return {device_id: serial_by_device.get(device_id, UNIDENTIFIED_PRINTER)}
    names: dict[str, PrinterSerial] = {}
    for device_id in sorted(sensors_by_device):
        serial = serial_by_device.get(device_id)
        if serial is None:
            LOGGER.warning(
                "%s device %s carries no readable serial; with several printers present it "
                "cannot be told apart from another and is not followed",
                UPSTREAM_PLATFORM,
                device_id,
            )
            continue
        if serial in names.values():
            LOGGER.warning(
                "%s device %s reports serial %s, which another device already claimed; "
                "the first device keeps the name and this one is not followed",
                UPSTREAM_PLATFORM,
                device_id,
                serial,
            )
            continue
        names[device_id] = serial
    return names


def _discover_trays(
    hass: HomeAssistant, printers: tuple[PrinterSerial, ...]
) -> dict[PrinterSerial, dict[TrayRef, str]]:
    """Resolve the AMS tray sensors to entity ids, keyed by the tray each one describes.

    The rule, from the shapes captured in docs/12: `platform == "bambu_lab"` selects the
    upstream integration, `translation_key == "tray"` discriminates tray sensors from the
    printer's other sensors, and the `unique_id` suffix `_tray_<n>` carries the slot. What
    is left in front of that suffix identifies the AMS unit, and is what groups a unit's
    four trays together — `_printer_of` is what turns a group into a machine.

    One AMS per printer, still: where a machine has several groups the first by identity
    wins and the rest are named in a warning. The ordinal every tray is followed under is
    `TRACKED_AMS`, for the reason that constant gives.
    """
    groups: dict[str, dict[int, str]] = {}
    for entry in er.async_get(hass).entities.values():
        if entry.platform != UPSTREAM_PLATFORM or entry.translation_key != TRAY_TRANSLATION_KEY:
            continue
        ams, marker, ordinal = entry.unique_id.rpartition(_TRAY_MARKER)
        if not marker or not ordinal.isdigit():
            LOGGER.debug("tray unique_id %r has no _tray_<n> suffix; skipped", entry.unique_id)
            continue
        groups.setdefault(ams, {})[int(ordinal)] = entry.entity_id
    if not groups:
        LOGGER.debug("no %s tray sensors in the entity registry", UPSTREAM_PLATFORM)
        return {}

    units: dict[PrinterSerial, list[str]] = {}
    for group in sorted(groups):
        printer = _printer_of(group, printers)
        if printer is None:
            LOGGER.warning(
                "AMS %r names none of the discovered printers (%s); its trays are not "
                "followed, because attributing them would be a guess about which machine "
                "holds them",
                group,
                [serial.value for serial in printers],
            )
            continue
        units.setdefault(printer, []).append(group)

    resolved: dict[PrinterSerial, dict[TrayRef, str]] = {}
    for printer, found in units.items():
        if len(found) > 1:
            LOGGER.warning(
                "printer %s has several AMS units in the registry (%s); one unit per "
                "printer is tracked, using %s",
                printer,
                found,
                found[0],
            )
        resolved[printer] = _trays_of(printer, groups[found[0]])
    return resolved


def _trays_of(printer: PrinterSerial, ordinals: dict[int, str]) -> dict[TrayRef, str]:
    trays: dict[TrayRef, str] = {}
    for ordinal, entity_id in ordinals.items():
        try:
            tray = TrayRef(printer=printer, ams=TRACKED_AMS, slot=SlotIndex(ordinal))
        except InvalidValueError:
            LOGGER.debug("tray %s of %s names a slot outside 1..4; skipped", ordinal, printer)
            continue
        trays[tray] = entity_id
    return trays


def _printer_of(group: str, printers: tuple[PrinterSerial, ...]) -> PrinterSerial | None:
    """Which machine an AMS group belongs to, by the serial its `unique_id` mentions.

    The captured shape is `A1_00000000TESTSER_AMS_00000000TESTAMS_tray_1`: the printer's
    serial is in there, ahead of a model prefix and behind an AMS serial, and where each
    boundary falls is written down nowhere. **This does not parse it.** It asks whether a
    serial the job sensors *already* resolved appears in the string, which needs no format
    to be true and is checked against the same frozen fixture the rest of discovery is.

    Three fallbacks, in the order they are reached:

    - **No printers.** Every AMS takes `UNIDENTIFIED_PRINTER`. This is the ledger with no
      discoverable machine, and the sentinel is what its rows already carry.
    - **One printer.** Every AMS is that machine's, without consulting the string at all —
      there is nothing else for it to be, and an upstream that reshapes tray `unique_id`s
      must not cost a one-machine household its trays.
    - **Several.** The string decides, and a group naming none of them is refused rather
      than assigned. The longest match wins so that one serial being a substring of another
      resolves to the more specific evidence rather than to whichever sorted first.
    """
    if not printers:
        return UNIDENTIFIED_PRINTER
    if len(printers) == 1:
        return printers[0]
    matches = [printer for printer in printers if printer.value in group]
    if not matches:
        return None
    return max(matches, key=lambda printer: len(printer.value))


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
        weight=_reel_weight(attributes.get("tray_weight")),
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


def _reel_weight(value: object) -> Grams | None:
    """The RFID's nominal spool weight — `tray_weight`, which the tag carries in grams.

    Deliberately *not* `_weight` above, on two counts that are policy rather than
    plumbing. The dialect differs: this field arrives as a **string** (`"1000"`), while
    the consumption figures arrive as numbers. And zero means the opposite thing: a tray
    that consumed nothing is a real figure of zero, whereas `tray_weight: "0"` is the tag
    declining to say — the reference machine writes it for the untagged third-party reel
    in tray 3 (docs/12-field-notes.md). Folding the two policies into one helper would
    make one of the two call sites wrong.

    Non-positive and unparseable both become `None`, never a fabricated number: the
    domain refuses an opening weight of nothing, and the register path reads absence as
    *fall back to the configured default* rather than as a figure.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        grams = Grams.of(value.strip() if isinstance(value, str) else value)
    except ArithmeticError, ValueError:
        # `Decimal` refuses the shapes an attribute dictionary can still hold — "", "n/a",
        # "NaN". Caught here so `_read` stays total, as its own docstring promises.
        LOGGER.debug("unusable tray_weight %r ignored", value)
        return None
    return grams if grams.is_positive else None
