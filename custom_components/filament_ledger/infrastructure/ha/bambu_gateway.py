"""The AMS, read through `ha-bambulab` — the inbound half of the printer boundary.

This is the only module in the project that touches another integration, and docs/05 §5.8
writes down exactly what it may touch: the entity registry, the state machine, and state
change events. Nothing is imported from `custom_components.bambu_lab` — its coordinator
and config entry are internals with no compatibility promise, and depending on them turns
an upstream refactor into a corrupted ledger.

Discovery never matches entity ids. The reference instance runs Spanish, so the tray
sensor is `sensor.…_ams_1_bandeja_1` (docs/12-field-notes.md); anything keyed on the
English string breaks for every user not running the developer's language. What *is*
stable is upstream's own identity: `platform == "bambu_lab"`, `translation_key == "tray"`,
and a `unique_id` ending in `_tray_<n>` — the slot translation lives here and nowhere
else, per docs/05 §5.8.

Two conscious limitations, both documented rather than discovered:

- **One printer, one AMS.** v1 targets a single ledger on a single machine; if the
  registry ever holds several AMS units, the first (by unique id) wins and a warning names
  the ones ignored.
- **No late binding.** If `ha-bambulab` is not set up when this entry loads, the gateway
  constructs dormant: `current_trays()` is empty and `subscribe` is a logged no-op.
  docs/05 §5.8 demands no startup-order tolerance, so watching the entity registry for
  trays appearing later is deliberately deferred — reloading this entry after the upstream
  integration appears re-runs discovery.

Job lifecycle (`bambu_lab_event` on the HA bus) is UC-04/05's concern and deliberately
absent: when it arrives, `subscribe` grows a second registration via
`hass.bus.async_listen` and `detach` drops it — a seam, not a redesign.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from ...domain.error import InvalidValueError
from ...domain.port.printer_gateway import TrayListener
from ...domain.value.colour import Colour
from ...domain.value.identifiers import ABSENT_TAG_SENTINEL, SlotIndex, TagUid
from ...domain.value.tray_reading import TrayReading

if TYPE_CHECKING:
    from homeassistant.core import CALLBACK_TYPE

LOGGER = logging.getLogger(__name__)

UPSTREAM_PLATFORM = "bambu_lab"
TRAY_TRANSLATION_KEY = "tray"
_TRAY_MARKER = "_tray_"


class BambuLabGateway:
    """`PrinterGateway`, implemented against `ha-bambulab`'s public entity surface.

    Discovery runs once, at construction — registry reads are synchronous and the
    composition root constructs the gateway inside `async_setup_entry`, so the entity
    population is as settled as it is ever going to be without late binding.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._listeners: list[TrayListener] = []
        self._unsubscribe: CALLBACK_TYPE | None = None
        self._entity_by_slot = _discover_trays(hass)
        self._slot_by_entity = {entity_id: slot for slot, entity_id in self._entity_by_slot.items()}

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
        """Stop listening. Idempotent, because it runs twice on a clean unload.

        The composition root calls it at the top of `async_unload_entry` — Home Assistant
        runs `async_on_unload` callbacks only after that function returns, and a tray
        change in the gap would reach a closed database — and the registration made with
        `entry.async_on_unload` then runs it again, kept as the safety net for the
        setup-failure paths that never reach unload.
        """
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._listeners.clear()

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
