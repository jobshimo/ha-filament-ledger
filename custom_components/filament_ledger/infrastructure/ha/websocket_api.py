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
from ...application.move_spool import UNSET, TagEdit
from ...application.reassign_movement import ReassignMovementCommand
from ...application.reconcile_spool import ReconcileSpoolCommand
from ...application.register_spool import RegisterSpoolCommand
from ...application.review_queue import ApproveReviewCommand, DismissReviewCommand
from ...application.void_movement import VoidMovementCommand
from ...const import DOMAIN
from ...domain.error import DomainError
from ...domain.value.colour import Colour
from ...domain.value.grams import Grams
from ...domain.value.identifiers import (
    MAX_AMS_SLOT,
    MIN_AMS_SLOT,
    MovementId,
    ReviewId,
    SlotIndex,
    SpoolId,
    TagSource,
    TagUid,
)
from ...domain.value.material import Material, MaterialKind
from .runtime import LedgerRuntime, runtimes
from .serialisers import (
    movement_line,
    pending_review,
    spool_detail,
    spool_summary,
    trash_result,
    tray_sync_result,
    whole_grams,
)
from .tray_sync import TraySyncResult

# A slot key as it crosses the wire. JSON object keys are strings, so `Coerce(int)` is
# what reads `"2"` — and the range is bounded here as well as in the domain, for the same
# reason the mount command bounds it: `SlotIndex` raises on garbage, and an unvalidated
# adapter would turn a typo into a stack trace instead of a message.
_SLOT_KEY = vol.All(vol.Coerce(int), vol.Range(min=MIN_AMS_SLOT, max=MAX_AMS_SLOT))


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
    ):
        websocket_api.async_register_command(hass, handler)


def _runtime(hass: HomeAssistant) -> LedgerRuntime:
    ledgers = runtimes(hass)
    if not ledgers:
        msg = "Filament Ledger is not set up"
        raise ApplicationError(msg)
    return ledgers[0]


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
        # Both maps are keyed by slot index, matching docs/02 §2.3: `amounts` overrides
        # the frozen estimates, `assign` resolves slots the review froze without a spool.
        # Amounts are bounded non-negative here as well as in the domain — a negative
        # confirmation has no physical reading.
        vol.Optional("amounts"): {_SLOT_KEY: vol.All(vol.Coerce(float), vol.Range(min=0))},
        vol.Optional("assign"): {_SLOT_KEY: str},
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
    await runtime.use_cases.approve_review.execute(
        ApproveReviewCommand(
            review_id=ReviewId(msg["review_id"]),
            amounts=(
                {SlotIndex(slot): Grams.of(value) for slot, value in amounts.items()}
                if amounts is not None
                else None
            ),
            assignments=(
                {SlotIndex(slot): SpoolId(spool_id) for slot, spool_id in assign.items()}
                if assign is not None
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
    }
)
@websocket_api.async_response
@guarded
async def handle_movements(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """The global history, newest first — UC-12 across every spool (docs/05 §5.6)."""
    runtime = _runtime(hass)
    lines = await runtime.use_cases.queries.movement_history(limit=int(msg.get("limit", 100)))
    connection.send_result(msg["id"], [movement_line(line) for line in lines])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/movements/reassign",
        vol.Required("movement_id"): str,
        vol.Required("to_spool_id"): str,
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
    moved = await runtime.use_cases.reassign_movement.execute(
        ReassignMovementCommand(
            movement_id=MovementId(msg["movement_id"]),
            to_spool_id=SpoolId(msg["to_spool_id"]),
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


@callback
def async_unregister_commands(hass: HomeAssistant) -> None:
    """Websocket commands are process-global; nothing to unwind per entry."""
