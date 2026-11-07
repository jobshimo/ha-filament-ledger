"""Which machines, AMS units, trays and holders `ha-bambulab` exposes — the printer boundary's discovery.

Read from the entity and device registries only, by upstream's own identity
(`platform == "bambu_lab"` plus the `translation_key`), never by entity id: the reference
instance runs Spanish, and anything keyed on the English string breaks for every user not
running the developer's language (docs/05 §5.8, ADR-0009).

Split out of `bambu_gateway.py`, whose module docstring still states the whole boundary's
contract — serial resolution, tray attribution through the device registry, which AMS and
which holder, and the two conscious limitations. `BambuLabGateway` runs `discover` at
construction and again whenever the registry changes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ...domain.error import InvalidValueError
from ...domain.value.identifiers import (
    MIN_EXTERNAL_HOLDER,
    UNIDENTIFIED_PRINTER,
    AmsIndex,
    HolderIndex,
    PrinterSerial,
    SlotIndex,
    TrayRef,
)

LOGGER = logging.getLogger(__name__)


UPSTREAM_PLATFORM = "bambu_lab"


TRAY_TRANSLATION_KEY = "tray"


_TRAY_MARKER = "_tray_"


# The holder sensor's own key, read off the live instances on 2026-09-23 alongside the
# `tray` key beside it (`ha-bambulab` 2.2.25, `definitions.py`). One entity per holder,
# hanging off that holder's own device, carrying `empty`, `name`, `type`, `colour` and the
# rest of a tray's vocabulary — which is what lets the Printer tab render a holder card the
# way it renders a tray card.
EXTERNAL_SPOOL_TRANSLATION_KEY = "external_spool"


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
# the `MQTT_MODE_KEY` block in `bambu_gateway` names, caught in this boundary's own code.
#
# `stage`, `subtask_name`, `online`, `active_tray` and `mqtt_mode` joined in the same pass,
# from the same read of the same registry. `stage` is the one that matters most: see
# `_PRINTING_STAGE` in `bambu_gateway` and `BambuLabGateway._is_printing`.
#
# `printable_objects` joined on 2026-09-03, off the live instance's registry
# (`<serial>_printable_objects`), and it is never read for a figure. Its *change* is the one
# signal that says this job's 3MF has been parsed — including for a re-print whose per-tray
# figures equal the previous print's, which republishes nothing else at all.
# `BambuLabGateway._on_objects_state_change` carries the measurement.
#
# `printer_name` joined on 2026-09-23, off the live instances' registries. It is the one key
# here whose `unique_id` suffix is *not* the translation key — upstream writes
# `<serial>_name` — which is why `_UNIQUE_ID_KEY` exists: added without it, `_serial_of`
# would strip `_printer_name`, match nothing, and silently resolve no serial from that row.
# It is display only; the serial remains the identity (`BambuLabGateway.printer_name`).
PRINT_SENSOR_KEYS = frozenset(
    {
        "print_weight",
        "printable_objects",
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
        "printer_name",
    }
)


# The sensors whose `unique_id` suffix differs from their `translation_key`, by key. Only
# `printer_name` is in here, and the correction is the point: upstream's entity description
# reads `key="name"`, so its `unique_id` is `<serial>_name`. `_serial_of` consults this
# table before falling back to the key itself, so one row's irregular spelling costs no
# other row anything.
_UNIQUE_ID_KEY = {"printer_name": "name"}


# The ordinal a caller that names none means, and the one an unnumberable AMS unit is
# followed under. **Not a ceiling any more.** Until v2.9 this constant was `TRACKED_AMS`:
# the registry's tray `unique_id` carries the AMS unit's *serial* and never its ordinal, so
# every unit but the first was dropped with a warning. The device registry states the
# ordinal outright — `<model>_<serial>_AMS_<n>` on the AMS device's own name — so each unit
# is now followed under the number the printer gives it, and this is what is left: the
# default a one-unit machine takes, and the answer a payload naming no AMS resolves to.
FIRST_AMS = AmsIndex(1)


# The AMS device's name, as `ha-bambulab` writes it: `<device_type>_<serial>_AMS_<ordinal>`,
# read off the live X2D and A1 on 2026-09-23. **`device.name`, never `name_by_user`**: Home
# Assistant stores a user's rename in the latter and leaves the former exactly as the
# integration wrote it, so a household that renamed its AMS units cannot move their ordinals.
_AMS_DEVICE_NAME = re.compile(r"_AMS_(\d+)$")


# The holder device's identifier, and the tail of the holder sensor's `unique_id`:
# `<serial>_ExternalSpool` for the first and `<serial>_ExternalSpool2` for the second
# (`coordinator.get_virtual_tray_device`, `sensor.py`). The empty suffix is the first
# holder, which is the same statement `FIRST_HOLDER` makes everywhere else.
_HOLDER_TAIL = re.compile(rf"_ExternalSpool(\d*)(?:_{EXTERNAL_SPOOL_TRANSLATION_KEY})?$")


@dataclass(frozen=True, slots=True)
class DiscoveredPrinter:
    """One machine, as the entity and device registries describe it.

    `device_id` is `None` for a machine assembled out of trays alone — an AMS whose printer
    sensors did not resolve. Such a machine has no job events to hear and no status to show,
    but it still holds the trays this ledger mounts spools into, which is why it is a
    printer here rather than a special case everywhere else.

    `ams_units` is every ordinal whose trays were followed, and `holders` maps each direct
    feed to the entity that describes it. Both can be empty, and empty means *nothing was
    discovered* rather than *the machine has none*: the AMS view floors the holders at one,
    because every Bambu printer has a holder whether or not an entity says so, and that
    flooring is the view's judgement rather than a claim made here.
    """

    serial: PrinterSerial
    device_id: str | None
    sensors: dict[str, str]
    trays: dict[TrayRef, str]
    ams_units: tuple[AmsIndex, ...] = ()
    holders: dict[HolderIndex, str] = field(default_factory=dict)


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


@dataclass(frozen=True, slots=True)
class _DeviceFacts:
    """What the device registry says about one upstream device (ADR-0009).

    Every field is optional, because every field is an inference from a shape upstream is
    free to change and none of them is worth a crash. The fallbacks that answer a `None`
    are the `unique_id` rules that predate this reader, so an upstream that renamed its
    devices tomorrow would cost this ledger exactly the precision the registry was adding.
    """

    #: The machine this device hangs off, read through `via_device` — the AMS units and the
    #: holders both point at their printer, whose own identifier is its serial.
    printer: PrinterSerial | None = None
    #: The ordinal in an AMS device's name. `None` for every device that is not an AMS.
    ams: AmsIndex | None = None
    #: Which direct feed a holder device is. `None` for every device that is not one.
    holder: HolderIndex | None = None


def device_entry(hass: HomeAssistant, device_id: str) -> dr.DeviceEntry | None:
    """One device, or `None` — including when there is no device registry to ask.

    `dr.async_get` raises when the registry has not been set up, which is a state this
    boundary has no business crashing on: it happens in a test harness and it could happen
    during a very early setup, and the answer in both cases is *fall back to the unique_id
    rules*, not *fail the config entry*.
    """
    try:
        registry = dr.async_get(hass)
    except RuntimeError, KeyError:
        return None
    return registry.async_get(device_id)


def _device_topology(hass: HomeAssistant) -> dict[str, _DeviceFacts]:
    """Every upstream device, and what its place in the tree says about it.

    Read in one pass rather than per entity: a household has a handful of devices and
    dozens of entities, and resolving `via_device` per entity would walk the same parents
    over and over.
    """
    try:
        registry = dr.async_get(hass)
    except RuntimeError, KeyError:
        LOGGER.debug("no device registry to read; discovery falls back to unique_ids")
        return {}
    facts: dict[str, _DeviceFacts] = {}
    for device in registry.devices.values():
        parent = registry.async_get(device.via_device_id) if device.via_device_id else None
        facts[device.id] = _DeviceFacts(
            printer=_serial_of_identifier(_upstream_identifier(parent)),
            ams=_ams_ordinal(device.name),
            holder=_holder_of(_upstream_identifier(device)),
        )
    return facts


def _upstream_identifier(device: dr.DeviceEntry | None) -> str | None:
    """The value of this device's `(bambu_lab, …)` identifier, if it has one.

    A device may carry identifiers from several integrations; only upstream's says anything
    about a printer, and a device with none is somebody else's entirely.
    """
    if device is None:
        return None
    for domain, value in device.identifiers:
        if domain == UPSTREAM_PLATFORM:
            return value
    return None


def _serial_of_identifier(value: str | None) -> PrinterSerial | None:
    """A printer device's identifier *is* its serial — upstream writes `(DOMAIN, serial)`."""
    if value is None or not value.strip():
        return None
    return PrinterSerial(value)


