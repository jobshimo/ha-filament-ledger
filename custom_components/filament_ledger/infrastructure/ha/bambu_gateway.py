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
slot translation lives here and nowhere else, per docs/05 §5.8 — and so does the
per-tray-attribute translation: `AMS 1 Tray 1` becomes `SlotIndex(1)`, and an
`External Spool` figure is dropped with a warning, because the domain keys usage by AMS
slot and inventing a fifth slot would be a lie with a number on it.

Job events are filtered by device. The bus carries `bambu_lab_event` for every machine
and the payload names only a device id, so the gateway keeps the id of the printer whose
job sensors it discovered — the trays hang off the AMS device, the job sensors off the
printer — and ignores everything else.

Two conscious limitations, both documented rather than discovered:

- **One printer, one AMS.** v1 targets a single ledger on a single machine; if the
  registry ever holds several AMS units or several printers, the first (by identity)
  wins and a warning names the ones ignored.
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
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from ...domain.error import InvalidValueError
from ...domain.port.printer_gateway import PrintListener, TrayListener
from ...domain.value.colour import Colour
from ...domain.value.grams import Grams
from ...domain.value.identifiers import ABSENT_TAG_SENTINEL, SlotIndex, TagUid
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
PRINT_SENSOR_KEYS = frozenset(
    {
        "print_weight",
        "print_status",
        "current_layer",
        "total_layers",
        "print_progress",
        "gcode_file_downloaded",
        "print_error",
    }
)

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
        self._entity_by_slot = _discover_trays(hass)
        self._slot_by_entity = {entity_id: slot for slot, entity_id in self._entity_by_slot.items()}
        self._print_sensors, self._printer_device_id = _discover_print_sensors(hass)

    @property
    def dormant(self) -> bool:
        """Whether discovery found no trays — `ha-bambulab` absent or not yet set up.

        The on-demand sync reads this to answer honestly: a dormant gateway has no trays
        to report, which is a different fact from four empty ones. Reloading the entry
        after the upstream integration appears re-runs discovery, per the module policy.
        """
        return not self._entity_by_slot

    def subscribe(self, listener: TrayListener) -> None:
        """Register a listener for tray changes. Registration itself does no I/O.

        The state-change tracker is installed on the first subscription and shared by all
        of them; with no trays discovered this is a no-op, because there is nothing to
        watch and inventing entities to watch would be worse than silence.
        """
        if not self._entity_by_slot:
            LOGGER.debug("no %s tray entities found; the gateway stays dormant", UPSTREAM_PLATFORM)
            return
        self._listeners.append(listener)
        if self._unsubscribe is None:
            self._unsubscribe = async_track_state_change_event(
                self._hass, list(self._slot_by_entity), self._on_tray_state_change
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

    async def current_trays(self) -> dict[SlotIndex, TrayReading]:
        """Every tray as last reported, keyed by slot, in slot order.

        A tray whose sensor is missing, unavailable or malformed is *omitted*, never
        reported empty: absence of data and absence of a spool are different facts, and
        conflating them would unmount a spool because a sensor blinked (docs/03 §3.8).
        """
        readings: dict[SlotIndex, TrayReading] = {}
        for slot, entity_id in sorted(self._entity_by_slot.items()):
            reading = _read(slot, self._hass.states.get(entity_id))
            if reading is not None:
                readings[slot] = reading
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
        slot = self._slot_by_entity.get(event.data["entity_id"])
        if slot is None:  # unreachable: the tracker watches only resolved entities
            return
        reading = _read(slot, event.data["new_state"])
        if reading is None:
            return
        self._hass.async_create_background_task(
            self._deliver(reading), name=f"filament_ledger tray {slot} change"
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
            return PrintStarted(name=self._job_name(), plan=self._per_tray_weights())
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

    def _per_tray_weights(self) -> dict[SlotIndex, Grams] | None:
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
        weights: dict[SlotIndex, Grams] = {}
        recognised = False
        for key, value in state.attributes.items():
            if key == _EXTERNAL_SPOOL_KEY:
                recognised = True
                # The domain keys usage by AMS slot (docs/02 §2.3); an external-spool
                # figure has no slot to land in. Dropping it silently would be the
                # optimistic lie this project exists to prevent, so it is at least loud.
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
            ams, tray = int(match.group(1)), int(match.group(2))
            if ams != 1:
                LOGGER.warning("per-tray figure for %r ignored; v1 tracks a single AMS", key)
                continue
            grams = _weight(value)
            if grams is None:
                LOGGER.debug("per-tray figure for %r reads %r; skipped", key, value)
                continue
            try:
                weights[SlotIndex(tray)] = grams
            except InvalidValueError:
                LOGGER.debug("per-tray key %r names a slot outside 1..4; skipped", key)
        return weights if recognised else None


def _discover_trays(hass: HomeAssistant) -> dict[SlotIndex, str]:
    """Resolve the AMS tray sensors to entity ids, keyed by our slot numbering.

    The rule, from the shapes captured in docs/12: `platform == "bambu_lab"` selects the
    upstream integration, `translation_key == "tray"` discriminates tray sensors from the
    printer's other sensors, and the `unique_id` suffix `_tray_<n>` carries the slot.
    """
    groups: dict[str, dict[SlotIndex, str]] = {}
    for entry in er.async_get(hass).entities.values():
        if entry.platform != UPSTREAM_PLATFORM or entry.translation_key != TRAY_TRANSLATION_KEY:
            continue
        ams, marker, ordinal = entry.unique_id.rpartition(_TRAY_MARKER)
        if not marker or not ordinal.isdigit():
            LOGGER.debug("tray unique_id %r has no _tray_<n> suffix; skipped", entry.unique_id)
            continue
        try:
            slot = SlotIndex(int(ordinal))
        except InvalidValueError:
            LOGGER.debug("tray unique_id %r names a slot outside 1..4; skipped", entry.unique_id)
            continue
        groups.setdefault(ams, {})[slot] = entry.entity_id
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


def _discover_print_sensors(hass: HomeAssistant) -> tuple[dict[str, str], str | None]:
    """Resolve the printer's job sensors, keyed by translation key, plus its device id.

    The job sensors hang off the printer device — the trays hang off the AMS device — so
    these registry entries are also where the printer's device id comes from, and that id
    is what filters `bambu_lab_event` down to the machine this ledger tracks.
    """
    groups: dict[str, dict[str, str]] = {}
    for entry in er.async_get(hass).entities.values():
        if (
            entry.platform != UPSTREAM_PLATFORM
            or entry.translation_key not in PRINT_SENSOR_KEYS
            or entry.device_id is None
        ):
            continue
        groups.setdefault(entry.device_id, {})[entry.translation_key] = entry.entity_id
    if not groups:
        LOGGER.debug("no %s print sensors in the entity registry", UPSTREAM_PLATFORM)
        return {}, None
    first = min(groups)
    if len(groups) > 1:
        LOGGER.warning(
            "multiple printers in the registry (%s); v1 tracks a single printer, using %s",
            sorted(groups),
            first,
        )
    return groups[first], first


def _read(slot: SlotIndex, state: State | None) -> TrayReading | None:
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
        LOGGER.debug("tray %s reports no usable 'empty' flag (%r); reading skipped", slot, empty)
        return None
    if empty:
        # An emptied tray describes no spool. Whatever name or colour the attributes
        # still carry is a leftover of the previous occupant, not an observation.
        return TrayReading(slot=slot, tag=None, empty=True)
    return TrayReading(
        slot=slot,
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
