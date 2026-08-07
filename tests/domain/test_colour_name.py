"""The colour vocabulary behind auto-generated labels.

Representative hexes rather than exhaustive sweeps: each anchor is pinned by the reading
that matters — the Bambu catalogue's own classics, and the tones nearest-RGB distance is
known to misread (a dark red as brown, a desaturated tan as grey).
"""

from __future__ import annotations

import pytest

from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.colour_name import (
    colour_name,
    label_with_colour,
)

YELLOW = Colour.parse("FFFF00")


class TestColourName:
    @pytest.mark.parametrize(
        ("hex_value", "expected"),
        [
            pytest.param("000000", "Black", id="bambu-black"),
            pytest.param("FFFFFF", "White", id="jade-white"),
            pytest.param("00AE42", "Green", id="bambu-green"),
            pytest.param("8E9089", "Grey", id="bambu-grey"),
            pytest.param("FF6A13", "Orange", id="bambu-orange"),
            pytest.param("8B0000", "Dark Red", id="a-dark-red-not-brown"),
            pytest.param("B8A88A", "Beige", id="a-desaturated-tan-not-grey"),
            pytest.param("FFFF00", "Yellow", id="yellow"),
            pytest.param("0A2989", "Blue", id="bambu-blue"),
            pytest.param("101040", "Navy", id="a-blue-gone-dark"),
            pytest.param("6A0DAD", "Purple", id="purple"),
            pytest.param("008080", "Teal", id="a-cyan-gone-dark"),
        ],
    )
    def test_the_classics_land_on_their_names(self, hex_value: str, expected: str) -> None:
        assert colour_name(Colour.parse(hex_value)) == expected

    def test_the_printers_own_rrggbbaa_form_is_accepted(self) -> None:
        """Bambu colours arrive as #RRGGBBAA; the alpha changes nothing about the name."""
        assert colour_name(Colour.parse("#00AE42FF")) == "Green"

    def test_spanish_names_for_any_regional_spanish(self) -> None:
        assert colour_name(YELLOW, "es") == "Amarillo"
        assert colour_name(YELLOW, "es-419") == "Amarillo"
        # Every other language falls back to English, as the panel translator does.
        assert colour_name(YELLOW, "de") == "Yellow"


class TestLabelWithColour:
    def test_the_colour_word_joins_the_product_name(self) -> None:
        assert label_with_colour("Bambu PLA Basic", YELLOW, "en") == "Bambu PLA Basic Yellow"
        assert label_with_colour("Bambu PLA Basic", YELLOW, "es") == "Bambu PLA Basic Amarillo"

    def test_a_nameless_reading_is_labelled_by_colour_alone(self) -> None:
        """The material and vendor already render beside the label on every card, so the
        colour is the one thing the label still has to say."""
        assert label_with_colour(None, YELLOW, "en") == "Yellow"

    def test_a_name_already_carrying_the_word_is_left_alone(self) -> None:
        """In either language, whatever language is asked for — "PLA Basic Black Negro"
        would be the doubling this guard exists to prevent."""
        black = Colour.parse("000000")
        assert label_with_colour("PLA Basic Black", black, "es") == "PLA Basic Black"
        assert label_with_colour("PLA Basic negro", black, "en") == "PLA Basic negro"

    def test_a_word_hiding_inside_another_does_not_count(self) -> None:
        """ "Limestone" contains the letters of Lime and names no colour: dropping the
        suffix there would recreate the very collision the label exists to prevent."""
        lime = Colour.parse("BFFF00")
        assert label_with_colour("PLA Limestone", lime, "en") == "PLA Limestone Lime"
        assert label_with_colour("PLA Lime", lime, "en") == "PLA Lime"

    def test_a_blank_name_reads_as_no_name(self) -> None:
        """The boundary normalises blank to None, and this function promises the same
        answer for both rather than a label born with a leading space."""
        assert label_with_colour("", YELLOW, "en") == "Yellow"
        assert label_with_colour("   ", YELLOW, "en") == "Yellow"
