"""A human name for a colour, for labels the user never typed.

Auto-registration builds a spool's label from what the printer said, and two reels of one
product in two colours would otherwise be born with identical labels — the user's shelf
cannot tell "PLA Basic" from "PLA Basic" (docs/04-use-cases.md UC-02). The name is baked
into the label at registration time, because a label is stored user data rather than a
translated view: a Spanish instance writes Spanish once, and changing the language later
renames nothing.

Classification runs in HSL, not by nearest-RGB distance. RGB distance reads a dark red as
brown and a desaturated tan as grey, while hue survives darkening and desaturation — the
two things filament colours actually do. The achromatics are decided first, by lightness
and saturation, because hue means nothing without saturation.
"""

from __future__ import annotations

import colorsys
import re

from .colour import Colour

#: The anchor palette: bucket → (English, Spanish). Twenty names on purpose — a label is
#: read on a card across a room, and "Cerulean" helps nobody find a reel on a shelf.
_NAMES: dict[str, tuple[str, str]] = {
    "black": ("Black", "Negro"),
    "white": ("White", "Blanco"),
    "grey": ("Grey", "Gris"),
    "red": ("Red", "Rojo"),
    "dark_red": ("Dark Red", "Rojo oscuro"),
    "orange": ("Orange", "Naranja"),
    "brown": ("Brown", "Marrón"),
    "beige": ("Beige", "Beige"),
    "yellow": ("Yellow", "Amarillo"),
    "gold": ("Gold", "Dorado"),
    "lime": ("Lime", "Lima"),
    "green": ("Green", "Verde"),
    "dark_green": ("Dark Green", "Verde oscuro"),
    "teal": ("Teal", "Verde azulado"),
    "cyan": ("Cyan", "Cian"),
    "blue": ("Blue", "Azul"),
    "navy": ("Navy", "Azul marino"),
    "purple": ("Purple", "Morado"),
    "magenta": ("Magenta", "Magenta"),
    "pink": ("Pink", "Rosa"),
}

# The achromatic gates. Hue carries no information below _GREY_BELOW saturation, and a
# near-black of any hue reads as black on a shelf.
_BLACK_BELOW = 0.10
_DIM_GREY_BELOW = 0.16
_WHITE_ABOVE = 0.93
_GREY_BELOW = 0.12

# A washed-out warm tone is beige whatever its exact hue: low saturation, high lightness.
_BEIGE_SATURATION_BELOW = 0.35
_BEIGE_LIGHTNESS_ABOVE = 0.55


def colour_name(colour: Colour, language: str = "en") -> str:
    """The nearest anchor's name, in the instance's language.

    Any regional Spanish ("es", "es-419") gets the Spanish names; every other language
    falls back to English — the same fallback the panel translator makes.
    """
    english, spanish = _NAMES[_bucket(colour)]
    return spanish if _is_spanish(language) else english


def label_with_colour(name: str | None, colour: Colour, language: str = "en") -> str:
    """The auto-register label: the tray's product name, made unique by its colour.

    A name that already carries the colour word — in either language, whatever language
    is asked for — is left alone rather than doubled. The match is on whole words:
    "Limestone" contains the letters of Lime and names no colour at all, and dropping
    the suffix there would recreate the very collision this label exists to prevent.
    A reading with no name at all — or a blank one — yields the colour name by itself:
    the material and the vendor already render beside the label on every card, so the
    colour is the one thing the label still has to say.
    """
    if name is None or not name.strip():
        return colour_name(colour, language)
    lowered = name.lower()
    if any(
        re.search(rf"\b{re.escape(variant.lower())}\b", lowered)
        for variant in _NAMES[_bucket(colour)]
    ):
        return name
    return f"{name} {colour_name(colour, language)}"


def _is_spanish(language: str) -> bool:
    return language.replace("_", "-").split("-")[0].strip().lower() == "es"


def _bucket(colour: Colour) -> str:
    """Total over every colour: exactly one anchor, decided top-down, first match wins."""
    hue, lightness, saturation = _hls(colour)
    if lightness <= _BLACK_BELOW:
        return "black"
    if saturation < _GREY_BELOW:
        if lightness >= _WHITE_ABOVE:
            return "white"
        return "grey" if lightness > _DIM_GREY_BELOW else "black"
    if hue < 12 or hue >= 342:
        return "red" if lightness >= 0.28 else "dark_red"
    if hue < 42:
        if lightness < 0.35:
            return "brown"
        if saturation < _BEIGE_SATURATION_BELOW and lightness > _BEIGE_LIGHTNESS_ABOVE:
            return "beige"
        return "orange"
    if hue < 68:
        if saturation < _BEIGE_SATURATION_BELOW and lightness > _BEIGE_LIGHTNESS_ABOVE:
            return "beige"
        return "yellow" if lightness >= 0.48 else "gold"
    if hue < 100:
        return "lime"
    if hue < 150:
        return "green" if lightness >= 0.22 else "dark_green"
    if hue < 198:
        # Teal is a cyan that went dark; the hue alone cannot tell them apart.
        return "cyan" if lightness >= 0.4 else "teal"
    if hue < 252:
        return "blue" if lightness >= 0.22 else "navy"
    if hue < 295:
        return "purple"
    return "pink" if lightness > 0.62 else "magenta"


def _hls(colour: Colour) -> tuple[float, float, float]:
    """Hue in degrees, lightness and saturation in 0..1 — the terms `_bucket` reasons in."""
    hue, lightness, saturation = colorsys.rgb_to_hls(
        colour.red / 255, colour.green / 255, colour.blue / 255
    )
    return hue * 360.0, lightness, saturation
