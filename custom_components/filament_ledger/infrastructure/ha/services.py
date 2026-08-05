"""Service registration.

Every service maps one-to-one to a use case. No service performs two operations, and no use
case is reachable through two services. `sync_trays` is the narrow exception the constant
documents: it runs the startup reconciliation pass — `DetectSpool` once per tray — which is
one operation repeated over what the printer reports, not two operations.

`reason` is required on discard and adjust. The requirement is enforced in the domain as
well — a service schema is a user-interface convenience, not a guarantee.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from ...application.adjust_spool import (
    AdjustSpoolCommand,
    DiscardFilamentCommand,
    DiscardMode,
)
from ...application.errors import ApplicationError
from ...application.reconcile_spool import ReconcileSpoolCommand
from ...application.register_spool import RegisterSpoolCommand
from ...application.review_queue import ApproveReviewCommand, DismissReviewCommand
from ...const import (
    DOMAIN,
    SERVICE_ADJUST_SPOOL,
    SERVICE_APPROVE_REVIEW,
    SERVICE_DISCARD_FILAMENT,
    SERVICE_DISMISS_REVIEW,
    SERVICE_MOUNT_SPOOL,
    SERVICE_RECONCILE_SPOOL,
    SERVICE_REGISTER_SPOOL,
    SERVICE_SYNC_TRAYS,
    SERVICE_UNMOUNT_SPOOL,
)
from ...domain.error import DomainError, InvalidValueError
from ...domain.model.pending_review import ReviewCharge
from ...domain.value.colour import Colour
from ...domain.value.grams import Grams
from ...domain.value.identifiers import (
    MAX_AMS_SLOT,
    MIN_AMS_INDEX,
    MIN_AMS_SLOT,
    AmsIndex,
    PrinterSerial,
    ReviewId,
    SlotIndex,
    SpoolId,
    TagUid,
    TrayRef,
)
from ...domain.value.material import Material, MaterialKind
from .bambu_gateway import TRACKED_AMS
from .runtime import LedgerRuntime, runtimes

# A tray as it arrives in service data — the three parts of `TrayRef`. YAML gives integers,
# the UI's object selector gives strings; `Coerce(int)` reads both, and the range is bounded
# here as well as in the domain so a typo becomes a message rather than a stack trace.
#
# `printer` and `ams` are optional and their absence names the tray space this ledger
# follows, so an automation written against v1 keeps working unchanged **while there is one
# machine for it to mean** — naming a serial for the only printer in the house would be
# ceremony rather than precision. With several followed the absence is refused rather than
# resolved, and the automation gets a message naming the machines it could have meant
# (`LedgerRuntime.tray_printer`, docs/05 §5.4).
_TRAY = vol.Schema(
    {
        vol.Optional("printer"): vol.Any(cv.string, None),
        vol.Optional("ams"): vol.All(vol.Coerce(int), vol.Range(min=MIN_AMS_INDEX)),
        vol.Required("slot"): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_AMS_SLOT, max=MAX_AMS_SLOT)
        ),
    }
)

# One entry of a tray's attribution (docs/05 §5.4). Non-negative for the same reason
# `amounts` is: a charge of minus five grams has no physical reading.
_CHARGE = vol.Schema(
    {
        vol.Required("spool_id"): cv.string,
        vol.Required("amount_g"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)

REGISTER_SCHEMA = vol.Schema(
    {
        vol.Required("material"): vol.In([kind.value for kind in MaterialKind]),
        vol.Required("colour"): cv.string,
        vol.Required("opening_weight"): vol.Coerce(float),
        vol.Optional("core_weight"): vol.Coerce(float),
        vol.Optional("material_other"): cv.string,
        vol.Optional("vendor"): cv.string,
        vol.Optional("label"): cv.string,
        vol.Optional("tag_uid"): cv.string,
        vol.Optional("confirm_duplicate_tag", default=False): cv.boolean,
    }
)

RECONCILE_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): cv.string,
        vol.Required("measured_g"): vol.Coerce(float),
        vol.Optional("includes_core", default=True): cv.boolean,
        vol.Optional("note"): cv.string,
    }
)

DISCARD_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): cv.string,
        vol.Required("mode"): vol.In([mode.value for mode in DiscardMode]),
        vol.Required("reason"): cv.string,
        vol.Optional("amount_g"): vol.Coerce(float),
    }
)

ADJUST_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): cv.string,
        vol.Required("amount_g"): vol.Coerce(float),
        vol.Required("reason"): cv.string,
    }
)

MOUNT_SCHEMA = _TRAY.extend({vol.Required("spool_id"): cv.string})

UNMOUNT_SCHEMA = vol.Schema({vol.Required("spool_id"): cv.string})

APPROVE_REVIEW_SCHEMA = vol.Schema(
    {
        vol.Required("review_id"): cv.string,
        # Lists of per-tray entries, matching docs/05 §5.4: `amounts` overrides the frozen
        # estimates, `assign` gives one tray to one spool whole, and `charges` states the
        # split for a tray that fed from more than one. Lists rather than the maps keyed by
        # slot these used to be, because a tray takes three parts to name. A negative
        # confirmation has no physical reading, so it is refused at the schema too.
        vol.Optional("amounts"): [
            _TRAY.extend({vol.Required("amount_g"): vol.All(vol.Coerce(float), vol.Range(min=0))})
        ],
        vol.Optional("assign"): [_TRAY.extend({vol.Required("spool_id"): cv.string})],
        vol.Optional("charges"): [_TRAY.extend({vol.Required("charges"): [_CHARGE]})],
        vol.Optional("note"): cv.string,
    }
)

DISMISS_REVIEW_SCHEMA = vol.Schema(
    {
        vol.Required("review_id"): cv.string,
        vol.Optional("note"): cv.string,
    }
)

SYNC_TRAYS_SCHEMA = vol.Schema({})


def _runtime(hass: HomeAssistant) -> LedgerRuntime:
    ledgers = runtimes(hass)
    if not ledgers:
        msg = "Filament Ledger is not set up"
        raise HomeAssistantError(msg)
    return ledgers[0]


def _charges(entries: list[dict[str, Any]]) -> tuple[ReviewCharge, ...]:
    """One tray's attribution, off the service call. The schema bounded every field."""
    return tuple(
        ReviewCharge(spool_id=SpoolId(entry["spool_id"]), amount=Grams.of(entry["amount_g"]))
        for entry in entries
    )


