"""Colour, Material, Percentage, TrayReading and the identity types.

An invalid value object cannot exist. These tests are the proof of that sentence.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

from custom_components.filament_ledger.domain.error import DomainError, InvalidValueError
from custom_components.filament_ledger.domain.value.colour import BLACK, WHITE, Colour
from custom_components.filament_ledger.domain.value.identifiers import (
    UNIDENTIFIED_PRINTER,
    AmsIndex,
    PrinterSerial,
    SlotIndex,
    TagUid,
    TrayRef,
)
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.percentage import Percentage
from custom_components.filament_ledger.domain.value.tray_reading import TrayReading

from .conftest import A_PRINTER, ANOTHER_PRINTER, a_tray


class TestColour:
    @pytest.mark.parametrize("text", ["000000", "#000000", "000000FF", "#000000FF"])
    def test_accepts_rgb_and_rgba_with_or_without_a_hash(self, text: str) -> None:
        assert Colour.parse(text) == BLACK

    def test_missing_alpha_defaults_to_opaque(self) -> None:
        assert Colour.parse("FF8800").alpha == 255

    def test_alpha_is_not_discarded(self) -> None:
        """`00000000` is transparent black, not black. The printer sends RRGGBBAA and the
        storage format matches it exactly to avoid a lossy conversion at the boundary."""
        assert Colour.parse("00000000").alpha == 0
        assert Colour.parse("00000000") != BLACK

    def test_round_trips_through_the_storage_form(self) -> None:
        assert Colour.parse("FF880080").hex8 == "FF880080"

    def test_display_hex_drops_the_alpha(self) -> None:
        assert Colour.parse("FF880080").display_hex == "#FF8800"

    @pytest.mark.parametrize("text", ["", "FFF", "GGGGGG", "12345", "#12345678900"])
    def test_rejects_anything_that_is_not_a_colour(self, text: str) -> None:
        with pytest.raises(ValueError, match="Colour"):
            Colour.parse(text)

    def test_channels_are_bounded(self) -> None:
        with pytest.raises(ValueError, match=r"0..255"):
            Colour(256, 0, 0)
        with pytest.raises(ValueError, match=r"0..255"):
            Colour(-1, 0, 0)

    def test_black_filament_gets_white_text(self) -> None:
        """A swatch has to be legible whether the filament is black or white."""
        assert BLACK.foreground == WHITE

    def test_white_filament_gets_black_text(self) -> None:
        assert WHITE.foreground == BLACK

    def test_a_mid_orange_gets_black_text(self) -> None:
        assert Colour.parse("FF8800").foreground == BLACK


class TestMaterial:
    def test_density_lives_with_the_material(self) -> None:
        """Density is a property of the material, not of whichever estimator needs it."""
        assert Material.of(MaterialKind.PETG).density_g_cm3 == Decimal("1.27")
        assert Material.of(MaterialKind.ABS).density_g_cm3 == Decimal("1.04")

    def test_every_kind_has_a_density(self) -> None:
        for kind in MaterialKind:
            material = (
                Material.other("Nylon-X") if kind is MaterialKind.OTHER else Material.of(kind)
            )
            assert material.density_g_cm3 > 0

    def test_other_requires_a_name(self) -> None:
        with pytest.raises(ValueError, match="requires a name"):
            Material(MaterialKind.OTHER)
        with pytest.raises(ValueError, match="requires a name"):
            Material(MaterialKind.OTHER, "   ")

    def test_a_name_is_only_valid_for_other(self) -> None:
        with pytest.raises(ValueError, match="only valid for OTHER"):
            Material(MaterialKind.PLA, "Something")

    def test_display_name(self) -> None:
        assert Material.of(MaterialKind.PLA).display_name == "PLA"
        assert Material.other("Nylon-X").display_name == "Nylon-X"


class TestPercentage:
    def test_bounded_to_zero_and_one_hundred(self) -> None:
        with pytest.raises(ValueError, match=r"0..100"):
            Percentage.of(101)
        with pytest.raises(ValueError, match=r"0..100"):
            Percentage.of(-1)

    def test_from_ratio_clamps_rather_than_raising(self) -> None:
        assert Percentage.from_ratio(Decimal("-0.04")).rounded == 0
        assert Percentage.from_ratio(Decimal("1.5")).rounded == 100

    @pytest.mark.parametrize(
        ("ratio", "expected"),
        [("0.612", 61), ("0.847", 85), ("0.3397", 34), ("0.0667", 7), ("0.421", 42)],
    )
    def test_rounds_to_nearest_whole_percent(self, ratio: str, expected: int) -> None:
        """docs/06-ui-spec.md §6.7. Four layers of sixty is 7%, not 6%."""
        assert Percentage.from_ratio(Decimal(ratio)).rounded == expected

    def test_string_form(self) -> None:
        assert str(Percentage.of(61.2)) == "61%"


class TestTagUid:
    def test_a_blank_tag_cannot_exist(self) -> None:
        with pytest.raises(ValueError, match="blank"):
            TagUid("")
        with pytest.raises(ValueError, match="blank"):
            TagUid("   ")

    def test_sixteen_zeros_denotes_absence_not_identity(self) -> None:
        """The printer reports `0000000000000000` for a spool with no readable tag.

        Accepting it as a value would merge every untagged spool the owner ever buys into
        one — the gateway translates it to None, and this rejection is the backstop that
        makes the bug unrepresentable. See docs/12-field-notes.md."""
        with pytest.raises(ValueError, match="absent"):
            TagUid("0000000000000000")

    def test_tags_compare_by_value(self) -> None:
        assert TagUid("A1B2") == TagUid("A1B2")


class TestTrayReading:
    def test_carries_what_one_tray_reports(self) -> None:
        reading = TrayReading(
            tray=a_tray(2),
            tag=TagUid("A1B2C3D4"),
            empty=False,
            name="Bambu PLA Basic",
            material="PLA",
            colour=Colour.parse("#5E43B7FF"),
        )
        assert reading.tag == TagUid("A1B2C3D4")
        assert str(reading) == "tray 2: A1B2C3D4"

    def test_an_occupied_tray_may_have_no_readable_tag(self) -> None:
        """A third-party or refilled spool: present, feeding the printer, anonymous."""
        reading = TrayReading(tray=a_tray(3), tag=None, empty=False)
        assert not reading.empty
        assert reading.tag is None

    def test_an_empty_tray_cannot_carry_a_tag(self) -> None:
        """Contradictory data must fail at construction — the empty branch unmounts
        whatever the ledger has in that slot, and it must never act on a reading that
        refutes itself."""
        with pytest.raises(ValueError, match="empty"):
            TrayReading(tray=a_tray(1), tag=TagUid("A1B2"), empty=True)

    def test_a_blank_hint_cannot_exist(self) -> None:
        with pytest.raises(ValueError, match="hint"):
            TrayReading(tray=a_tray(1), tag=None, empty=False, name="   ")
        with pytest.raises(ValueError, match="hint"):
            TrayReading(tray=a_tray(1), tag=None, empty=False, material="")


class TestTrayRef:
    """The three-part reference: what actually identifies a tray.

    A bare tray number stopped being an address the moment the model could hold a second
    machine, and these are the facts that follow — all three parts are the identity, and
    the whole thing sorts, because every reader that walks trays in order depends on one
    canonical order rather than on inventing its own.
    """

    def test_the_same_tray_number_on_two_printers_is_two_trays(self) -> None:
        assert a_tray(1) != a_tray(1, printer=ANOTHER_PRINTER)

    def test_the_same_tray_number_on_two_ams_units_is_two_trays(self) -> None:
        assert a_tray(1) != a_tray(1, ams=2)

    def test_all_three_parts_together_are_the_identity(self) -> None:
        assert a_tray(1) == TrayRef(printer=A_PRINTER, ams=AmsIndex(1), slot=SlotIndex(1))

    def test_trays_sort_by_printer_then_ams_then_slot(self) -> None:
        unordered = [a_tray(2), a_tray(1, ams=2), a_tray(1, printer=ANOTHER_PRINTER), a_tray(1)]
        assert sorted(unordered) == [
            a_tray(1, printer=ANOTHER_PRINTER),
            a_tray(1),
            a_tray(2),
            a_tray(1, ams=2),
        ]

    def test_it_names_all_three_parts_when_it_speaks(self) -> None:
        assert str(a_tray(3)) == f"AMS 1 tray 3 on printer {A_PRINTER}"


class TestUnidentifiedPrinter:
    """The sentinel migration 0007 writes, and why it is a name rather than a gap.

    `TagUid` refuses its own sentinel because sixteen zeros denotes *absence*, and treating
    absence as identity would merge every untagged spool into one. This one is accepted for
    the opposite reason: it denotes a real machine whose name nobody recorded, and a
    single-printer ledger has exactly one of those — so every tray under it is a tray of
    the same printer, which is the whole argument that migration 0007 loses nothing.
    """

    def test_it_is_a_usable_serial_unlike_the_absent_tag_sentinel(self) -> None:
        assert PrinterSerial("UNIDENTIFIED") == UNIDENTIFIED_PRINTER
        assert str(UNIDENTIFIED_PRINTER) == "UNIDENTIFIED"

    def test_every_tray_under_it_belongs_to_one_printer(self) -> None:
        under = {
            TrayRef(printer=UNIDENTIFIED_PRINTER, ams=AmsIndex(1), slot=SlotIndex(slot))
            for slot in (1, 2, 3, 4)
        }
        assert len(under) == 4
        assert len({tray.printer for tray in under}) == 1


class TestEveryValueObjectRaisesTheDomainError:
    """One catch clause at the adapter has to cover all of them.

    `InvalidValueError` subclasses both `DomainError` and `ValueError`. That is what lets
    the websocket layer and the service layer catch every malformed field with a single
    `except DomainError`, instead of maintaining a list of fields to re-validate — a list
    somebody forgets to extend, where the forgotten entry surfaces as a stack trace.
    """

    def test_it_is_both_a_domain_error_and_a_value_error(self) -> None:
        assert issubclass(InvalidValueError, DomainError)
        assert issubclass(InvalidValueError, ValueError)

    @pytest.mark.parametrize(
        "construct",
        [
            pytest.param(lambda: Colour.parse("nonsense"), id="colour-format"),
            pytest.param(lambda: Colour.parse("GGGGGG"), id="colour-not-hex"),
            pytest.param(lambda: Colour(300, 0, 0), id="colour-channel"),
            pytest.param(lambda: SlotIndex(9), id="slot-out-of-range"),
            pytest.param(lambda: AmsIndex(0), id="ams-index-below-one"),
            pytest.param(lambda: PrinterSerial("  "), id="printer-serial-blank"),
            pytest.param(lambda: TagUid("   "), id="tag-blank"),
            pytest.param(lambda: TagUid("0000000000000000"), id="tag-absence-sentinel"),
            pytest.param(
                lambda: TrayReading(tray=a_tray(1), tag=TagUid("A1"), empty=True),
                id="tray-empty-yet-tagged",
            ),
            pytest.param(lambda: Material(MaterialKind.OTHER), id="material-unnamed"),
            pytest.param(lambda: Material(MaterialKind.PLA, "x"), id="material-named-non-other"),
            pytest.param(lambda: Percentage.of(101), id="percentage-range"),
        ],
    )
    def test_every_rejection_is_catchable_as_a_domain_error(
        self, construct: Callable[[], object]
    ) -> None:
        with pytest.raises(DomainError):
            construct()
