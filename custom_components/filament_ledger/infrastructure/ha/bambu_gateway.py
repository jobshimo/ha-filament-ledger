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
# frozen. Three joined in v1.4: `remaining_time` is what the Printer tab shows for a job in
# progress, and `start_time`/`end_time` are the machine's own answer to how long a print
# actually took, as opposed to how long Home Assistant took to notice it.
#
# **`gcode_file` is the key `gcode_name` was meant to be, and the correction is the point.**
# v2.5 added a fallback for `_job_name` keyed on `gcode_name` and froze a fixture row to
# match. No such key exists. The reference instance's `sensor.…_nombre_del_gcode` — the very
# sensor that fallback was written for — reads `translation_key: "gcode_file"`
# (docs/12-field-notes.md, 2026-08-11, read from `core.entity_registry`). The fixture was
# transcribed rather than captured, so the tests agreed with themselves while production
# resolved nothing and every restart mid-print still wrote `unknown print` — which is
# exactly what job `3e752c9c` in the reference ledger is called. This is the failure mode
# the block below names, caught in this module's own code.
#
# `stage`, `subtask_name`, `online`, `active_tray` and `mqtt_mode` joined in the same pass,
# from the same read of the same registry. `stage` is the one that matters most: see
# `_PRINTING_STAGE` below and `BambuLabGateway._is_printing`.
PRINT_SENSOR_KEYS = frozenset(
    {
        "print_weight",
        "print_status",
        "stage",
        "current_layer",
        "total_layers",
        "print_progress",
        "gcode_file_downloaded",
        "gcode_file",
        "subtask_name",
        "print_error",
        "remaining_time",
        "start_time",
        "end_time",
        "online",
        "active_tray",
        "mqtt_mode",
    }
)

# The stages that mean **a print is in progress on this machine**, off the `stage` sensor's
# own enum — 70-odd options, read verbatim from the reference instance's entity registry
# (docs/12-field-notes.md, 2026-08-11).
#
# `printing` is the unambiguous one. The `paused_*` family — `paused_user`,
# `paused_filament_runout`, `paused_nozzle_clog` and the rest — is admitted by prefix
# because a paused print is a print: it has a row's worth of job in front of it, and a
# machine cannot pause what it is not running.
#
# **Everything else is refused, including the stages that plainly happen during a print.**
# `heating_hotend`, `filament_loading`, `calibrating_extrusion` and their neighbours occur
# inside a job *and* during a calibration a machine runs while idle, and there is no third
# signal here to tell those apart. Admitting them would let an idle calibration mint a
# phantom job — the exact failure `PrintStarted.derived` is bounded to prevent — while
# refusing them costs nothing at all: the stage reaches `printing` moments later, and the
# inference is a level that is read again on every transition rather than an edge that is
# missed once and gone.
_PRINTING_STAGE = "printing"
_PAUSED_STAGE_PREFIX = "paused_"

# The same question asked of `print_status`, whose vocabulary is upstream's `gcode_state`.
# Both sensors are consulted and **either one is enough**, because they fail separately: a
# reconnect leaves them unavailable at different moments, and the reading that survives is
# the one that answers. Neither can say `printing` about an idle machine, so the union
# widens availability without widening what is claimed.
_RUNNING_STATUSES = frozenset({"running", "pause"})

# Minutes per unit the `remaining_time` sensor may declare — every duration unit Home
# Assistant defines (`UnitOfTime`: d, h, min, s, ms, µs), with the long spellings, the
# `hr` abbreviation, the plain-ASCII `us`, and both micro glyphs (U+00B5 and U+03BC)
# riding along. The empty string is a sensor declaring nothing, read as minutes because
# minutes were this reader's original assumption. A unit absent from this table converts
# nothing: `_remaining_minutes` drops the reading rather than guess.
_MINUTES_PER_DECLARED_UNIT = {
    "d": 1440.0,
    "days": 1440.0,
    "h": 60.0,
    "hr": 60.0,
    "hours": 60.0,
    "min": 1.0,
    "minutes": 1.0,
    "": 1.0,
    "s": 1 / 60,
    "seconds": 1 / 60,
    "ms": 1 / 60_000,
    "milliseconds": 1 / 60_000,
    "µs": 1 / 60_000_000,
    "μs": 1 / 60_000_000,
    "us": 1 / 60_000_000,
    "microseconds": 1 / 60_000_000,
}