def _ams_ordinal(name: str | None) -> AmsIndex | None:
    """The `_AMS_<n>` tail of an AMS device's name, as the printer numbers the unit.

    An AMS HT reports a number upstream derives differently — 128 and up — and it is
    accepted here rather than refused: `AmsIndex` has no ceiling for exactly this reason,
    and a unit whose trays this ledger can mount into is worth following even though
    upstream's sixteen-slot weight loop never names it in a consumption figure.
    """
    if name is None:
        return None
    match = _AMS_DEVICE_NAME.search(name)
    if match is None:
        return None
    try:
        return AmsIndex(int(match.group(1)))
    except InvalidValueError:
        LOGGER.debug("AMS device %r names an ordinal below one; skipped", name)
        return None


def _holder_of(value: str | None) -> HolderIndex | None:
    """Which direct feed a `<serial>_ExternalSpool<suffix>` string names.

    Takes both spellings the suffix appears in — the holder device's identifier and the
    holder sensor's `unique_id`, which appends its own translation key — so the registry
    answer and the fallback are read by one rule rather than two that could disagree.
    """
    if value is None:
        return None
    match = _HOLDER_TAIL.search(value)
    if match is None:
        return None
    tail = match.group(1)
    try:
        return HolderIndex(int(tail) if tail else MIN_EXTERNAL_HOLDER)
    except InvalidValueError:
        LOGGER.debug("external spool %r names a holder no machine has; skipped", value)
        return None


