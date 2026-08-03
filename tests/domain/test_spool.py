"""Spool invariants and transitions."""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.filament_ledger.domain.error import (
    InvalidValueError,
    SpoolDiscardedError,
    TagNotEditableError,
)
from custom_components.filament_ledger.domain.model.spool import Spool, register
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    SlotIndex,
    TagSource,
    TagUid,
)
from custom_components.filament_ledger.domain.value.location import (
    AmsSlot,
    ExternalSpool,
    Storage,
    is_mounted,
)
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.spool_state import SpoolState

from .conftest import EPOCH, a_spool, at


class TestInvariants:
    def test_opening_weight_must_be_positive(self) -> None:
        with pytest.raises(InvalidValueError):
            a_spool(opening_weight=Grams.zero())
        with pytest.raises(InvalidValueError):
            a_spool(opening_weight=Grams.of(-1))

    def test_core_weight_may_be_zero_but_not_negative(self) -> None:
        assert a_spool(core_weight=Grams.zero()) is not None
        with pytest.raises(InvalidValueError):
            a_spool(core_weight=Grams.of(-1))

    def test_core_weight_has_no_default(self) -> None:
        """A silent zero would report every reconciliation ~250 g heavier than reality,
        forever, and the error would look like drift rather than like a bug."""
        with pytest.raises(TypeError):
            register(  # type: ignore[call-arg]
                material=Material.of(MaterialKind.PLA),
                colour=Colour.parse("000000"),
                opening_weight=Grams.of(1000),
                registered_at=EPOCH,
            )

    def test_identity_is_generated_not_the_tag(self) -> None:
        """Two spools from the same batch share a tag. Using it as identity would merge
        them and corrupt both balances."""
        tag = TagUid("A1B2C3D4")
        first = register(
            material=Material.of(MaterialKind.PLA),
            colour=Colour.parse("000000"),
            opening_weight=Grams.of(1000),
            core_weight=Grams.of(250),
            registered_at=EPOCH,
            tag_uid=tag,
        )
        second = register(
            material=Material.of(MaterialKind.PLA),
            colour=Colour.parse("000000"),
            opening_weight=Grams.of(1000),
            core_weight=Grams.of(250),
            registered_at=EPOCH,
            tag_uid=tag,
        )
        assert first.id != second.id
        assert first.tag_uid == second.tag_uid


class TestLocation:
    def test_a_new_spool_starts_in_storage(self) -> None:
        assert a_spool().location == Storage()

    def test_mounting_and_unmounting(self) -> None:
        spool = a_spool().mounted_in(SlotIndex(1))
        assert spool.location == AmsSlot(SlotIndex(1))
        assert is_mounted(spool.location)
        assert spool.unmounted().location == Storage()
        assert not is_mounted(Storage())

    def test_the_external_spool_counts_as_mounted(self) -> None:
        assert is_mounted(a_spool().mounted_externally().location)
        assert a_spool().mounted_externally().location == ExternalSpool()

    def test_moving_returns_a_new_instance(self) -> None:
        original = a_spool()
        moved = original.mounted_in(SlotIndex(2))
        assert original.location == Storage()
        assert moved is not original

    def test_slot_index_is_bounded(self) -> None:
        for valid in (1, 2, 3, 4):
            assert SlotIndex(valid).value == valid
        for invalid in (0, 5, -1):
            with pytest.raises(ValueError, match="AMS slot"):
                SlotIndex(invalid)


class TestDiscard:
    def test_discarding_is_terminal(self) -> None:
        spool = a_spool().discarded(at(days=1))
        assert spool.is_discarded
        assert spool.state(balance=Grams.of(612), movement_count=4) is SpoolState.DISCARDED

    def test_a_discarded_spool_cannot_move(self) -> None:
        spool = a_spool().discarded(at(days=1))
        with pytest.raises(SpoolDiscardedError):
            spool.mounted_in(SlotIndex(1))

    def test_a_discarded_spool_cannot_be_edited(self) -> None:
        spool = a_spool().discarded(at(days=1))
        with pytest.raises(SpoolDiscardedError):
            spool.with_details(label="renamed")

    def test_a_discarded_spool_cannot_be_discarded_again(self) -> None:
        spool = a_spool().discarded(at(days=1))
        with pytest.raises(SpoolDiscardedError):
            spool.discarded(at(days=2))

    def test_discarding_returns_the_spool_to_storage(self) -> None:
        spool = a_spool().mounted_in(SlotIndex(3)).discarded(at(days=1))
        assert spool.location == Storage()


class TestReconciliationArithmetic:
    def test_the_core_is_subtracted_from_a_gross_reading(self) -> None:
        spool = a_spool(core_weight=Grams.of(250))
        assert spool.net_from_gross(Grams.of(974)) == Grams.of(724)

    def test_a_coreless_spool_needs_no_arithmetic(self) -> None:
        spool = a_spool(core_weight=Grams.zero())
        assert spool.net_from_gross(Grams.of(724)) == Grams.of(724)


class TestPresentation:
    def test_percentage_remaining(self) -> None:
        assert a_spool().remaining_percentage(Grams.of(611.7)).rounded == 61

    def test_a_negative_balance_clamps_to_zero_percent(self) -> None:
        """Clamping is a display decision. The ledger still records -40 g and the anomaly
        detector still flags it; a progress bar simply has nowhere to draw it."""
        assert a_spool().remaining_percentage(Grams.of(-40)).rounded == 0

    def test_display_name_prefers_the_label(self) -> None:
        assert a_spool().with_details(label="Shelf B").display_name == "Shelf B"

    def test_display_name_falls_back_to_vendor_and_material(self) -> None:
        assert a_spool().display_name == "Bambu Lab PLA"


