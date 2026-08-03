"""The correction commands over the wire — docs/14 §14.3, §14.4.

Dispatched exactly as `ActiveConnection.async_handle` dispatches them: looked up in the
table `async_register_commands` filled, validated against the schema the decorator
attached, then run against the real use cases on real SQLite. The connection is the only
fake, so every assertion about a payload here is an assertion about the actual ledger.

The suite's job is the adapter's job: schema, result shape, and error mapping. The
accounting itself is proved in `tests/application/test_corrections.py`, where it belongs.
"""

from __future__ import annotations

from typing import cast

import pytest

from custom_components.filament_ledger.infrastructure.ha.websocket_api import (
    async_register_commands,
)

from .conftest import Harness, as_hass
from .test_websocket_api import (
    ADJUST,
    DISCARD,
    GET,
    LIST,
    MOUNT,
    MOVEMENTS,
    STOCK,
    WsClient,
    a_created_spool,
)

REASSIGN = "filament_ledger/movements/reassign"
VOID = "filament_ledger/movements/void"
RESTORE_MOVEMENT = "filament_ledger/movements/restore"
DELETE_SPOOL = "filament_ledger/spools/delete"
RESTORE_SPOOL = "filament_ledger/spools/restore"
TRASH = "filament_ledger/trash"


@pytest.fixture
def ws(harness: Harness) -> WsClient:
    """A client over the harness's real ledger, with the command table filled.

    Declared here rather than imported from the neighbouring module: a pytest fixture
    imported by name is a fixture *and* a shadowed symbol, so every helper that takes a
    `ws` argument then reads as a redefinition of it.
    """
    async_register_commands(as_hass(harness.hass))
    return WsClient(hass=harness.hass)


async def a_charge(ws: WsClient, spool_id: str, grams: float = -84.1) -> str:
    """One deducting entry, and its id as the panel would read it off the History row."""
    await ws.result_dict(ADJUST, spool_id=spool_id, amount_g=grams, reason="lamp shade")
    rows = await ws.result_list(MOVEMENTS)
    return cast(str, rows[0]["movement_id"])


class TestSchemasRejectMalformedMessages:
    """A typo in the panel must become a message, not a stack trace."""

    @pytest.mark.parametrize(
        ("command", "payload"),
        [
            (REASSIGN, {"movement_id": "m"}),
            (REASSIGN, {"to_spool_id": "s"}),
            (VOID, {}),
            (VOID, {"movement_id": "m", "without_restitution": "yes"}),
            (RESTORE_MOVEMENT, {}),
            (DELETE_SPOOL, {}),
            (RESTORE_SPOOL, {}),
        ],
    )
    async def test_a_malformed_message_never_reaches_a_handler(
        self,
        ws: WsClient,
        command: str,
        payload: dict[str, object],
    ) -> None:
        with pytest.raises(Exception, match=r"required key|expected bool|extra keys"):
            ws.parse(command, **payload)

    async def test_trash_takes_no_arguments(self, ws: WsClient) -> None:
        assert await ws.result_dict(TRASH) == {"spools": [], "movements": []}


class TestReassign:
    async def test_it_moves_the_charge_and_reports_the_magnitude(
        self,
        ws: WsClient,
    ) -> None:
        wrong = await a_created_spool(ws, label="Wrongly charged")
        right = await a_created_spool(ws, label="Actually printed")
        charge = await a_charge(ws, wrong)

        assert await ws.result_dict(REASSIGN, movement_id=charge, to_spool_id=right) == {
            "ok": True,
            "moved_g": 84.1,
        }

        balances = {
            cast(str, s["id"]): cast(int, s["balance_g"]) for s in await ws.result_list(LIST)
        }
        assert balances == {wrong: 1000, right: 916}

    async def test_the_note_is_optional(self, ws: WsClient) -> None:
        wrong = await a_created_spool(ws)
        right = await a_created_spool(ws)
        charge = await a_charge(ws, wrong)

        assert await ws.result_dict(
            REASSIGN, movement_id=charge, to_spool_id=right, note="slot 2 really"
        ) == {"ok": True, "moved_g": 84.1}
        rows = await ws.result_list(MOVEMENTS)
        assert rows[0]["note"] == "Reassigned from Bambu Lab PLA · slot 2 really"

    async def test_an_increase_is_refused_by_name(self, ws: WsClient) -> None:
        """`guarded` sends the exception's class name as the error code, so the panel can
        branch on the rule rather than on prose."""
        await a_created_spool(ws)
        other = await a_created_spool(ws)
        opening = cast(str, (await ws.result_list(MOVEMENTS))[0]["movement_id"])

        code, message = await ws.error(REASSIGN, movement_id=opening, to_spool_id=other)

        assert code == "MovementNotReassignableError"
        assert "no charge here to move" in message

    async def test_an_unknown_movement_is_refused(self, ws: WsClient) -> None:
        spool_id = await a_created_spool(ws)

        code, _message = await ws.error(REASSIGN, movement_id="nobody", to_spool_id=spool_id)

        assert code == "MovementNotFoundError"

    async def test_a_deleted_target_is_refused(self, ws: WsClient) -> None:
        wrong = await a_created_spool(ws)
        right = await a_created_spool(ws)
        charge = await a_charge(ws, wrong)
        await ws.result_dict(DELETE_SPOOL, spool_id=right)

        code, _message = await ws.error(REASSIGN, movement_id=charge, to_spool_id=right)

        assert code == "SpoolDeletedError"