def discover(hass: HomeAssistant) -> PrinterDiscovery:
    """Read both registries once and assemble the machines out of them.

    Printers first, because a tray is named after the machine that holds it and the serial
    only exists on the job sensors. The device topology beside them, because it is what
    attributes an AMS unit and numbers it. Trays and holders last, attributed to those
    names.
    """
    topology = _device_topology(hass)
    sensors_by_device, serial_by_device = _job_sensors(hass)
    names = _resolve_names(sensors_by_device, serial_by_device)
    followed = tuple(sorted(set(names.values())))
    trays, ams_units = _discover_trays(hass, followed, topology)
    holders = _discover_holders(hass, followed, topology)
    printers = [
        DiscoveredPrinter(
            serial=serial,
            device_id=device_id,
            sensors=sensors_by_device[device_id],
            trays=trays.pop(serial, {}),
            ams_units=ams_units.get(serial, ()),
            holders=holders.get(serial, {}),
        )
        for device_id, serial in sorted(names.items(), key=lambda pair: pair[1])
    ]
    # Whatever trays are left name a machine with no job sensors: an AMS discovered on its
    # own, under the reserved serial `_discover_trays` gives it. It is a printer this ledger
    # mounts into, so it is one of these too.
    printers.extend(
        DiscoveredPrinter(
            serial=serial,
            device_id=None,
            sensors={},
            trays=entities,
            ams_units=ams_units.get(serial, ()),
            holders=holders.get(serial, {}),
        )
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
    hass: HomeAssistant,
    printers: tuple[PrinterSerial, ...],
    topology: dict[str, _DeviceFacts],
) -> tuple[dict[PrinterSerial, dict[TrayRef, str]], dict[PrinterSerial, tuple[AmsIndex, ...]]]:
    """Resolve the AMS tray sensors to entity ids, keyed by the tray each one describes.

    The rule, from the shapes captured in docs/12: `platform == "bambu_lab"` selects the
    upstream integration, `translation_key == "tray"` discriminates tray sensors from the
    printer's other sensors, and the `unique_id` suffix `_tray_<n>` carries the slot. What
    is left in front of that suffix identifies the AMS unit, and is what groups a unit's
    four trays together.

    **Every unit the registry numbers is followed, since v2.9.** `_ams_printer` says which
    machine a group belongs to and `_numbered_units` says which ordinal each one is; before
    the device registry was read, neither question had an answer and the first group by
    identity was the only one kept.

    Returns the trays per machine and the ordinals per machine — the second so the Printer
    tab can render a block per unit without re-deriving it from the tray keys, which would
    hide a unit whose four sensors are all unavailable.
    """
    slots_by_group: dict[str, dict[int, str]] = {}
    device_by_group: dict[str, str | None] = {}
    for entry in er.async_get(hass).entities.values():
        if entry.platform != UPSTREAM_PLATFORM or entry.translation_key != TRAY_TRANSLATION_KEY:
            continue
        unit, marker, slot = entry.unique_id.rpartition(_TRAY_MARKER)
        if not marker or not slot.isdigit():
            LOGGER.debug("tray unique_id %r has no _tray_<n> suffix; skipped", entry.unique_id)
            continue
        slots_by_group.setdefault(unit, {})[int(slot)] = entry.entity_id
        device_by_group.setdefault(unit, entry.device_id)
    if not slots_by_group:
        LOGGER.debug("no %s tray sensors in the entity registry", UPSTREAM_PLATFORM)
        return {}, {}

    units: dict[PrinterSerial, list[str]] = {}
    for group in sorted(slots_by_group):
        printer = _ams_printer(group, device_by_group[group], printers, topology)
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
    ordinals: dict[PrinterSerial, tuple[AmsIndex, ...]] = {}
    for printer, found in units.items():
        numbered = _numbered_units(printer, found, device_by_group, topology)
        trays: dict[TrayRef, str] = {}
        for ordinal, numbered_group in sorted(numbered.items()):
            trays.update(_trays_of(printer, ordinal, slots_by_group[numbered_group]))
        resolved[printer] = trays
        ordinals[printer] = tuple(sorted(numbered))
    return resolved, ordinals