# A countdown a year long is not a countdown. Nothing a printer runs takes that long, so
# a figure past this line is upstream noise — refused in the same spirit as `Grams.of`
# refusing a figure too large to quantise.
_MINUTES_IN_A_YEAR = 525_600

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

# The same two endings, read off the status sensor instead of announced on the bus — the
# ledger's second, independent path to *this print stopped* (docs/05 §5.8).
#
# **It exists because the bus event is not delivered when it is needed most.** Upstream
# fires `event_print_finished` only on a transition it observed, and guards it with
# `previous_gcode_state != "unknown"` (`pybambu/models.py`) — a reconnection resets that to
# `unknown`, so a machine whose connection drops across its own ending announces nothing at
# all. Measured on the reference instance (docs/12-field-notes.md, 2026-08-08): a
# seven-hour print reached `finish` at 21:12:29 UTC after the sensor went unavailable at
# 21:08:13, and no terminal event was ever fired for it — 248.41 g the ledger never heard
# about, against 26 endings out of 26 that had been announced correctly before it.
#
# `offline` is deliberately absent, and so is `pause`. Both are states of the *connection*
# or of the machine, not of the job: the same print flickered offline and back dozens of
# times while it was running perfectly well.
#
# **Cancelled cannot be told from failed here, and is not guessed at.** Upstream
# distinguishes them by a `print_error` code on the MQTT payload, which the sensor does not
# carry; its `gcode_state` calls both `failed`. Both open a review either way, so the cost
# is a word on a card a human is already reading — and inventing the distinction would put
# a confident wrong label on the one path that exists for when nothing else spoke.
_DERIVED_OUTCOMES = {
    "finish": PrintJobState.FINISHED,
    "failed": PrintJobState.FAILED,
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
# connection mode — waited here through v1.4 and v2.5 for their keys to be read rather than
# guessed. They were read on 2026-08-11, off the reference instance's `core.entity_registry`
# (docs/12-field-notes.md), and they are in `PRINT_SENSOR_KEYS` above.
#
# **Two of the three guesses were right and the third was not**, which is the whole argument
# for having waited. `active_tray` and `online` are upstream's keys verbatim — `online` on a
# `binary_sensor` rather than a `sensor`, which discovery does not care about because it
# matches on platform and key. `connection_mode` does not exist anywhere in upstream: the
# reference instance's `sensor.…_modo_de_conexion_mqtt` reads `translation_key: "mqtt_mode"`.
# A constant frozen on the plausible-sounding guess would have discovered nothing and
# reported "the printer did not say" forever, for every user, with no error anywhere to
# suggest the key was ours rather than theirs.
#
# `MQTT_MODE_KEY` names the survivor of that correction so the reader below and the
# serialiser agree on one spelling.
MQTT_MODE_KEY = "mqtt_mode"


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
        self._unsubscribe_weights: CALLBACK_TYPE | None = None
        self._unsubscribe_status: CALLBACK_TYPE | None = None
        # The last weight-sensor reading each machine *published with tray keys in it*,
        # held here because a single instant is not enough to read it — see
        # `_on_weight_state_change` for the measurements that forced this. Keyed by
        # serial, so two machines printing at once can never read each other's figures,
        # and emptied both by `detach` and by each machine's own print starting.
        self._observations: dict[PrinterSerial, _WeightObservation] = {}
        # Which machines this gateway watched *start* the job they are now running. It is
        # the difference between "nothing was published during this job" and "this
        # gateway was not alive when the job began", and `_plan_at_ending` says why those
        # two absences must be answered differently.
        self._started: set[PrinterSerial] = set()
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
        # The third index: a weight-sensor change names an entity, and the plan it carries
        # belongs to exactly one machine.
        self._printer_by_weight = {
            entity_id: printer.serial
            for printer in discovery.printers
            if (entity_id := printer.sensors.get("print_weight")) is not None
        }
        # The fourth: a lifecycle *level* changed, and the edge it may describe — an ending
        # or a start — belongs to exactly one machine. Same shape as the weights above, and
        # resolved from the same discovery.
        #
        # **Both level sensors are indexed, and either one firing is enough.** They report
        # the same fact in two vocabularies and they fail separately: a reconnect leaves
        # them unavailable at different moments, and whichever recovers first is the one
        # that gets the ledger looking. `_on_status_state_change` answers each question from
        # the level rather than from the payload, so which of the two woke it never matters.
        self._printer_by_status = {
            entity_id: printer.serial
            for printer in discovery.printers
            for key in ("print_status", "stage")
            if (entity_id := printer.sensors.get(key)) is not None
        }

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

    def online(self, printer: PrinterSerial) -> bool | None:
        """Whether upstream considers this machine reachable — `None` when it did not say.

        A binary sensor, so the reading is `STATE_ON` against anything else; an absent or
        unavailable one is `None` rather than `False`, because *the sensor is not there* and
        *the printer is unreachable* are different facts and the tab renders them
        differently. Note the asymmetry with the print levels: a machine can be `online` and
        idle, so this answers nothing about whether a job is running (`_is_printing` does).
        """
        state = self._sensor_state(printer, "online")
        return None if state is None else state.state == STATE_ON

    def connection_mode(self, printer: PrinterSerial) -> str | None:
        """How upstream is talking to this machine — `mqtt_mode`, verbatim.

        The key `FUTURE_PRINT_SENSOR_KEYS` guessed as `connection_mode`, which exists
        nowhere; `MQTT_MODE_KEY` carries the correction and the reason it mattered. The
        value travels verbatim because its vocabulary is upstream's and this boundary has no
        business translating a word it has not captured the full set of.
        """
        return self._text_state(printer, MQTT_MODE_KEY)

    def active_tray(self, printer: PrinterSerial) -> int | None:
        """Which AMS slot is feeding right now, or `None` when the machine did not say.

        Read through the same total reader every other counter uses, so an unavailable
        sensor and an unparseable one are both `None` — the standing under-claim. The figure
        is *not* bounded to 1..4 here: the domain's `SlotIndex` owns that rule, and a tab
        that silently dropped a fifth slot would hide exactly the surprise worth seeing.
        """
        return self._layer(printer, "active_tray")

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
        # The weight sensors are followed for the same reason the trays are: what they say
        # has to be *observed over the job*, not sampled when it ends. Installed with the
        # bus listener because the held plan has exactly one consumer — the job events —
        # and a gateway nobody asked for job events from has nothing to hold.
        #
        # This only ever sees changes from here on, which is precisely why
        # `_plan_at_ending` keeps a live read for the job that was already running when
        # this subscription was made.
        if self._unsubscribe_weights is None and self._printer_by_weight:
            self._unsubscribe_weights = async_track_state_change_event(
                self._hass, list(self._printer_by_weight), self._on_weight_state_change
            )
        # The status sensors, for the same consumer and the reason `_DERIVED_OUTCOMES`
        # gives: this is the path that speaks when the bus does not. Installed here rather
        # than beside the trays because an ending has one receiver, and a gateway nobody
        # asked for job events from has nobody to tell.
        if self._unsubscribe_status is None and self._printer_by_status:
            self._unsubscribe_status = async_track_state_change_event(
                self._hass, list(self._printer_by_status), self._on_status_state_change
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
        if self._unsubscribe_weights is not None:
            self._unsubscribe_weights()
            self._unsubscribe_weights = None
        if self._unsubscribe_status is not None:
            self._unsubscribe_status()
            self._unsubscribe_status = None
        self._listeners.clear()
        self._job_listeners.clear()
        # Held observations die with the subscription that collected them. A reload builds
        # a new gateway, and a plan carried across it would describe a job nobody is
        # watching any more.
        self._observations.clear()
        self._started.clear()

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

    def _plan_at_ending(self, printer: PrinterSerial) -> dict[TrayRef, Grams] | None:
        """The per-tray breakdown a finishing job is charged with, or `None`.

        The held reading first: it is the last one published *during* this job, and the
        start discarded whatever preceded it.

        **With nothing held, the answer depends on whether this gateway saw the job
        begin**, because the two absences mean opposite things:

        - *We watched it start.* Then nothing has been published since the start, and the
          sensor still carries the figures of the job before this one. Reading it live
          would charge this print with its predecessor's plan — the exact defect this
          whole mechanism exists to end. The honest answer is `None`, and UC-04's
          missing-figure branch opens a review.
        - *We did not.* A config-entry reload — which every options change performs — or a
          restart mid-print builds a new gateway, and
          `async_track_state_change_event` only ever fires for *future* changes. So the
          held reading is empty while the correct plan is sitting in `hass.states` right
          now, published during a job this process was not alive for. Reading it live is
          a strict improvement: the sensor has been updated during this job, so it yields
          either this job's real figures or the shape-B nothing that was already
          tolerated. Without this branch a reload mid-print silently reports no usage,
          which is the very failure being fixed, reintroduced by the fix.

        Seeding the held reading at subscription time would answer the same case, and is
        deliberately not done: it would freeze a setup-time snapshot that the tracker
        immediately supersedes anyway, and it would still need the distinction above to
        avoid adopting a stale plan as though it had been observed. One live read, at the
        one moment the figure is consumed, is the same information later and in one place.
        """
        held = self._observations.get(printer)
        if held is not None:
            return held.plan
        if printer in self._started:
            return None
        state = self._sensor_state(printer, "print_weight")
        if state is None:
            return None
        observation = _tray_plan(printer, state)
        return observation.plan if observation is not None else None

    @callback
    def _on_weight_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Keep the last per-tray breakdown this machine actually published.

        Runs inside Home Assistant's event loop, so it must never raise: `_tray_plan` is
        total, and nothing here awaits or writes.

        **This exists because the sensor cannot be read at an instant.** Measured on the
        reference machine's recorder rows (docs/12-field-notes.md, 2026-08-08): the
        sensor is republished in occasional bursts — about eight over the three hours of
        one 220-layer print — and each burst is a *pair* of rows one to four seconds
        apart, the state value unchanged, one carrying the breakdown
        (`AMS 1 Tray 1: 31.33`) and one carrying no tray key at all. Sampling when a job
        ends therefore returned the breakdown or nothing depending on which half of a
        pair the event landed beside — that 220-layer two-colour print was charged
        nothing.

        So a shape without tray keys is treated as **a non-observation, never a
        correction**: it leaves whatever was last seen standing. The sensor going
        unavailable is the same silence. Only a reading that speaks the per-tray dialect
        replaces the held one.

        **The dedupe key is the whole observation**, not the tray half of it: an
        external-spool figure that changes while the AMS trays stand still is news, and
        comparing only the plan would swallow exactly the reading the warning below
        exists to announce.
        """
        printer = self._printer_by_weight.get(event.data["entity_id"])
        if printer is None:  # unreachable: the tracker watches only resolved entities
            return
        state = event.data["new_state"]
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        observation = _tray_plan(printer, state)
        if observation is None or self._observations.get(printer) == observation:
            return
        self._observations[printer] = observation
        # Both warnings are raised here rather than inside the translation, so each one
        # fires once per *new* observation. Inside, they would repeat on every republish
        # for the length of a print, which is how a real warning becomes scenery.
        if observation.external is not None:
            # The domain keys usage by tray (docs/02 §2.3); an external-spool figure has
            # no tray to land in. A spool on the direct feed now has a *location* that
            # names its machine, which is a different question from a consumption figure
            # having a tray to be deducted through — so this stays dropped, and dropping
            # it silently would be the optimistic lie this project exists to prevent.
            LOGGER.warning(
                "printer %s reports %r g on the external spool; this ledger tracks AMS "
                "consumption only, so the figure is not recorded",
                printer,
                observation.external,
            )
        for key in observation.other_ams:
            LOGGER.warning(
                "per-tray figure for %r on printer %s ignored; one AMS per printer is tracked",
                key,
                printer,
            )

    @callback
    def _on_status_state_change(self, event: Event[EventStateChangedData]) -> None:
        """The inferred lifecycle: a level moved, so ask what the machine is doing now.

        Runs inside Home Assistant's event loop, so it must never raise — both builders are
        assembled from the same total readers the bus path uses, and delivery happens in a
        background task exactly as it does there.

        **Only a transition counts**, and the transition is only the trigger. What is
        *reported* is read off the level afterwards, which is what makes this pass converge
        rather than merely react: an edge that was missed is repaired by the next unrelated
        edge, because every one of them re-reads the same truth. The bus events remain the
        fast path; this is the contract.

        Two questions, asked in this order and for a reason:

        - **Did it stop?** Answered from `print_status` alone (`_DERIVED_OUTCOMES`), because
          only that vocabulary tells a finish from a failure. `stage` reaches `idle` for
          both, and closing a failed print as `FINISHED` would skip the review its owner
          needs — so a `stage` change simply never matches here and falls through to the
          second question.
        - **Is it running something the ledger has no row for?** Answered from the level by
          `derived_start`, which is bounded by `PrintStarted.derived` on the far side.

        A `finish → offline → finish` bounce *is* two arrivals, and the reference machine
        did it five times in the ten minutes after one print ended. That is not filtered
        here, because filtering it would need this callback to remember what it already
        reported and a reload would forget. It is answered where the answer is durable
        instead: a derived ending may only close a running job and a derived start may only
        open one the ledger does not hold, so the first arrival does the work and every
        later one finds nothing to do.
        """
        printer = self._printer_by_status.get(event.data["entity_id"])
        if printer is None:  # unreachable: the tracker watches only resolved entities
            return
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        if new_state is None:
            return
        if old_state is not None and old_state.state == new_state.state:
            return
        outcome = _DERIVED_OUTCOMES.get(new_state.state)
        if outcome is not None:
            self._hass.async_create_background_task(
                self._deliver_job(self._derived_ending(printer, outcome)),
                name=f"filament_ledger derived ending on {printer}",
            )
            return
        start = self.derived_start(printer)
        if start is None:
            return
        self._hass.async_create_background_task(
            self._deliver_job(start), name=f"filament_ledger derived start on {printer}"
        )

    def derived_start(self, printer: PrinterSerial) -> PrintStarted | None:
        """That machine's print in progress as its levels read *right now*, or `None`.

        The mirror of `derived_ending`, and it exists for the mirror reason. Upstream guards
        `event_print_started` with `previous_gcode_state != "unknown"` exactly as it guards
        the finish, and a reconnection resets that to `unknown` — so a machine whose
        connection drops before its own start announces nothing, no row is opened, and the
        ending that follows finds nothing to close and is discarded. The whole print, and
        every gram it consumed, disappears in silence. `PrintStarted.derived` carries the
        measurements.

        Reading a level is only safe because a derived start cannot duplicate a job: a
        machine prints for hours and reads `printing` throughout, and every answer here is
        discarded unless the ledger independently holds *no* row for the print it names
        (`TrackPrintJob._started`). This is the same bargain the ending path makes, taken
        from the other end.

        **`plan` is `None`, and the held observation is deliberately left alone.** An
        announced start clears `_observations` and joins `_started`, because it knows the
        sensor still carries the previous job's figures. This one knows the opposite: the
        job has been running for some unknown while, so the weight sensor has already been
        republished *during* it, and the figures standing there are this print's own. Not
        touching either piece of state is what routes `_plan_at_ending` into its live-read
        branch — the branch written for a reload mid-print, which is the same situation
        arrived at by a different road.
        """
        if not self._is_printing(printer):
            return None
        return PrintStarted(
            name=self._job_name(printer),
            printer=printer,
            plan=None,
            printer_started_at=self._moment(printer, "start_time"),
            derived=True,
        )

    def _is_printing(self, printer: PrinterSerial) -> bool:
        """Whether this machine has a print in progress, by either level that can say so.

        `stage` first, because its vocabulary is the specific one: `printing` means exactly
        that, and the `paused_*` family is a print that is still a print. `print_status`
        answers the same question in `gcode_state`'s coarser words and is consulted when the
        first said nothing — an unavailable sensor is silence here, never a denial.

        Neither sensor can say `printing` about an idle machine, so consulting both widens
        what is *available* without widening what is claimed. `_PRINTING_STAGE` explains why
        the stages that merely happen during a print are refused.
        """
        stage = self._text_state(printer, "stage")
        if stage is not None and (
            stage == _PRINTING_STAGE or stage.startswith(_PAUSED_STAGE_PREFIX)
        ):
            return True
        status = self._text_state(printer, "print_status")
        return status is not None and status in _RUNNING_STATUSES

    def derived_ending(self, printer: PrinterSerial) -> PrintEnded | None:
        """That machine's ending as its status sensor reads *right now*, or `None`.

        The level, deliberately — where `_on_status_state_change` reads the arrival. This
        is the reconciliation question rather than the live one: *the printer is sitting on
        a finished print, is the ledger still holding it open?* Startup is the caller that
        has to ask it, because a machine that stopped while Home Assistant was down
        transitioned in front of nobody, and the port's own contract is that the printer
        does not replay what happened while nothing was listening.

        Reading a level is only safe because a derived ending cannot open a job: an idle
        machine reads `finish` all day, and every answer here is discarded unless the
        ledger independently holds a running row for that printer.
        """
        state = self._sensor_state(printer, "print_status")
        if state is None:
            return None
        outcome = _DERIVED_OUTCOMES.get(state.state)
        if outcome is None:
            return None
        return self._derived_ending(printer, outcome)

    def _derived_ending(self, printer: PrinterSerial, outcome: PrintJobState) -> PrintEnded:
        """One inferred ending, read through the very same sensors the bus path reads.

        Identical to `_translate_job_event`'s ending in every figure — the counters, the
        plan, the machine's own timestamps — and different in exactly one field. Sharing
        the readers is the point: a second path that sampled the printer differently would
        charge a different number depending on which signal happened to win.
        """
        return PrintEnded(
            outcome=outcome,
            name=self._job_name(printer),
            printer=printer,
            layer_reached=self._layer(printer, "current_layer"),
            total_layers=self._total_layers(printer),
            progress=self._progress(printer),
            reported_usage=self._plan_at_ending(printer),
            raw_gcode_state=self._text_state(printer, "print_status"),
            raw_print_error=self._error_code(printer),
            printer_started_at=self._moment(printer, "start_time"),
            printer_ended_at=self._moment(printer, "end_time"),
            derived=True,
        )

    def _translate_job_event(self, printer: PrinterSerial, event_type: object) -> PrintEvent | None:
        """One machine's bus event into domain terms, reading that machine's sensors.

        Every figure comes from the sensors of the printer the device id resolved to, which
        is the whole of what makes two machines printing at once safe: a reading taken from
        whichever job sensor discovery happened to keep would put one printer's layer count
        and one printer's grams on the other printer's job.

        The counters — layers, progress, error, the job name — are captured *now* because
        the ending is the last moment they describe this job: they reset when the next
        print starts. **The per-tray breakdown is not among them.** It is the reading held
        by `_on_weight_state_change`, for the two measured reasons stated there and here:
        the sensor flickers, and it is republished *after* the start event rather than
        before it.
        """
        if event_type == EVENT_PRINT_STARTED:
            # A new job inherits nothing. Upstream updates the weight sensor about
            # three-quarters of a minute *after* the start fires — measured: a job that
            # started at 13:49 saw its sensor update at 13:49:45 — so whatever stands
            # there now is the *previous* job's plan, and keeping it is how a 937-layer
            # print got charged the 2.1 g of the print before it. The plan is therefore
            # not known yet, and stays that way until this job's own reading arrives.
            #
            # **A republish landing in that gap would be held**, and it would be the old
            # job's figures. The measurements show no republish there (none between
            # 11:44:17 and the next job's 13:49:45), and the case is self-correcting
            # because the ending consumes the *last* good reading: the real plan arrives
            # later in the job and overwrites the stale one. It would only bite a print
            # that ended before its own plan was ever published — which lands in
            # `_plan_at_ending`'s honest-absence branch anyway, one job's figures being
            # wrong in the same direction the review queue exists to catch.
            self._observations.pop(printer, None)
            self._started.add(printer)
            return PrintStarted(
                name=self._job_name(printer),
                printer=printer,
                plan=None,
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
            reported_usage=self._plan_at_ending(printer),
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
        """The running job's name, from whichever sensor still remembers it.

        **`gcode_file_downloaded` wins whenever it speaks**, because it is the identity
        every historical row was named with — the `NNNN-name.gcode` form the ledger has
        written down since v1 — and preferring another source while it speaks would let
        the same job answer to two names between two glances. But that sensor publishes
        only at the moment the printer downloads a file: a Home Assistant restart
        mid-print leaves it `unavailable` until the *next* download, so everything that
        asks in that window — the Printer tab, a start, an ending — read an unknown
        print off a machine that was verifiably printing something.

        **`gcode_file` is the honest fallback for exactly that gap** — the key v2.5 wrote
        as `gcode_name`, which resolves to nothing (`PRINT_SENSOR_KEYS` tells that story).
        Upstream restores it on reconnect, so after a restart it is the one sensor still
        carrying the job's name. It answers in the slicer's own form — `0.28mm layer, 2
        walls, 15% infill.3mf` where the downloaded file reads `2585574-0.28mm layer, 2
        walls, 15% infill.gcode` — which is why it is a fallback and not a preference.

        **`subtask_name` is the last resort, and the one reading it must refuse is
        `unknown`.** The sensor parks on that literal between prints — measured at 18:20:11
        on 2026-08-11 — so passing it through would name a job the word *unknown* while
        claiming a sensor had spoken. Falling through to `UNKNOWN_JOB_NAME` says the same
        thing and says it as this module's own admission rather than as the printer's.

        A blank or whitespace answer falls through at every step rather than naming a job
        the empty string, and only when all three are silent does this reader answer
        `UNKNOWN_JOB_NAME` — the same under-claim every reader here applies, and never an
        exception.
        """
        downloaded = self._sensor_state(printer, "gcode_file_downloaded")
        if downloaded is not None and downloaded.state.strip():
            return downloaded.state
        restored = self._sensor_state(printer, "gcode_file")
        if restored is not None and restored.state.strip():
            return restored.state
        subtask = self._sensor_state(printer, "subtask_name")
        if subtask is not None and subtask.state.strip() and subtask.state.strip() != "unknown":
            return subtask.state
        return UNKNOWN_JOB_NAME

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

        **The sensor names its own unit, and this reader converts by it.** On the
        reference instance the upstream sensor speaks decimal hours — measured
        2026-08-09: state `"6.35"`, `unit_of_measurement: "h"` — so a reader assuming
        whole minutes choked on every real print and this figure never reached the
        screen. Every duration unit Home Assistant defines converts here — days, hours,
        minutes, seconds, milliseconds, microseconds — spelling and case normalised
        first. A sensor declaring **no unit at all** is read as minutes, the reader's
        original assumption; a sensor declaring a unit the table does not hold is
        dropped instead, because a figure converted by a guessed unit is confidently
        wrong where a dash is merely silent — the module's under-claim rule again. The
        result rounds to the nearest whole minute — the finest grain the screen shows —
        and a figure past a year is refused as noise no real countdown could mean.

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
        unit = str(state.attributes.get("unit_of_measurement") or "").strip().lower()
        scale = _MINUTES_PER_DECLARED_UNIT.get(unit)
        if scale is None:
            LOGGER.debug("remaining_time declares %r, a unit this reader does not know", unit)
            return None
        try:
            minutes = round(float(state.state) * scale)
        # A reading of "nan" or "inf" parses as a float but has no whole-minute reading,
        # so `round` refusing it lands here with everything that never parsed at all.
        except ValueError, OverflowError:
            LOGGER.debug("remaining_time reads %r, which is not a duration", state.state)
            return None
        if minutes > _MINUTES_IN_A_YEAR:
            LOGGER.debug(
                "remaining_time reads %r %s, which no real countdown means", state.state, unit
            )
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
    non-numeric shapes are upstream noise, not data.

    **Total, including the shapes a type check waves through.** `Grams.of` raises
    `InvalidOperation` on `inf`, `-inf` and figures too large to quantise, and
    `ValueError` on `NaN` — all four are floats, so the guard above admits every one of
    them. That gap was survivable while this ran twice per job, from a coroutine; it is
    not now that it runs on every republish from `_on_weight_state_change`, which is a
    `@callback` promising the event loop it never raises. Caught the same way
    `_reel_weight` catches it, for the same reason.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        grams = Grams.of(value)
    except ArithmeticError, ValueError:
        LOGGER.debug("per-tray figure %r is not a usable quantity; skipped", value)
        return None
    return None if grams.is_negative else grams


@dataclass(frozen=True, slots=True)
class _WeightObservation:
    """One weight-sensor reading, whole — the unit the gateway holds and compares.

    Everything the reading said, not merely the part that is consumed: `plan` is what a
    job is charged with, and the other two are what has to be *announced* about it. They
    travel together because they are deduped together — a reading whose trays stand still
    while its external-spool figure moves is a new observation, and comparing the plan
    alone would silently drop the one reading the warning exists for.
    """

    plan: dict[TrayRef, Grams]
    #: The external-spool figure, verbatim, when the reading named one.
    external: object | None = None
    #: The keys naming an AMS this ledger does not track, verbatim, in reading order.
    other_ams: tuple[str, ...] = ()


def _tray_plan(printer: PrinterSerial, state: State) -> _WeightObservation | None:
    """One weight-sensor reading, translated — or `None` when it said nothing.

    Total by construction, like `_read`: every malformed shape becomes a skipped key or a
    `None`, so the caller can run bare inside the event loop.

    `None` is **the shape that carries no per-tray key at all** — the other half of each
    flicker pair, and a sensor that never had a breakdown. That is silence, and the
    caller's whole job is to leave a real reading standing in its place. A shape that
    *does* speak the dialect translates to an observation whose plan may be empty, which
    is the printer naming no AMS trays and is a different fact from silence
    (docs/04-use-cases.md UC-04).

    Nothing here warns. Both of the things worth saying out loud — an external-spool
    figure and a second AMS — ride out on the observation instead, so the caller can say
    them once per new reading rather than once per republish.
    """
    weights: dict[TrayRef, Grams] = {}
    external: object | None = None
    other_ams: list[str] = []
    recognised = False
    for key, value in state.attributes.items():
        if key == _EXTERNAL_SPOOL_KEY:
            recognised = True
            external = value
            continue
        match = _TRAY_WEIGHT_KEY.fullmatch(key)
        if match is None:
            continue
        recognised = True
        ams, slot = int(match.group(1)), int(match.group(2))
        if ams != TRACKED_AMS.value:
            other_ams.append(key)
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
    if not recognised:
        return None
    return _WeightObservation(plan=weights, external=external, other_ams=tuple(other_ams))


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
