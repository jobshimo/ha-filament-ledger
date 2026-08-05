"""The panel's only channel to the backend.

Read models are served here rather than assembled from entity attributes, because entity
state is a presentation surface, not a query API.

**There is no command that sets a balance.** Changing a balance requires a movement, and
that is the whole design — an API that could set one would make the ledger decorative.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Final

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_state_change_event

from ...application.adjust_spool import (
    AdjustSpoolCommand,
    DiscardFilamentCommand,
    DiscardMode,
)
from ...application.errors import ApplicationError
from ...application.move_spool import UNSET, TagEdit
from ...application.query import StatisticsPeriod
from ...application.reassign_movement import ReassignMovementCommand
from ...application.reconcile_spool import ReconcileSpoolCommand
from ...application.register_spool import RegisterSpoolCommand
from ...application.review_queue import ApproveReviewCommand, DismissReviewCommand
from ...application.void_movement import VoidMovementCommand
from ...const import (
    CONF_ANOMALY_THRESHOLD,
    CONF_AUTO_MOUNT_ON_RFID,
    CONF_DEFAULT_CORE_WEIGHT,
    CONF_DEFAULT_OPENING_WEIGHT,
    DEFAULT_ANOMALY_THRESHOLD_PCT,
    DEFAULT_AUTO_MOUNT_ON_RFID,
    DEFAULT_CORE_WEIGHT_G,
    DEFAULT_OPENING_WEIGHT_G,
    DOMAIN,
)
from ...domain.error import DomainError, InvalidValueError
from ...domain.model.pending_review import ReviewCharge
from ...domain.port.repositories import MovementFilter
from ...domain.value.colour import Colour
from ...domain.value.grams import Grams
from ...domain.value.identifiers import (
    MAX_AMS_SLOT,
    MIN_AMS_INDEX,
    MIN_AMS_SLOT,
    AmsIndex,
    MovementId,
    PrinterSerial,
    ReviewId,
    SlotIndex,
    SpoolId,
    TagSource,
    TagUid,
    TrayRef,
)
from ...domain.value.material import Material, MaterialKind
from .bambu_gateway import TRACKED_AMS
from .event_bridge import LEDGER_EVENTS
from .printer_state import PrinterSnapshot
from .runtime import LedgerConfigEntry, LedgerRuntime, loaded_entries, runtimes
from .serialisers import (
    movement_line,
    pending_review,
    printer_state,
    spool_detail,
    spool_summary,
    statistics_result,
    trash_result,
    tray_sync_result,
    whole_grams,
)
from .tray_sync import TraySyncResult

#: A tray as it crosses the wire — the three parts of `TrayRef`, bounded here as well as
#: in the domain for the reason every adapter input is: the value objects raise on garbage,
#: and an unvalidated adapter turns a typo into a stack trace instead of a message.
#:
#: **`printer` and `ams` are optional, and their absence names the tray space this ledger
#: follows** — the machine it has always talked to, whose serial it may not know, on AMS 1.
#: A caller written against v1 that sends only a slot therefore still lands in the right
#: tray, which is right: the ledger still follows exactly one printer. The panel sends all
#: three, because the printer glance already told it what they are.
_TRAY = vol.Schema(
    {
        vol.Optional("printer"): vol.Any(str, None),
        vol.Optional("ams"): vol.All(vol.Coerce(int), vol.Range(min=MIN_AMS_INDEX)),
        vol.Required("slot"): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_AMS_SLOT, max=MAX_AMS_SLOT)
        ),
    }
)

#: How many colours one history filter may name. A household's palette is a few dozen, so
#: the bound costs nobody anything — and it is here because an unbounded list eventually
#: meets SQLite's own parameter limit, which arrives as an unhandled `OperationalError`
#: rather than as a sentence the panel can show.
_MAX_FILTER_COLOURS: Final = 64

#: One entry of a tray's attribution on the wire (docs/05 §5.4). Bounded non-negative here
#: as well as in the domain: a charge of minus five grams has no physical reading, and the
#: adapter is where a typo becomes a message rather than a stack trace.
_CHARGE = vol.Schema(
    {
        vol.Required("spool_id"): str,
        vol.Required("amount_g"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)


LOGGER = logging.getLogger(__name__)


def _charges(entries: list[dict[str, Any]]) -> tuple[ReviewCharge, ...]:
    """One tray's attribution, off the wire. The schema has already bounded every field."""
    return tuple(
        ReviewCharge(spool_id=SpoolId(entry["spool_id"]), amount=Grams.of(entry["amount_g"]))
        for entry in entries
    )