def _tray(runtime: LedgerRuntime, data: dict[str, Any]) -> TrayRef:
    """One tray reference, off the service call. `_TRAY` states what an absent half means.

    An absent printer is answered by the runtime rather than by a bare sentinel, for the
    reason `LedgerRuntime.tray_printer` gives — which is also what keeps an automation
    written against v1 landing in the tray it has always landed in.
    """
    printer = data.get("printer")
    return TrayRef(
        printer=PrinterSerial(printer) if printer else runtime.tray_printer,
        ams=AmsIndex(int(data.get("ams", TRACKED_AMS.value))),
        slot=SlotIndex(int(data["slot"])),
    )


def _by_tray[T](
    runtime: LedgerRuntime, entries: list[dict[str, Any]], read: Callable[[dict[str, Any]], T]
) -> dict[TrayRef, T]:
    """A per-tray list as a mapping, refusing a tray named twice.

    A YAML list can carry one tray twice where the map these used to be could not, and two
    answers to one question must not be resolved by keeping the last.
    """
    mapped: dict[TrayRef, T] = {}
    for entry in entries:
        tray = _tray(runtime, entry)
        if tray in mapped:
            msg = f"{tray} is named twice in one call; state it once"
            raise InvalidValueError(msg)
        mapped[tray] = read(entry)
    return mapped


def _material(data: dict[str, Any]) -> Material:
    kind = MaterialKind(data["material"])
    if kind is MaterialKind.OTHER:
        return Material.other(data.get("material_other") or "Other")
    return Material.of(kind)


