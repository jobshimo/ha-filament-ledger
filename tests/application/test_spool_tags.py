"""Tag provenance, end to end on real SQLite (docs/14 §14.2).

The owner's rule is one sentence — *a tag the printer attached is the printer's statement,
a tag I typed is mine to change* — and it only holds if the provenance survives the round
trip through the database. These tests drive `EditSpoolDetails` over the wired ledger, so
every assertion is about the column as well as about the entity.
"""

from __future__ import annotations

import pytest

from custom_components.filament_ledger.application.errors import SpoolNotFoundError
from custom_components.filament_ledger.application.move_spool import UNSET
from custom_components.filament_ledger.application.register_spool import RegisterSpoolCommand
from custom_components.filament_ledger.domain.error import (
    DuplicateTagNotConfirmedError,
    TagNotEditableError,
)
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    SpoolId,
    TagSource,
    TagUid,
)
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind

from .conftest import Ledger

TAG = TagUid("A1B2C3D4")
OTHER_TAG = TagUid("BEEF0001")


async def a_spool(
    ledger: Ledger,
    *,
    tag_uid: TagUid | None = None,
    tag_source: TagSource = TagSource.MANUAL,
    label: str | None = None,
) -> SpoolId:
    return await ledger.use_cases.register_spool.execute(
        RegisterSpoolCommand(
            material=Material.of(MaterialKind.PLA),
            colour=Colour.parse("000000"),
            opening_weight=Grams.of(1000),
            core_weight=Grams.of(250),
            label=label,
            tag_uid=tag_uid,
            tag_source=tag_source,
        )
    )


async def stored(ledger: Ledger, spool_id: SpoolId) -> tuple[str | None, str | None]:
    """The two columns, read straight out of SQLite — the entity is not the witness here."""
    row = await ledger.database.fetch_one(
        "SELECT tag_uid, tag_source FROM spool WHERE id = ?", (spool_id,)
    )
    assert row is not None
    return row["tag_uid"], row["tag_source"]