class TestVoidAndRestore:
    async def test_voiding_reports_what_came_back(self, ws: WsClient) -> None:
        spool_id = await a_created_spool(ws)
        charge = await a_charge(ws, spool_id)

        assert await ws.result_dict(VOID, movement_id=charge, reason="wrong spool") == {
            "ok": True,
            "returned_g": 84.1,
        }

        detail = await ws.result_dict(GET, spool_id=spool_id)
        assert detail["balance_g"] == 1000
        # The pair is gone from the default history…
        assert [row["type"] for row in await ws.result_list(MOVEMENTS)] == ["OPENING_BALANCE"]
        # …and both of its rows are still in the spool's own, both marked. "Hidden as
        # voided" covers the entry *and* its reversal (docs/14 §14.4.5): they are one
        # chapter, and the detail view shows a chapter as a chapter.
        history = cast("list[dict[str, object]]", detail["history"])
        assert [(row["type"], row["voided"]) for row in history] == [
            ("VOID_REVERSAL", True),
            ("MANUAL_ADJUSTMENT", True),
            ("OPENING_BALANCE", False),
        ]

    async def test_restoring_reports_what_went_out_again(
        self,
        ws: WsClient,
    ) -> None:
        spool_id = await a_created_spool(ws)
        charge = await a_charge(ws, spool_id)
        await ws.result_dict(VOID, movement_id=charge)

        assert await ws.result_dict(RESTORE_MOVEMENT, movement_id=charge) == {
            "ok": True,
            "deducted_g": -84.1,
        }
        assert (await ws.result_dict(GET, spool_id=spool_id))["balance_g"] == 916

    async def test_a_without_restitution_void_returns_null_not_zero(
        self,
        ws: WsClient,
    ) -> None:
        """Null and zero say different things, and only one of them is true here."""
        spool_id = await a_created_spool(ws)
        charge = await a_charge(ws, spool_id)
        await ws.result_dict(DELETE_SPOOL, spool_id=spool_id)

        assert await ws.result_dict(
            VOID,
            movement_id=charge,
            reason="this spool was never here",
            without_restitution=True,
        ) == {"ok": True, "returned_g": None}

    async def test_restitution_onto_a_retired_spool_is_refused_rather_than_downgraded(
        self,
        ws: WsClient,
    ) -> None:
        """A silent downgrade is a gram count that changed meaning without the user
        noticing, so the server refuses and the modal asks."""
        spool_id = await a_created_spool(ws)
        charge = await a_charge(ws, spool_id)
        await ws.result_dict(DELETE_SPOOL, spool_id=spool_id)

        code, message = await ws.error(VOID, movement_id=charge)

        assert code == "RestitutionUnavailableError"
        assert "restore it from the trash first" in message

    @pytest.mark.parametrize(
        ("kind", "code"),
        [
            ("OPENING_BALANCE", "MovementNotVoidableError"),
            ("VOID_REVERSAL", "MovementNotVoidableError"),
        ],
    )
    async def test_the_two_unvoidable_types_map_to_their_error(
        self,
        ws: WsClient,
        kind: str,
        code: str,
    ) -> None:
        spool_id = await a_created_spool(ws)
        charge = await a_charge(ws, spool_id)
        await ws.result_dict(VOID, movement_id=charge)
        target = next(
            cast(str, row["movement_id"])
            for row in cast(
                "list[dict[str, object]]",
                (await ws.result_dict(GET, spool_id=spool_id))["history"],
            )
            if row["type"] == kind
        )

        assert (await ws.error(VOID, movement_id=target))[0] == code

    async def test_restoring_a_terminal_void_is_refused(
        self,
        ws: WsClient,
    ) -> None:
        spool_id = await a_created_spool(ws)
        charge = await a_charge(ws, spool_id)
        await ws.result_dict(DELETE_SPOOL, spool_id=spool_id)
        await ws.result_dict(
            VOID, movement_id=charge, reason="never here", without_restitution=True
        )
        await ws.result_dict(RESTORE_SPOOL, spool_id=spool_id)

        assert (await ws.error(RESTORE_MOVEMENT, movement_id=charge))[0] == (
            "VoidNotReinstatableError"
        )