@callback
def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_REGISTER_SPOOL):
        return

    async def register_spool(call: ServiceCall) -> None:
        runtime = _runtime(hass)
        tag = call.data.get("tag_uid")
        async with _translated_errors():
            await runtime.use_cases.register_spool.execute(
                RegisterSpoolCommand(
                    material=_material(dict(call.data)),
                    colour=Colour.parse(call.data["colour"]),
                    opening_weight=Grams.of(call.data["opening_weight"]),
                    core_weight=Grams.of(
                        call.data.get("core_weight", runtime.default_core_weight_g)
                    ),
                    vendor=call.data.get("vendor"),
                    label=call.data.get("label"),
                    tag_uid=TagUid(tag) if tag else None,
                    confirm_duplicate_tag=call.data["confirm_duplicate_tag"],
                )
            )
        await runtime.async_refresh()

    async def reconcile_spool(call: ServiceCall) -> None:
        runtime = _runtime(hass)
        async with _translated_errors():
            await runtime.use_cases.reconcile_spool.execute(
                ReconcileSpoolCommand(
                    spool_id=SpoolId(call.data["spool_id"]),
                    measured=Grams.of(call.data["measured_g"]),
                    includes_core=call.data["includes_core"],
                    note=call.data.get("note"),
                )
            )
        await runtime.async_refresh()

    async def discard_filament(call: ServiceCall) -> None:
        runtime = _runtime(hass)
        amount = call.data.get("amount_g")
        async with _translated_errors():
            await runtime.use_cases.discard_filament.execute(
                DiscardFilamentCommand(
                    spool_id=SpoolId(call.data["spool_id"]),
                    mode=DiscardMode(call.data["mode"]),
                    reason=call.data["reason"],
                    amount=Grams.of(amount) if amount is not None else None,
                )
            )
        await runtime.async_refresh()

    async def adjust_spool(call: ServiceCall) -> None:
        runtime = _runtime(hass)
        async with _translated_errors():
            await runtime.use_cases.adjust_spool.execute(
                AdjustSpoolCommand(
                    spool_id=SpoolId(call.data["spool_id"]),
                    amount=Grams.of(call.data["amount_g"]),
                    reason=call.data["reason"],
                )
            )
        await runtime.async_refresh()

    async def mount_spool(call: ServiceCall) -> None:
        runtime = _runtime(hass)
        async with _translated_errors():
            await runtime.use_cases.mount_spool.execute(
                SpoolId(call.data["spool_id"]), _tray(runtime, dict(call.data))
            )
        await runtime.async_refresh()

    async def unmount_spool(call: ServiceCall) -> None:
        runtime = _runtime(hass)
        async with _translated_errors():
            await runtime.use_cases.unmount_spool.execute(SpoolId(call.data["spool_id"]))
        await runtime.async_refresh()

    async def approve_review(call: ServiceCall) -> None:
        runtime = _runtime(hass)
        amounts = call.data.get("amounts")
        assign = call.data.get("assign")
        charges = call.data.get("charges")
        async with _translated_errors():
            await runtime.use_cases.approve_review.execute(
                ApproveReviewCommand(
                    review_id=ReviewId(call.data["review_id"]),
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
                    note=call.data.get("note"),
                )
            )
        await runtime.async_refresh()

    async def dismiss_review(call: ServiceCall) -> None:
        runtime = _runtime(hass)
        async with _translated_errors():
            await runtime.use_cases.dismiss_review.execute(
                DismissReviewCommand(
                    review_id=ReviewId(call.data["review_id"]), note=call.data.get("note")
                )
            )
        await runtime.async_refresh()

    async def sync_trays(call: ServiceCall) -> None:
        """Fire-and-forget: the same reconciliation pass the panel button runs. The
        per-slot outcome is the websocket command's business — an automation calling
        this wants the ledger healed, not a report."""
        runtime = _runtime(hass)
        if runtime.sync_trays is not None:
            await runtime.sync_trays.execute()
            await runtime.async_refresh()

    for name, handler, schema in (
        (SERVICE_REGISTER_SPOOL, register_spool, REGISTER_SCHEMA),
        (SERVICE_RECONCILE_SPOOL, reconcile_spool, RECONCILE_SCHEMA),
        (SERVICE_DISCARD_FILAMENT, discard_filament, DISCARD_SCHEMA),
        (SERVICE_ADJUST_SPOOL, adjust_spool, ADJUST_SCHEMA),
        (SERVICE_MOUNT_SPOOL, mount_spool, MOUNT_SCHEMA),
        (SERVICE_UNMOUNT_SPOOL, unmount_spool, UNMOUNT_SCHEMA),
        (SERVICE_APPROVE_REVIEW, approve_review, APPROVE_REVIEW_SCHEMA),
        (SERVICE_DISMISS_REVIEW, dismiss_review, DISMISS_REVIEW_SCHEMA),
        (SERVICE_SYNC_TRAYS, sync_trays, SYNC_TRAYS_SCHEMA),
    ):
        hass.services.async_register(DOMAIN, name, handler, schema=schema)


class _translated_errors:  # noqa: N801 — a context manager used as a statement reads better
    """Surface a refused rule as a Home Assistant error the user can read.

    A domain error is not a crash; it is the system declining to record something it cannot
    justify. It deserves a message, not a stack trace.
    """

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object
    ) -> bool:
        if exc is not None and isinstance(exc, DomainError | ApplicationError):
            raise HomeAssistantError(str(exc)) from exc
        return False