def _tray(runtime: LedgerRuntime, payload: dict[str, Any]) -> TrayRef:
    """One tray reference, off the wire. `_TRAY` states what an absent half means.

    An absent printer is answered by the runtime, never by a bare sentinel:
    `LedgerRuntime.tray_printer` says why that distinction decides whether a caller lands
    in the tray space the ledger actually uses or in a second one where every slot is free.
    """
    printer = payload.get("printer")
    return TrayRef(
        printer=PrinterSerial(printer) if printer else runtime.tray_printer,
        ams=AmsIndex(int(payload.get("ams", TRACKED_AMS.value))),
        slot=SlotIndex(int(payload["slot"])),
    )


def _by_tray[T](
    runtime: LedgerRuntime, entries: list[dict[str, Any]], read: Callable[[dict[str, Any]], T]
) -> dict[TrayRef, T]:
    """A wire list of per-tray entries as a mapping, refusing a tray named twice.

    These three payloads were JSON objects keyed by slot until a tray needed three parts to
    name it, and an object could not be keyed twice. A list can, and two entries for one
    tray are two answers to one question: keeping the last silently is how a user's first
    instruction disappears. Refused as an invalid value, which reaches the panel as a
    sentence rather than as a stack trace.
    """
    mapped: dict[TrayRef, T] = {}
    for entry in entries:
        tray = _tray(runtime, entry)
        if tray in mapped:
            msg = f"{tray} is named twice in one payload; state it once"
            raise InvalidValueError(msg)
        mapped[tray] = read(entry)
    return mapped


def _moment(value: object) -> datetime:
    """One end of the history's date filter, read off the wire.

    `cv.datetime` refuses anything that is not a timestamp. The offset is refused on top of
    that, because a bound without one names a wall clock and the ledger stores instants:
    guessing the host's timezone would make one saved filter mean two different windows on
    two installations restored from the same backup. There is nothing to guess — the
    browser knows its own offset, and the panel sends it.
    """
    parsed = cv.datetime(value)
    if parsed.tzinfo is None:
        msg = f"a date filter needs an offset, got {value!r}"
        raise vol.Invalid(msg)
    return parsed


def async_register_commands(hass: HomeAssistant) -> None:
    for handler in (
        handle_list,
        handle_detail,
        handle_stock,
        handle_create,
        handle_update,
        handle_reconcile,
        handle_discard,
        handle_adjust,
        handle_mount,
        handle_unmount,
        handle_reviews_list,
        handle_reviews_approve,
        handle_reviews_dismiss,
        handle_trays_sync,
        handle_movements,
        handle_movements_reassign,
        handle_movements_void,
        handle_movements_restore,
        handle_spools_delete,
        handle_spools_restore,
        handle_trash,
        handle_statistics,
        handle_printer_state,
        handle_settings_get,
        handle_settings_update,
        handle_subscribe,
    ):
        websocket_api.async_register_command(hass, handler)


def _runtime(hass: HomeAssistant) -> LedgerRuntime:
    ledgers = runtimes(hass)
    if not ledgers:
        msg = "Filament Ledger is not set up"
        raise ApplicationError(msg)
    return ledgers[0]


def _entry(hass: HomeAssistant) -> LedgerConfigEntry:
    """The config entry the settings commands read and write.

    Resolved the same way `_runtime` resolves its runtime, and refused the same way when
    nothing is set up: the panel gets a message rather than an exception.
    """
    entries = loaded_entries(hass)
    if not entries:
        msg = "Filament Ledger is not set up"
        raise ApplicationError(msg)
    return entries[0]


def _settings(entry: LedgerConfigEntry) -> dict[str, Any]:
    """The **effective** options: `entry.data` overlaid with `entry.options`.

    This is the composition root's own merge, restated (`__init__.py`). Reading only
    `options` would report the install-time answers as unset for every user who has never
    opened the options flow, which is precisely the audience the Settings tab exists for.
    """
    return {**entry.data, **entry.options}


def _tag_edit(payload: dict[str, Any]) -> TagEdit:
    """Read `spools/update`'s three-state tag field off the wire.

    Key absent means the panel said nothing about the tag — the DETECTED case renders no
    input at all, so this is also how a read-only tag stays untouched. An explicit null
    clears it. An empty string is neither, and `TagUid` refuses it as the blank it is.
    """
    if "tag_uid" not in payload:
        return UNSET
    raw = payload["tag_uid"]
    return None if raw is None else TagUid(raw)


def _movement_filter(payload: dict[str, Any]) -> MovementFilter:
    """Read the History tab's filter payload off the wire.

    Absent means unfiltered, field by field, so a message carrying none of these keys
    builds `NO_FILTERS` — the value object's own empty state — and *clear every filter*
    needs no command, no flag and no branch anywhere. A search box the user has emptied
    sends `""`, which is the same absence and is read as one.

    Everything numeric and every date was refused by the schema before this ran. A
    malformed colour is refused here, by `Colour.parse`, whose `InvalidValueError` is a
    `DomainError` and so reaches the panel through `guarded` as a message.
    """
    colours = payload.get("colours")
    minimum = payload.get("min_g")
    maximum = payload.get("max_g")
    return MovementFilter(
        since=payload.get("since"),
        until=payload.get("until"),
        colours=frozenset(Colour.parse(value) for value in colours or ()),
        min_magnitude=Grams.of(minimum) if minimum is not None else None,
        max_magnitude=Grams.of(maximum) if maximum is not None else None,
        search=(payload.get("search") or "").strip() or None,
    )