class TestMetadataEditing:
    def test_editing_details_never_touches_the_balance(self) -> None:
        """The form contains no balance field at all. That is not an omission but the
        point: changing a balance requires a movement."""
        spool = a_spool()
        edited = spool.with_details(label="new", vendor="Other")
        assert edited.opening_weight == spool.opening_weight
        assert not hasattr(edited, "balance")

    def test_unspecified_fields_are_left_alone(self) -> None:
        spool = a_spool().with_details(label="kept")
        assert spool.with_details(vendor="Elegoo").label == "kept"


def a_tagged_spool(source: TagSource, tag: str = "A1B2C3D4") -> Spool:
    return register(
        material=Material.of(MaterialKind.PLA),
        colour=Colour.parse("000000"),
        opening_weight=Grams.of(1000),
        core_weight=Grams.of(250),
        registered_at=EPOCH,
        tag_uid=TagUid(tag),
        tag_source=source,
    )


class TestTagProvenance:
    """The owner's rule, in the entity: *a tag the printer attached is the printer's
    statement; a tag I typed is mine to change* (docs/14 §14.2)."""

    def test_a_tag_and_its_provenance_are_set_together(self) -> None:
        """The pairing lives here because SQLite's ADD COLUMN cannot carry a cross-column
        CHECK — migration 0003's check covers only the value set."""
        with pytest.raises(InvalidValueError):
            replace(a_spool(), tag_uid=TagUid("A1B2C3D4"))
        with pytest.raises(InvalidValueError):
            replace(a_tagged_spool(TagSource.MANUAL), tag_source=None)

    def test_an_untagged_spool_carries_no_provenance(self) -> None:
        assert a_spool().tag_uid is None
        assert a_spool().tag_source is None

    def test_an_unstated_provenance_registers_as_manual(self) -> None:
        """MANUAL is the honest floor — the same argument migration 0003's backfill makes.
        Claiming DETECTED for a tag nobody watched arrive would be invented history."""
        spool = register(
            material=Material.of(MaterialKind.PLA),
            colour=Colour.parse("000000"),
            opening_weight=Grams.of(1000),
            core_weight=Grams.of(250),
            registered_at=EPOCH,
            tag_uid=TagUid("A1B2C3D4"),
        )
        assert spool.tag_source is TagSource.MANUAL

    def test_a_provenance_without_a_tag_is_refused_rather_than_dropped(self) -> None:
        with pytest.raises(InvalidValueError):
            register(
                material=Material.of(MaterialKind.PLA),
                colour=Colour.parse("000000"),
                opening_weight=Grams.of(1000),
                core_weight=Grams.of(250),
                registered_at=EPOCH,
                tag_source=TagSource.DETECTED,
            )

    def test_a_manual_tag_can_be_changed(self) -> None:
        spool = a_tagged_spool(TagSource.MANUAL).with_tag(TagUid("BEEF0001"), TagSource.MANUAL)
        assert spool.tag_uid == TagUid("BEEF0001")
        assert spool.tag_source is TagSource.MANUAL

    def test_a_manual_tag_can_be_cleared(self) -> None:
        """`with_details` reads None as "unchanged", which is exactly why clearing needed a
        transition of its own: here None means *cleared*, and the pair goes together."""
        spool = a_tagged_spool(TagSource.MANUAL).with_tag(None, None)
        assert spool.tag_uid is None
        assert spool.tag_source is None

    def test_an_untagged_spool_can_be_given_a_manual_tag(self) -> None:
        spool = a_spool().with_tag(TagUid("A1B2C3D4"), TagSource.MANUAL)
        assert spool.tag_uid == TagUid("A1B2C3D4")
        assert spool.tag_source is TagSource.MANUAL

    def test_a_detected_tag_refuses_every_edit(self) -> None:
        """Neither changed nor cleared: the sync pass read it off the tray, and a ledger
        tag that no longer matches the reel mounts the wrong spool on the next pass."""
        spool = a_tagged_spool(TagSource.DETECTED)
        with pytest.raises(TagNotEditableError):
            spool.with_tag(TagUid("BEEF0001"), TagSource.MANUAL)
        with pytest.raises(TagNotEditableError):
            spool.with_tag(None, None)

    def test_only_a_detected_tag_is_uneditable(self) -> None:
        assert a_spool().is_tag_editable
        assert a_tagged_spool(TagSource.MANUAL).is_tag_editable
        assert not a_tagged_spool(TagSource.DETECTED).is_tag_editable

    def test_a_half_stated_pair_is_refused(self) -> None:
        with pytest.raises(InvalidValueError):
            a_spool().with_tag(TagUid("A1B2C3D4"), None)
        with pytest.raises(InvalidValueError):
            a_spool().with_tag(None, TagSource.MANUAL)

    def test_a_discarded_spool_keeps_its_tag(self) -> None:
        spool = a_tagged_spool(TagSource.MANUAL).discarded(at(days=1))
        with pytest.raises(SpoolDiscardedError):
            spool.with_tag(None, None)
