"""Service registration.

Every service maps one-to-one to a use case. No service performs two operations, and no use
case is reachable through two services.

`reason` is required on discard and adjust. The requirement is enforced in the domain as
well — a service schema is a user-interface convenience, not a guarantee.
"""

from __future__ import annotations

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
from ...const import (
    DOMAIN,
    SERVICE_ADJUST_SPOOL,
    SERVICE_DISCARD_FILAMENT,
    SERVICE_MOUNT_SPOOL,
    SERVICE_RECONCILE_SPOOL,
    SERVICE_REGISTER_SPOOL,
    SERVICE_UNMOUNT_SPOOL,
)
from ...domain.error import DomainError
from ...domain.value.colour import Colour
from ...domain.value.grams import Grams
from ...domain.value.identifiers import (
    MAX_AMS_SLOT,
    MIN_AMS_SLOT,
    SlotIndex,
    SpoolId,
    TagUid,
)
from ...domain.value.material import Material, MaterialKind
from .runtime import LedgerRuntime, runtimes

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

MOUNT_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): cv.string,
        vol.Required("slot"): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_AMS_SLOT, max=MAX_AMS_SLOT)
        ),
    }
)

UNMOUNT_SCHEMA = vol.Schema({vol.Required("spool_id"): cv.string})


def _runtime(hass: HomeAssistant) -> LedgerRuntime:
    ledgers = runtimes(hass)
    if not ledgers:
        msg = "Filament Ledger is not set up"
        raise HomeAssistantError(msg)
    return ledgers[0]


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
                SpoolId(call.data["spool_id"]), SlotIndex(call.data["slot"])
            )
        await runtime.async_refresh()

    async def unmount_spool(call: ServiceCall) -> None:
        runtime = _runtime(hass)
        async with _translated_errors():
            await runtime.use_cases.unmount_spool.execute(SpoolId(call.data["spool_id"]))
        await runtime.async_refresh()

    for name, handler, schema in (
        (SERVICE_REGISTER_SPOOL, register_spool, REGISTER_SCHEMA),
        (SERVICE_RECONCILE_SPOOL, reconcile_spool, RECONCILE_SCHEMA),
        (SERVICE_DISCARD_FILAMENT, discard_filament, DISCARD_SCHEMA),
        (SERVICE_ADJUST_SPOOL, adjust_spool, ADJUST_SCHEMA),
        (SERVICE_MOUNT_SPOOL, mount_spool, MOUNT_SCHEMA),
        (SERVICE_UNMOUNT_SPOOL, unmount_spool, UNMOUNT_SCHEMA),
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