def _material(payload: dict[str, Any]) -> Material:
    kind = MaterialKind(payload["material"])
    if kind is MaterialKind.OTHER:
        return Material.other(payload.get("material_other") or "Other")
    return Material.of(kind)


def guarded(func: Any) -> Any:  # noqa: ANN401 — decorator over websocket handlers
    """Turn a domain or application error into a websocket error the panel can show.

    Anything else propagates: an unexpected exception is a bug, and swallowing it here would
    turn a bug into a silently empty panel.
    """

    async def wrapper(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
    ) -> None:
        try:
            await func(hass, connection, msg)
        except (DomainError, ApplicationError) as error:
            connection.send_error(msg["id"], type(error).__name__, str(error))

    wrapper.__name__ = func.__name__
    return wrapper


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/spools/list"})
@websocket_api.async_response
@guarded
async def handle_list(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    runtime = _runtime(hass)
    summaries = await runtime.use_cases.queries.overview()
    connection.send_result(msg["id"], [spool_summary(s) for s in summaries])


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/spools/get", vol.Required("spool_id"): str}
)
@websocket_api.async_response
@guarded
async def handle_detail(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    runtime = _runtime(hass)
    detail = await runtime.use_cases.queries.detail(SpoolId(msg["spool_id"]))
    connection.send_result(msg["id"], spool_detail(detail))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/stock"})
@websocket_api.async_response
@guarded
async def handle_stock(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    runtime = _runtime(hass)
    totals = await runtime.use_cases.queries.stock()
    connection.send_result(
        msg["id"],
        {
            "total_g": whole_grams(totals.total),
            "spool_count": totals.spool_count,
            "needs_weighing": totals.needs_weighing,
            "per_material": {
                name: whole_grams(amount) for name, amount in totals.per_material.items()
            },
            "defaults": {
                "opening_weight_g": runtime.default_opening_weight_g,
                "core_weight_g": runtime.default_core_weight_g,
            },
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/spools/create",
        # Same reason as the slot bound below: `MaterialKind` raises a plain `ValueError`
        # for an unknown name, so the adapter validates before the domain is reached.
        vol.Required("material"): vol.In([kind.value for kind in MaterialKind]),
        vol.Required("colour"): str,
        vol.Required("opening_weight_g"): vol.Coerce(float),
        vol.Optional("core_weight_g"): vol.Coerce(float),
        vol.Optional("material_other"): str,
        vol.Optional("vendor"): vol.Any(str, None),
        vol.Optional("label"): vol.Any(str, None),
        vol.Optional("tag_uid"): vol.Any(str, None),
        # Who attached the tag. The register form omits it and gets MANUAL; only the
        # register-from-sync path says DETECTED, because the serial it forwards came off
        # the tray reading rather than off the keyboard (docs/14 §14.2).
        vol.Optional("tag_source"): vol.In([source.value for source in TagSource]),
        vol.Optional("confirm_duplicate_tag"): bool,
    }
)
@websocket_api.async_response
@guarded
async def handle_create(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    runtime = _runtime(hass)
    tag = msg.get("tag_uid")
    spool_id = await runtime.use_cases.register_spool.execute(
        RegisterSpoolCommand(
            material=_material(msg),
            colour=Colour.parse(msg["colour"]),
            opening_weight=Grams.of(msg["opening_weight_g"]),
            # The configured default is applied here, in one place, above the domain — which
            # refuses to default it at all. See docs/02-domain-model.md §2.8.
            core_weight=Grams.of(msg.get("core_weight_g", runtime.default_core_weight_g)),
            vendor=msg.get("vendor") or None,
            label=msg.get("label") or None,
            tag_uid=TagUid(tag) if tag else None,
            tag_source=TagSource(msg.get("tag_source", TagSource.MANUAL)),
            confirm_duplicate_tag=bool(msg.get("confirm_duplicate_tag", False)),
        )
    )
    await runtime.async_refresh()
    connection.send_result(msg["id"], {"spool_id": spool_id})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/spools/update",
        vol.Required("spool_id"): str,
        # Metadata only — **never the balance**. The schema is the surface of
        # `EditSpoolDetails`, which replaces fields and cannot clear them: absent and
        # null both mean "leave unchanged".
        vol.Optional("label"): vol.Any(str, None),
        vol.Optional("vendor"): vol.Any(str, None),
        vol.Optional("colour"): str,
        vol.Optional("material"): vol.In([kind.value for kind in MaterialKind]),
        vol.Optional("material_other"): str,
        vol.Optional("core_weight_g"): vol.Coerce(float),
        # **The tag deviates from the rule above, deliberately.** Every other field here
        # reads null as "leave unchanged"; the tag is the only clearable one, so it needs
        # a third state and null is what clears it:
        #
        #     absent → unchanged     null → clear     "" → invalid
        #
        # Stated rather than left to be inferred: a reader who assumes uniformity writes
        # the bug this comment exists to prevent (docs/14 §14.2).
        vol.Optional("tag_uid"): vol.Any(str, None),
        # Required true when the tag being attached already belongs to another spool in
        # inventory — UC-01's rule, because a Bambu tag identifies a batch, not a unit.
        vol.Optional("confirm_duplicate_tag"): bool,
    }
)
@websocket_api.async_response
@guarded
async def handle_update(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    runtime = _runtime(hass)
    await runtime.use_cases.edit_spool_details.execute(
        SpoolId(msg["spool_id"]),
        label=msg.get("label"),
        vendor=msg.get("vendor"),
        colour=Colour.parse(msg["colour"]) if msg.get("colour") is not None else None,
        material=_material(msg) if msg.get("material") is not None else None,
        core_weight=(
            Grams.of(msg["core_weight_g"]) if msg.get("core_weight_g") is not None else None
        ),
        tag=_tag_edit(msg),
        confirm_duplicate_tag=bool(msg.get("confirm_duplicate_tag", False)),
    )
    await runtime.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/spools/reconcile",
        vol.Required("spool_id"): str,
        vol.Required("measured_g"): vol.Coerce(float),
        vol.Optional("includes_core"): bool,
        vol.Optional("note"): vol.Any(str, None),
    }
)
@websocket_api.async_response
@guarded
async def handle_reconcile(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    runtime = _runtime(hass)
    result = await runtime.use_cases.reconcile_spool.execute(
        ReconcileSpoolCommand(
            spool_id=SpoolId(msg["spool_id"]),
            measured=Grams.of(msg["measured_g"]),
            includes_core=bool(msg.get("includes_core", True)),
            note=msg.get("note") or None,
        )
    )
    await runtime.async_refresh()
    connection.send_result(
        msg["id"],
        {
            "delta_g": float(result.delta.as_decimal),
            "new_balance_g": whole_grams(result.new_balance),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/spools/discard",
        vol.Required("spool_id"): str,
        vol.Required("mode"): vol.In([m.value for m in DiscardMode]),
        vol.Required("reason"): str,
        vol.Optional("amount_g"): vol.Coerce(float),
    }
)
@websocket_api.async_response
@guarded
async def handle_discard(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    runtime = _runtime(hass)
    amount = msg.get("amount_g")
    await runtime.use_cases.discard_filament.execute(
        DiscardFilamentCommand(
            spool_id=SpoolId(msg["spool_id"]),
            mode=DiscardMode(msg["mode"]),
            reason=msg["reason"],
            amount=Grams.of(amount) if amount is not None else None,
        )
    )
    await runtime.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/spools/adjust",
        vol.Required("spool_id"): str,
        vol.Required("amount_g"): vol.Coerce(float),
        vol.Required("reason"): str,
    }
)
@websocket_api.async_response
@guarded
async def handle_adjust(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    runtime = _runtime(hass)
    await runtime.use_cases.adjust_spool.execute(
        AdjustSpoolCommand(
            spool_id=SpoolId(msg["spool_id"]),
            amount=Grams.of(msg["amount_g"]),
            reason=msg["reason"],
        )
    )
    await runtime.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/spools/mount",
        vol.Required("spool_id"): str,
        # The tray, in the three parts `_TRAY` documents — and on the same terms: naming
        # only a slot still means the tray space this ledger follows.
        vol.Optional("printer"): vol.Any(str, None),
        vol.Optional("ams"): vol.All(vol.Coerce(int), vol.Range(min=MIN_AMS_INDEX)),
        vol.Required("slot"): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_AMS_SLOT, max=MAX_AMS_SLOT)
        ),
    }
)
@websocket_api.async_response
@guarded
async def handle_mount(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    runtime = _runtime(hass)
    await runtime.use_cases.mount_spool.execute(SpoolId(msg["spool_id"]), _tray(runtime, msg))
    await runtime.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/spools/unmount", vol.Required("spool_id"): str}
)
@websocket_api.async_response
@guarded
async def handle_unmount(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    runtime = _runtime(hass)
    await runtime.use_cases.unmount_spool.execute(SpoolId(msg["spool_id"]))
    await runtime.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/reviews/list"})
@websocket_api.async_response
@guarded
async def handle_reviews_list(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """The open queue, oldest first — decisions in the order the doubts arose."""
    runtime = _runtime(hass)
    details = await runtime.use_cases.queries.pending_reviews()
    connection.send_result(msg["id"], [pending_review(detail) for detail in details])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/reviews/approve",
        vol.Required("review_id"): str,
        # All three are **lists of per-tray entries**, matching docs/02 §2.3: `amounts`
        # overrides the frozen estimates, `assign` gives one tray to one spool whole, and
        # `charges` states the split for a tray that fed from more than one. Lists rather
        # than the objects keyed by slot these used to be, because a tray takes three
        # parts to name and a JSON key holds one. Amounts are bounded non-negative here as
        # well as in the domain — a negative confirmation has no physical reading.
        vol.Optional("amounts"): [
            _TRAY.extend({vol.Required("amount_g"): vol.All(vol.Coerce(float), vol.Range(min=0))})
        ],
        vol.Optional("assign"): [_TRAY.extend({vol.Required("spool_id"): str})],
        vol.Optional("charges"): [_TRAY.extend({vol.Required("charges"): [_CHARGE]})],
        vol.Optional("note"): vol.Any(str, None),
    }
)
@websocket_api.async_response
@guarded
async def handle_reviews_approve(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    runtime = _runtime(hass)
    amounts = msg.get("amounts")
    assign = msg.get("assign")
    charges = msg.get("charges")
    await runtime.use_cases.approve_review.execute(
        ApproveReviewCommand(
            review_id=ReviewId(msg["review_id"]),
            amounts=(
                _by_tray(runtime, amounts, lambda entry: Grams.of(entry["amount_g"]))
                if amounts is not None
                else None
            ),
            assignments=(
                _by_tray(runtime, assign, lambda entry: SpoolId(entry["spool_id"]))
                if assign is not None
                else None
            ),
            charges=(
                _by_tray(runtime, charges, lambda entry: _charges(entry["charges"]))
                if charges is not None
                else None
            ),
            note=msg.get("note") or None,
        )
    )
    await runtime.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/reviews/dismiss",
        vol.Required("review_id"): str,
        vol.Optional("note"): vol.Any(str, None),
    }
)
@websocket_api.async_response
@guarded
async def handle_reviews_dismiss(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    runtime = _runtime(hass)
    await runtime.use_cases.dismiss_review.execute(
        DismissReviewCommand(review_id=ReviewId(msg["review_id"]), note=msg.get("note") or None)
    )
    await runtime.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/trays/sync"})
@websocket_api.async_response
@guarded
async def handle_trays_sync(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """The startup reconciliation pass, on demand, with the per-slot outcome reported.

    A runtime without a wired pass — or a dormant gateway underneath one — answers with
    the honest `dormant` flag instead of four invented empty slots: absence of a printer
    is not an absence of spools, and the panel says so instead of spinning.
    """
    runtime = _runtime(hass)
    if runtime.sync_trays is None:
        connection.send_result(msg["id"], tray_sync_result(TraySyncResult(dormant=True, slots=[])))
        return
    result = await runtime.sync_trays.execute()
    # The pass can mount and unmount spools, which makes it a mutation path like every
    # other command here — the entities hear about it without waiting for the next poll.
    await runtime.async_refresh()
    connection.send_result(msg["id"], tray_sync_result(result))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/movements",
        # Bounded like every adapter input: a limit of zero would render an empty history
        # over a full ledger, and an unbounded one invites the panel to ask for everything.
        vol.Optional("limit"): vol.All(vol.Coerce(int), vol.Range(min=1, max=500)),
        # The filters, each optional and each independent of the others (docs/06 §6.6). An
        # absent key is that filter cleared, so a message carrying none of them is the
        # whole history — which is what makes *clear every filter* an empty payload rather
        # than a command of its own.
        vol.Optional("since"): _moment,
        vol.Optional("until"): _moment,
        vol.Optional("colours"): vol.All([str], vol.Length(max=_MAX_FILTER_COLOURS)),
        # Magnitudes, therefore non-negative: the question is how many grams moved, never
        # which way they went. Bounded here rather than left to `Grams.of`, which would
        # take −50 without complaint and then match nothing for a reason nobody could see.
        vol.Optional("min_g"): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional("max_g"): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional("search"): vol.Any(str, None),
    }
)
@websocket_api.async_response
@guarded
async def handle_movements(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """The global history, newest first — UC-12 across every spool (docs/05 §5.6).

    **The filters are applied server-side**, in SQL, for the reason `handle_statistics`
    applies its period there and then some: a ledger grows without bound, so shipping it
    whole for the panel to sieve would put a query in the one layer this project cannot
    test *and* grow the payload for ever. Nothing is written, so `async_refresh` is
    deliberately not called: narrowing a view changes nothing for the entities to hear.
    """
    runtime = _runtime(hass)
    lines = await runtime.use_cases.queries.movement_history(
        limit=int(msg.get("limit", 100)), criteria=_movement_filter(msg)
    )
    connection.send_result(msg["id"], [movement_line(line) for line in lines])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/movements/reassign",
        vol.Required("movement_id"): str,
        vol.Required("to_spool_id"): str,
        # Absent means the whole charge, which is what a reassignment has always moved.
        # A magnitude of nothing is refused at the schema as well as in the use case: the
        # pair it would write cancels out and explains nothing (docs/14 §14.3). The upper
        # bound is the charge's own size, which only the use case can see.
        vol.Optional("amount_g"): vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False)),
        # Optional, unlike UC-10's mandatory reason, and the difference is principled: a
        # reassignment explains itself structurally — the link names the entry it corrects
        # and the pair names both spools (docs/14 §14.3).
        vol.Optional("note"): vol.Any(str, None),
    }
)
@websocket_api.async_response
@guarded
async def handle_movements_reassign(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Move a charge to the spool that actually fed the print.

    No HA service mirrors this one. The correction surface is anchored in History rows
    that the service grammar cannot reference usably, and the curated service list grows
    only when an automation story exists (docs/14 §14.3).
    """
    runtime = _runtime(hass)
    amount = msg.get("amount_g")
    moved = await runtime.use_cases.reassign_movement.execute(
        ReassignMovementCommand(
            movement_id=MovementId(msg["movement_id"]),
            to_spool_id=SpoolId(msg["to_spool_id"]),
            amount=Grams.of(amount) if amount is not None else None,
            note=msg.get("note") or None,
        )
    )
    await runtime.async_refresh()
    connection.send_result(msg["id"], {"ok": True, "moved_g": float(moved.as_decimal)})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/movements/void",
        vol.Required("movement_id"): str,
        vol.Optional("reason"): vol.Any(str, None),
        # **Must be explicitly true** for the no-return branch. The server refuses a
        # restitution void on a retired spool rather than silently downgrading it: a
        # silent downgrade is a gram count that changed meaning without the user
        # noticing (docs/14 §14.4).
        vol.Optional("without_restitution"): bool,
    }
)
@websocket_api.async_response
@guarded
async def handle_movements_void(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """The X on a history row: the entry leaves the default views, the grams come back.

    `returned_g` is **null** for a without-restitution void — nothing came back, and a
    zero would say something different and false.
    """
    runtime = _runtime(hass)
    returned = await runtime.use_cases.void_movement.execute(
        VoidMovementCommand(
            movement_id=MovementId(msg["movement_id"]),
            reason=msg.get("reason") or None,
            without_restitution=bool(msg.get("without_restitution", False)),
        )
    )
    await runtime.async_refresh()
    connection.send_result(
        msg["id"],
        {
            "ok": True,
            "returned_g": float(returned.as_decimal) if returned is not None else None,
        },
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/movements/restore", vol.Required("movement_id"): str}
)
@websocket_api.async_response
@guarded
async def handle_movements_restore(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """The symmetric question, answered: deduct those grams from the spool again."""
    runtime = _runtime(hass)
    deducted = await runtime.use_cases.restore_movement.execute(MovementId(msg["movement_id"]))
    await runtime.async_refresh()
    connection.send_result(msg["id"], {"ok": True, "deducted_g": float(deducted.as_decimal)})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/spools/delete", vol.Required("spool_id"): str}
)
@websocket_api.async_response
@guarded
async def handle_spools_delete(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """*Registered by mistake* — the second answer of the intent modal (docs/14 §14.4.3).

    The first answer, *thrown away*, calls the existing `spools/discard`. No command here
    duplicates it: a discard is a real-world event that counts as waste, and giving it a
    second entry point would eventually give it a second meaning.
    """
    runtime = _runtime(hass)
    await runtime.use_cases.delete_spool.execute(SpoolId(msg["spool_id"]))
    await runtime.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/spools/restore", vol.Required("spool_id"): str}
)
@websocket_api.async_response
@guarded
async def handle_spools_restore(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Back to inventory, in storage, with its history — visibility was derived all along."""
    runtime = _runtime(hass)
    await runtime.use_cases.restore_spool.execute(SpoolId(msg["spool_id"]))
    await runtime.async_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/trash"})
@websocket_api.async_response
@guarded
async def handle_trash(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Deleted spools and open void chapters — a view over facts, not a holding pen."""
    runtime = _runtime(hass)
    connection.send_result(msg["id"], trash_result(await runtime.use_cases.queries.trash()))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/statistics",
        # Bounded to the three the read model defines, like every adapter input: an
        # unknown period would reach `StatisticsPeriod` as a plain `ValueError`, which
        # `guarded` deliberately does not catch, and a typo must be a message rather than
        # a stack trace. Optional, and absent means the default the tab opens on.
        vol.Optional("period"): vol.In([period.value for period in StatisticsPeriod]),
    }
)
@websocket_api.async_response
@guarded
async def handle_statistics(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """One period's figures, computed in the application layer (docs/15 §15.6).

    **The period is applied server-side.** Sending the whole ledger for the panel to
    filter would put the visibility law of docs/14 §14.4.5 into panel JavaScript, which is
    the one layer this project cannot test — and would grow the payload with the ledger.

    Nothing is written, so `async_refresh` is deliberately not called: there is nothing for
    the entities to hear about a page being looked at.
    """
    runtime = _runtime(hass)
    period = StatisticsPeriod(msg.get("period", StatisticsPeriod.LAST_30_DAYS))
    connection.send_result(
        msg["id"], statistics_result(await runtime.use_cases.queries.statistics(period))
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/printer/state"})
@websocket_api.async_response
@guarded
async def handle_printer_state(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """One glance at the printer, read-only (docs/14 §14.5).

    **Nothing is written.** The trays are computed with the very reads the sync pass
    performs, minus `DetectSpool` — a tab that mutated the ledger by being looked at would
    violate the reader's reasonable model of "just looking", so `async_refresh` is
    deliberately *not* called here either: there is nothing for the entities to hear.

    A runtime without a wired printer — or a gateway that discovered nothing underneath
    one — answers `{"dormant": true}` and the tab renders the teaching empty state, in the
    voice the sync strip already uses: no spinner, no four invented trays.
    """
    runtime = _runtime(hass)
    if runtime.printer is None:
        connection.send_result(msg["id"], printer_state(PrinterSnapshot(dormant=True)))
        return
    connection.send_result(msg["id"], printer_state(await runtime.printer.execute()))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/settings/get"})
@websocket_api.async_response
@guarded
async def handle_settings_get(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """The four options, as they currently take effect (docs/14 §14.6.4).

    Readable by anyone: a hidden tab invites "it's broken", while a labelled read-only one
    teaches the model. Only the *write* below is an administrative act.
    """
    settings = _settings(_entry(hass))
    connection.send_result(
        msg["id"],
        {
            CONF_DEFAULT_OPENING_WEIGHT: int(
                settings.get(CONF_DEFAULT_OPENING_WEIGHT, DEFAULT_OPENING_WEIGHT_G)
            ),
            CONF_DEFAULT_CORE_WEIGHT: int(
                settings.get(CONF_DEFAULT_CORE_WEIGHT, DEFAULT_CORE_WEIGHT_G)
            ),
            CONF_ANOMALY_THRESHOLD: int(
                settings.get(CONF_ANOMALY_THRESHOLD, DEFAULT_ANOMALY_THRESHOLD_PCT)
            ),
            CONF_AUTO_MOUNT_ON_RFID: bool(
                settings.get(CONF_AUTO_MOUNT_ON_RFID, DEFAULT_AUTO_MOUNT_ON_RFID)
            ),
        },
    )


# Outermost, which is the canonical Home Assistant stacking (`config/entity_registry`):
# `websocket_command` only tags the function it wraps, and `require_admin` carries the tag
# forward through `functools.wraps` while running its check *before* the handler is ever
# scheduled. Stacked the other way round the gate would still hold, but the registered
# command would be the ungated function — a distinction nobody should have to re-derive.
@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/settings/update",
        # The bounds are the config flow's, restated — for the same reason every adapter
        # validates: a typo must be a message, not a stack trace. Every field is optional
        # because the tab may send any subset; the merge below is what keeps the ones it
        # did not send.
        vol.Optional(CONF_DEFAULT_OPENING_WEIGHT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10000)
        ),
        vol.Optional(CONF_DEFAULT_CORE_WEIGHT): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=2000)
        ),
        vol.Optional(CONF_ANOMALY_THRESHOLD): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
        vol.Optional(CONF_AUTO_MOUNT_ON_RFID): bool,
    }
)
@websocket_api.async_response
@guarded
async def handle_settings_update(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Change how every user's ledger behaves — which *is* an administrative act.

    `require_admin` is the considered inverse of the panel's own `require_admin=False`
    (`panel.py`): weighing a spool is not administrative, and changing the anomaly
    threshold for the whole household is.

    The write goes through `async_update_entry`, which fires the registered update
    listener and **reloads the entry** — the existing, only mechanism by which an option
    change takes effect (`DetectSpool` holds `auto_mount` as a plain value on precisely
    this promise). The tab says so before saving.

    The subset is merged over the *effective* settings, not over `options` alone: writing
    only what the tab sent would silently revert every option the user has already changed
    but did not touch this time.
    """
    entry = _entry(hass)
    changes = {key: value for key, value in msg.items() if key not in ("id", "type")}
    hass.config_entries.async_update_entry(entry, options={**_settings(entry), **changes})
    connection.send_result(msg["id"], {"ok": True})


@callback
def async_unregister_commands(hass: HomeAssistant) -> None:
    """Websocket commands are process-global; nothing to unwind per entry."""


# -- the live subscription ---------------------------------------------------------------

#: How long a burst is allowed to coalesce before the panel is told about it.
#:
#: One print finishing raises a movement, possibly a depletion and possibly a confidence
#: change, and a tray reconciliation can touch four entities at once. Each would otherwise
#: be a full read model computed and pushed. Short enough that nobody perceives it as lag.
_PUSH_COOLDOWN_S: Final = 0.3


async def _ledger_payload(hass: HomeAssistant) -> dict[str, Any]:
    """Everything the panel's ledger views read, in one pass.

    The same five reads the panel used to make on every change, computed once here and
    pushed. The panel no longer asks — which is the entire point: a client that refetches
    on a signal is still a client that decides when to ask.
    """
    queries = _runtime(hass).use_cases.queries
    totals = await queries.stock()
    return {
        "kind": "ledger",
        "spools": [spool_summary(summary) for summary in await queries.overview()],
        "stock": {
            "total_g": whole_grams(totals.total),
            "spool_count": totals.spool_count,
            "needs_weighing": totals.needs_weighing,
            "per_material": {name: whole_grams(g) for name, g in totals.per_material.items()},
        },
        "reviews": [pending_review(detail) for detail in await queries.pending_reviews()],
        "movements": [movement_line(line) for line in await queries.movement_history()],
        "trash": trash_result(await queries.trash()),
    }


async def _printer_payload(hass: HomeAssistant) -> dict[str, Any]:
    runtime = _runtime(hass)
    if runtime.printer is None:
        return {"kind": "printer", "printer": printer_state(PrinterSnapshot(dormant=True))}
    return {"kind": "printer", "printer": printer_state(await runtime.printer.execute())}


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@websocket_api.async_response
@guarded
async def handle_subscribe(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """One subscription, and the backend pushes.

    **Nothing here polls, and nothing on the other end asks twice.** Two things can change
    what the panel shows, and each has a signal that already exists:

    - **The ledger**, which changes only when this integration writes to it. Its own
      `filament_ledger_*` events (`event_bridge.LEDGER_EVENTS`) are that moment, exactly.
    - **The printer**, whose figures belong to the gateway's entities. Discovery already
      resolved which ones (`BambuLabGateway.watched_entity_ids`), so the subscription
      watches those and nothing else — not every state change in the house.

    A panel comparing `hass` objects, or asking again on an interval, would be inferring
    both facts from a distance. The integration knows them; it says so.

    The current state is pushed on subscribe, so a freshly opened panel is filled by the
    same path that keeps it current, rather than by a separate set of first-load reads that
    could disagree with it.
    """
    entity_ids = sorted(_printer_entity_ids(hass))

    async def push_ledger() -> None:
        connection.send_message(websocket_api.event_message(msg["id"], await _ledger_payload(hass)))

    async def push_printer() -> None:
        connection.send_message(
            websocket_api.event_message(msg["id"], await _printer_payload(hass))
        )

    ledger = Debouncer(
        hass, LOGGER, cooldown=_PUSH_COOLDOWN_S, immediate=False, function=push_ledger
    )
    printer = Debouncer(
        hass, LOGGER, cooldown=_PUSH_COOLDOWN_S, immediate=False, function=push_printer
    )

    @callback
    def _ledger_changed(_event: Event[Any]) -> None:
        hass.async_create_task(ledger.async_call())

    @callback
    def _printer_changed(_event: Event[Any]) -> None:
        hass.async_create_task(printer.async_call())

    unsubscribes = [hass.bus.async_listen(name, _ledger_changed) for name in sorted(LEDGER_EVENTS)]
    if entity_ids:
        unsubscribes.append(async_track_state_change_event(hass, entity_ids, _printer_changed))

    @callback
    def _unsubscribe() -> None:
        for unsubscribe in unsubscribes:
            unsubscribe()

    connection.subscriptions[msg["id"]] = _unsubscribe
    connection.send_result(msg["id"])

    await push_ledger()
    await push_printer()


def _printer_entity_ids(hass: HomeAssistant) -> frozenset[str]:
    """Which entities the printer half of the subscription watches.

    Empty when no printer was discovered, which makes the subscription correctly silent
    rather than absent: the panel still receives ledger pushes, and the Printer tab still
    renders its honest dormant state.
    """
    printer = _runtime(hass).printer
    return printer.gateway.watched_entity_ids if printer is not None else frozenset()