def _ams_printer(
    group: str,
    device_id: str | None,
    printers: tuple[PrinterSerial, ...],
    topology: dict[str, _DeviceFacts],
) -> PrinterSerial | None:
    """Which machine an AMS group belongs to — the registry first, the string second.

    **The registry answer is only taken for a machine this ledger already follows.** The
    `via_device` chain can name a printer whose own job sensors resolved no serial, and
    that machine is deliberately not followed (`_resolve_names`); accepting the name here
    would smuggle it back in through a side door, under a serial no job sensor agrees with.
    """
    facts = topology.get(device_id) if device_id is not None else None
    if facts is not None and facts.printer is not None and facts.printer in printers:
        return facts.printer
    return _printer_of(group, printers)


def _numbered_units(
    printer: PrinterSerial,
    found: list[str],
    device_by_group: dict[str, str | None],
    topology: dict[str, _DeviceFacts],
) -> dict[AmsIndex, str]:
    """Which ordinal each of one machine's AMS groups is, by the printer's own numbering.

    Three outcomes, in the order they are reached:

    - **Every group is numbered.** Each is followed under its own ordinal, which is v2.9's
      whole point: a second unit's trays stop being dropped.
    - **None is.** The registry has no device for them, or upstream stopped writing
      `_AMS_<n>` in the device name. The first by identity is followed as AMS 1 — v1's
      behaviour verbatim — and the rest are named in a warning, because placing them would
      be a guess and dropping them silently would be worse.
    - **Some are and some are not.** The numbered ones are followed and the rest are
      dropped with a warning. There is no least-wrong ordinal to give the others: every
      free number is one upstream might mean for a unit it did number.
    """
    numbered: dict[AmsIndex, str] = {}
    unnumbered: list[str] = []
    for group in found:
        device_id = device_by_group.get(group)
        facts = topology.get(device_id) if device_id is not None else None
        ordinal = facts.ams if facts is not None else None
        if ordinal is None:
            unnumbered.append(group)
        elif ordinal in numbered:
            LOGGER.warning(
                "printer %s has two AMS units the registry both calls AMS %s (%s and %s); "
                "the first keeps the ordinal and the second is not followed",
                printer,
                ordinal,
                numbered[ordinal],
                group,
            )
        else:
            numbered[ordinal] = group
    if not unnumbered:
        return numbered
    if not numbered:
        if len(unnumbered) > 1:
            LOGGER.warning(
                "printer %s has several AMS units the registry numbers none of (%s); the "
                "first by identity is followed as AMS %s and the rest are not, because "
                "numbering them here would be a guess",
                printer,
                unnumbered,
                FIRST_AMS,
            )
        numbered[FIRST_AMS] = unnumbered[0]
        return numbered
    LOGGER.warning(
        "printer %s has AMS units the registry does not number (%s) beside units it does "
        "(%s); the unnumbered ones are not followed, because every ordinal left is one "
        "upstream might mean for a unit it did number",
        printer,
        unnumbered,
        sorted(str(ordinal) for ordinal in numbered),
    )
    return numbered


