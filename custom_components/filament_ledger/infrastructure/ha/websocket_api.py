"""The panel's only channel to the backend.

Read models are served here rather than assembled from entity attributes, because entity
state is a presentation surface, not a query API.

**There is no command that sets a balance.** Changing a balance requires a movement, and
that is the whole design — an API that could set one would make the ledger decorative.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.core import HomeAssistant, callback

from ...application.adjust_spool import (
    AdjustSpoolCommand,
    DiscardFilamentCommand,
    DiscardMode,
)
from ...application.errors import ApplicationError
from ...application.reconcile_spool import ReconcileSpoolCommand
from ...application.register_spool import RegisterSpoolCommand
from ...const import DOMAIN
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
from .serialisers import spool_detail, spool_summary, whole_grams


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
    ):
        websocket_api.async_register_command(hass, handler)


def _runtime(hass: HomeAssistant) -> LedgerRuntime:
    ledgers = runtimes(hass)
    if not ledgers:
        msg = "Filament Ledger is not set up"
        raise ApplicationError(msg)
    return ledgers[0]


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
        # Bounded here as well as in the domain. `SlotIndex` raises a plain `ValueError`,
        # which `guarded` deliberately does not catch — an unvalidated adapter would turn a
        # typo into a stack trace instead of a message.
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
    await runtime.use_cases.mount_spool.execute(SpoolId(msg["spool_id"]), SlotIndex(msg["slot"]))
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


@callback
def async_unregister_commands(hass: HomeAssistant) -> None:
    """Websocket commands are process-global; nothing to unwind per entry."""