class TestProvenanceIsPersisted:
    async def test_a_tag_typed_at_registration_is_manual(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger, tag_uid=TAG)
        assert await stored(ledger, spool_id) == ("A1B2C3D4", "MANUAL")

    async def test_a_tag_read_off_a_tray_is_detected(self, ledger: Ledger) -> None:
        """Criterion 9: the register-from-sync path forwards a serial the printer read, so
        it records DETECTED — and the edit dialog then refuses to let it drift."""
        spool_id = await a_spool(ledger, tag_uid=TAG, tag_source=TagSource.DETECTED)
        assert await stored(ledger, spool_id) == ("A1B2C3D4", "DETECTED")

    async def test_an_untagged_spool_stores_neither(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        assert await stored(ledger, spool_id) == (None, None)


class TestTheTagMatrix:
    """Set / change / clear / refuse-DETECTED / duplicate-confirm, over the use case."""

    async def test_a_tag_can_be_attached_to_an_untagged_spool(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)

        await ledger.use_cases.edit_spool_details.execute(spool_id, tag=TAG)

        assert await stored(ledger, spool_id) == ("A1B2C3D4", "MANUAL")

    async def test_a_manual_tag_can_be_changed(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger, tag_uid=TAG)

        await ledger.use_cases.edit_spool_details.execute(spool_id, tag=OTHER_TAG)

        assert await stored(ledger, spool_id) == ("BEEF0001", "MANUAL")

    async def test_a_manual_tag_can_be_cleared_and_takes_its_provenance_with_it(
        self, ledger: Ledger
    ) -> None:
        """Criterion 6: a cleared tag stores `tag_uid NULL, tag_source NULL`. The pair goes
        together — a provenance describing no tag describes nothing."""
        spool_id = await a_spool(ledger, tag_uid=TAG)

        await ledger.use_cases.edit_spool_details.execute(spool_id, tag=None)

        assert await stored(ledger, spool_id) == (None, None)

    async def test_an_omitted_tag_is_left_exactly_as_it_was(self, ledger: Ledger) -> None:
        """The third state, and the reason it exists: `None` already means *clear*."""
        spool_id = await a_spool(ledger, tag_uid=TAG)

        await ledger.use_cases.edit_spool_details.execute(spool_id, label="renamed")
        assert await stored(ledger, spool_id) == ("A1B2C3D4", "MANUAL")

        await ledger.use_cases.edit_spool_details.execute(spool_id, tag=UNSET)
        assert await stored(ledger, spool_id) == ("A1B2C3D4", "MANUAL")

    async def test_a_detected_tag_refuses_to_be_changed_or_cleared(self, ledger: Ledger) -> None:
        """Criterion 5, at the layer the panel cannot route around: the dialog never offers
        the input, and the command refuses it anyway."""
        spool_id = await a_spool(ledger, tag_uid=TAG, tag_source=TagSource.DETECTED)

        with pytest.raises(TagNotEditableError):
            await ledger.use_cases.edit_spool_details.execute(spool_id, tag=OTHER_TAG)
        with pytest.raises(TagNotEditableError):
            await ledger.use_cases.edit_spool_details.execute(spool_id, tag=None)

        assert await stored(ledger, spool_id) == ("A1B2C3D4", "DETECTED")

    async def test_a_detected_spool_can_still_have_its_metadata_edited(
        self, ledger: Ledger
    ) -> None:
        """The tag is frozen; the label a human typed is not."""
        spool_id = await a_spool(ledger, tag_uid=TAG, tag_source=TagSource.DETECTED)

        await ledger.use_cases.edit_spool_details.execute(spool_id, label="Shelf B")

        detail = await ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.spool.label == "Shelf B"
        assert detail.summary.spool.tag_source is TagSource.DETECTED

    async def test_a_colliding_tag_needs_confirmation_and_then_lands(self, ledger: Ledger) -> None:
        """Criterion 7 — UC-01's rule, for UC-01's reason: a Bambu tag identifies a batch,
        so duplicates are legal, but they are deliberate or they are a bug."""
        await a_spool(ledger, tag_uid=TAG, label="the first one")
        spool_id = await a_spool(ledger, label="the second one")

        with pytest.raises(DuplicateTagNotConfirmedError, match="the first one"):
            await ledger.use_cases.edit_spool_details.execute(spool_id, tag=TAG)
        assert await stored(ledger, spool_id) == (None, None)

        await ledger.use_cases.edit_spool_details.execute(
            spool_id, tag=TAG, confirm_duplicate_tag=True
        )
        assert await stored(ledger, spool_id) == ("A1B2C3D4", "MANUAL")

    async def test_a_spool_is_not_its_own_duplicate(self, ledger: Ledger) -> None:
        """Re-saving the tag a spool already carries is not a collision with anything."""
        spool_id = await a_spool(ledger, tag_uid=TAG)

        await ledger.use_cases.edit_spool_details.execute(spool_id, tag=TAG)

        assert await stored(ledger, spool_id) == ("A1B2C3D4", "MANUAL")

    async def test_editing_a_spool_that_is_not_there(self, ledger: Ledger) -> None:
        with pytest.raises(SpoolNotFoundError):
            await ledger.use_cases.edit_spool_details.execute(SpoolId("ghost"), tag=TAG)


class TestEditingNeverTouchesTheLedger:
    async def test_metadata_and_tag_edits_write_no_movement(self, ledger: Ledger) -> None:
        """Criterion 1's second half, and criterion 4's whole point: this command has no
        way to change a balance, so the balance and the history are identical after it."""
        spool_id = await a_spool(ledger, tag_uid=TAG)
        before = await ledger.use_cases.queries.detail(spool_id)

        await ledger.use_cases.edit_spool_details.execute(
            spool_id,
            label="Rebadged",
            vendor="Polymaker",
            colour=Colour.parse("FF0000"),
            material=Material.of(MaterialKind.PETG),
            core_weight=Grams.of(180),
            tag=OTHER_TAG,
        )
        after = await ledger.use_cases.queries.detail(spool_id)

        assert after.summary.balance == before.summary.balance
        assert [line.movement.id for line in after.lines] == [
            line.movement.id for line in before.lines
        ]
        assert after.summary.spool.label == "Rebadged"
        assert after.summary.spool.vendor == "Polymaker"
        assert after.summary.spool.colour == Colour.parse("FF0000")
        assert after.summary.spool.material == Material.of(MaterialKind.PETG)
        assert after.summary.spool.core_weight == Grams.of(180)
        assert after.summary.spool.tag_uid == OTHER_TAG