def _trays_of(
    printer: PrinterSerial, ams: AmsIndex, ordinals: dict[int, str]
) -> dict[TrayRef, str]:
    trays: dict[TrayRef, str] = {}
    for ordinal, entity_id in ordinals.items():
        try:
            tray = TrayRef(printer=printer, ams=ams, slot=SlotIndex(ordinal))
        except InvalidValueError:
            LOGGER.debug("tray %s of %s names a slot outside 1..4; skipped", ordinal, printer)
            continue
        trays[tray] = entity_id
    return trays


def _discover_holders(
    hass: HomeAssistant,
    printers: tuple[PrinterSerial, ...],
    topology: dict[str, _DeviceFacts],
) -> dict[PrinterSerial, dict[HolderIndex, str]]:
    """Resolve the direct-feed sensors to entity ids, keyed by the holder each describes.

    The same two-source rule the trays follow: the holder device's own identifier says
    which feed it is and its `via_device` says whose, and the sensor's `unique_id` —
    `<model>_<serial>_ExternalSpool<suffix>_external_spool` — answers both when there is no
    device registry to ask.

    A machine with no holder entity simply has no entry here. That is not a claim it has no
    holder: upstream publishes one of these per holder the *printer* reports, and a
    firmware that reports none still has a physical one the user can mount a reel on. The
    AMS view floors the union at the first holder for exactly that reason.
    """
    holders: dict[PrinterSerial, dict[HolderIndex, str]] = {}
    for entry in er.async_get(hass).entities.values():
        if (
            entry.platform != UPSTREAM_PLATFORM
            or entry.translation_key != EXTERNAL_SPOOL_TRANSLATION_KEY
        ):
            continue
        facts = topology.get(entry.device_id) if entry.device_id is not None else None
        printer = (
            facts.printer
            if facts is not None and facts.printer is not None and facts.printer in printers
            else _printer_of(entry.unique_id, printers)
        )
        if printer is None:
            LOGGER.warning(
                "external spool %r names none of the discovered printers (%s); it is not "
                "followed, because attributing it would be a guess about which machine it "
                "is bolted to",
                entry.unique_id,
                [serial.value for serial in printers],
            )
            continue
        holder = (facts.holder if facts is not None else None) or _holder_of(entry.unique_id)
        if holder is None:
            LOGGER.debug(
                "external spool %r says which machine but not which holder; skipped",
                entry.unique_id,
            )
            continue
        holders.setdefault(printer, {}).setdefault(holder, entry.entity_id)
    return holders


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

    **The suffix is not always the translation key**, and `_UNIQUE_ID_KEY` is where that is
    stated. `printer_name` is written `<serial>_name`, so a reader that assumed the key
    would strip `_printer_name`, match nothing, and resolve no serial from a row that
    plainly carries one — silently, and only for the machines whose other rows happened not
    to resolve one either.
    """
    suffix = f"_{_UNIQUE_ID_KEY.get(translation_key, translation_key)}"
    if not unique_id.endswith(suffix):
        LOGGER.debug("print sensor unique_id %r does not end in %r; no serial", unique_id, suffix)
        return None
    serial = unique_id[: -len(suffix)]
    if not serial.strip():
        return None
    return PrinterSerial(serial)
