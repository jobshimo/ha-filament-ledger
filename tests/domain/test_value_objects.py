"""Colour, Material, Percentage and the identity types.

An invalid value object cannot exist. These tests are the proof of that sentence.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from custom_components.filament_ledger.domain.value.colour import BLACK, WHITE, Colour
from custom_components.filament_ledger.domain.value.identifiers import TagUid
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.percentage import Percentage


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
        """docs/06-ui-spec.md §6.6. Four layers of sixty is 7%, not 6%."""
        assert Percentage.from_ratio(Decimal(ratio)).rounded == expected

    def test_string_form(self) -> None:
        assert str(Percentage.of(61.2)) == "61%"


class TestTagUid:
    def test_a_blank_tag_cannot_exist(self) -> None:
        with pytest.raises(ValueError, match="blank"):
            TagUid("")
        with pytest.raises(ValueError, match="blank"):
            TagUid("   ")

    def test_tags_compare_by_value(self) -> None:
        assert TagUid("A1B2") == TagUid("A1B2")