class TestDeleteAndRestoreASpool:
    async def test_deleting_frees_the_slot_and_empties_the_inventory_row(
        self,
        ws: WsClient,
    ) -> None:
        gone = await a_created_spool(ws, label="Mistake")
        replacement = await a_created_spool(ws, label="The real one")
        await ws.result_dict(MOUNT, spool_id=gone, slot=1)

        assert await ws.result_dict(DELETE_SPOOL, spool_id=gone) == {"ok": True}

        assert [s["id"] for s in await ws.result_list(LIST)] == [replacement]
        # The slot is free immediately — this mount would collide otherwise.
        assert await ws.result_dict(MOUNT, spool_id=replacement, slot=1) == {"ok": True}
        stock = await ws.result_dict(STOCK)
        assert (stock["total_g"], stock["spool_count"]) == (1000, 1)

    async def test_the_detail_of_a_deleted_spool_is_still_served(
        self,
        ws: WsClient,
    ) -> None:
        """Reachable from the Trash, and complete — the derivation surface never hides."""
        spool_id = await a_created_spool(ws, label="Mistake")
        await ws.result_dict(ADJUST, spool_id=spool_id, amount_g=-100, reason="lamp shade")
        await ws.result_dict(DELETE_SPOOL, spool_id=spool_id)

        detail = await ws.result_dict(GET, spool_id=spool_id)

        assert detail["state"] == "DELETED"
        assert detail["balance_g"] == 900
        assert len(cast("list[object]", detail["history"])) == 2

    async def test_restoring_brings_the_spool_and_its_history_back(
        self,
        ws: WsClient,
    ) -> None:
        spool_id = await a_created_spool(ws, label="Mistake")
        await ws.result_dict(DELETE_SPOOL, spool_id=spool_id)
        assert await ws.result_list(MOVEMENTS) == []

        assert await ws.result_dict(RESTORE_SPOOL, spool_id=spool_id) == {"ok": True}

        assert [s["id"] for s in await ws.result_list(LIST)] == [spool_id]
        assert [row["type"] for row in await ws.result_list(MOVEMENTS)] == ["OPENING_BALANCE"]

    async def test_restoring_a_spool_that_is_not_in_the_trash_is_refused(
        self,
        ws: WsClient,
    ) -> None:
        spool_id = await a_created_spool(ws)

        code, message = await ws.error(RESTORE_SPOOL, spool_id=spool_id)

        assert code == "InvalidValueError"
        assert "not in the trash" in message

    async def test_an_unknown_spool_is_refused(self, ws: WsClient) -> None:
        assert (await ws.error(DELETE_SPOOL, spool_id="nobody"))[0] == "SpoolNotFoundError"


class TestTheTrashPayload:
    async def test_it_carries_both_sections_in_the_documented_shape(
        self,
        ws: WsClient,
    ) -> None:
        kept = await a_created_spool(ws, label="Kept")
        gone = await a_created_spool(ws, label="Gone")
        charge = await a_charge(ws, kept)
        await ws.result_dict(DELETE_SPOOL, spool_id=gone)
        await ws.result_dict(VOID, movement_id=charge, reason="wrong spool")

        trash = await ws.result_dict(TRASH)

        (spool,) = cast("list[dict[str, object]]", trash["spools"])
        assert spool["id"] == gone
        assert spool["name"] == "Gone"
        assert spool["deleted_at"] is not None

        (entry,) = cast("list[dict[str, object]]", trash["movements"])
        assert {k: v for k, v in entry.items() if k != "voided_at"} == {
            "movement_id": charge,
            "spool_id": kept,
            "spool_name": "Kept",
            "spool_colour": "#000000",
            "spool_deleted": False,
            "spool_discarded": False,
            "type": "MANUAL_ADJUSTMENT",
            "label": "Adjustment",
            "amount_g": -84.1,
            "occurred_at": entry["occurred_at"],
            "reason": "wrong spool",
            "had_restitution": True,
            "restorable": True,
        }

    async def test_a_terminal_chapter_says_it_cannot_be_restored(
        self,
        ws: WsClient,
    ) -> None:
        """`restorable` is computed server-side: a rule that lived only in the panel would
        live in the one layer with no harness (docs/14 §14.8)."""
        spool_id = await a_created_spool(ws)
        charge = await a_charge(ws, spool_id)
        await ws.result_dict(DELETE_SPOOL, spool_id=spool_id)
        await ws.result_dict(
            VOID, movement_id=charge, reason="never here", without_restitution=True
        )

        (entry,) = cast("list[dict[str, object]]", (await ws.result_dict(TRASH))["movements"])

        assert entry["had_restitution"] is False
        assert entry["restorable"] is False
        assert entry["spool_deleted"] is True

    async def test_a_discarded_spools_chapter_is_not_restorable_either(
        self,
        ws: WsClient,
    ) -> None:
        spool_id = await a_created_spool(ws)
        charge = await a_charge(ws, spool_id)
        await ws.result_dict(VOID, movement_id=charge)
        await ws.result_dict(DISCARD, spool_id=spool_id, mode="whole_spool", reason="water damage")

        (entry,) = cast("list[dict[str, object]]", (await ws.result_dict(TRASH))["movements"])

        assert entry["had_restitution"] is True
        assert entry["restorable"] is False
        assert entry["spool_discarded"] is True


class TestTheHistoryRowCarriesItsActions:
    async def test_a_reassignment_leg_reports_its_own_direction(
        self,
        ws: WsClient,
    ) -> None:
        """`MovementType.REASSIGNMENT.direction` is `EITHER`; a *row* went one way, and it
        is the row the panel offers an action on (docs/14 §14.3)."""
        wrong = await a_created_spool(ws)
        right = await a_created_spool(ws)
        charge = await a_charge(ws, wrong)
        await ws.result_dict(REASSIGN, movement_id=charge, to_spool_id=right)

        legs = [row for row in await ws.result_list(MOVEMENTS) if row["type"] == "REASSIGNMENT"]

        assert {cast(str, row["spool_id"]): row["direction"] for row in legs} == {
            wrong: "INCREASE",
            right: "DECREASE",
        }


class TestThePanelsRefreshStillWorks:
    async def test_every_command_refreshes_the_coordinator(self, harness: Harness) -> None:
        """Each correction is a mutation path, so the entities hear about it without
        waiting for the next poll — the same contract every other command here keeps."""
        async_register_commands(as_hass(harness.hass))
        client = WsClient(hass=harness.hass)
        spool_id = await a_created_spool(client)
        charge = await a_charge(client, spool_id)
        before = harness.coordinator.refresh_count

        await client.result_dict(VOID, movement_id=charge)
        await client.result_dict(RESTORE_MOVEMENT, movement_id=charge)
        await client.result_dict(DELETE_SPOOL, spool_id=spool_id)
        await client.result_dict(RESTORE_SPOOL, spool_id=spool_id)

        assert harness.coordinator.refresh_count == before + 4
        # The read-only command is the one that must *not* refresh.
        await client.result_dict(TRASH)
        assert harness.coordinator.refresh_count == before + 4


class TestGramsCrossTheWireAsTheModalPromised:
    async def test_the_legs_carry_the_figure_the_modal_read(
        self,
        ws: WsClient,
    ) -> None:
        """docs/14 §14.3 criterion 7, from the backend's side.

        The modal prints `|amount_g|` off the row it was opened from, at one decimal. The
        legs are serialised by the same rule from the same underlying quantity, so the
        number the user was promised and the number the ledger holds cannot drift — even
        for a charge that sits exactly on a rounding boundary, which is the case that
        would expose two different roundings if there were two.
        """
        wrong = await a_created_spool(ws)
        right = await a_created_spool(ws)
        charge = await a_charge(ws, wrong, grams=-37.25)
        row = next(r for r in await ws.result_list(MOVEMENTS) if r["movement_id"] == charge)
        promised = abs(cast(float, row["amount_g"]))

        await ws.result_dict(REASSIGN, movement_id=charge, to_spool_id=right)

        legs = [r for r in await ws.result_list(MOVEMENTS) if r["type"] == "REASSIGNMENT"]
        assert sorted(cast(float, r["amount_g"]) for r in legs) == [-promised, promised]
