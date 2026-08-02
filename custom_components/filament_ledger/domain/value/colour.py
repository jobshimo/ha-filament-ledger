"""Filament colour.

Stored as RRGGBBAA to match the printer's own format, avoiding a lossy conversion at the
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

_CHANNEL_MAX = 255
_RGB_LENGTH = 6
_RGBA_LENGTH = 8

# Rec. 709 luma coefficients, scaled to integers so the comparison stays exact.
_LUMA_RED = 2126
_LUMA_GREEN = 7152
_LUMA_BLUE = 722
_LUMA_TOTAL = 10000

# Below this relative luminance a swatch needs white text on it rather than black.
_DARK_THRESHOLD = _CHANNEL_MAX * _LUMA_TOTAL // 2


@dataclass(frozen=True, slots=True)
class Colour:
    red: int
    green: int
    blue: int
    alpha: int = _CHANNEL_MAX

    def __post_init__(self) -> None:
        for name in ("red", "green", "blue", "alpha"):
            channel = getattr(self, name)
            if not isinstance(channel, int) or isinstance(channel, bool):
                msg = f"{name} must be an int, got {type(channel).__name__}"
                raise TypeError(msg)
            if not 0 <= channel <= _CHANNEL_MAX:
                msg = f"{name} must be 0..{_CHANNEL_MAX}, got {channel}"
                raise ValueError(msg)

    @classmethod
    def parse(cls, value: str) -> Self:
        """Accept `RRGGBB` or `RRGGBBAA`, with or without a leading `#`."""
        text = value.strip().removeprefix("#")
        if len(text) not in (_RGB_LENGTH, _RGBA_LENGTH):
            msg = f"Colour must be RRGGBB or RRGGBBAA, got {value!r}"
            raise ValueError(msg)
        try:
            channels = [int(text[i : i + 2], 16) for i in range(0, len(text), 2)]
        except ValueError as error:
            msg = f"Colour must be hexadecimal, got {value!r}"
            raise ValueError(msg) from error
        if len(channels) == _RGB_LENGTH // 2:
            channels.append(_CHANNEL_MAX)
        return cls(*channels)

    @property
    def hex8(self) -> str:
        """The storage form: `RRGGBBAA`, uppercase, no prefix."""
        return f"{self.red:02X}{self.green:02X}{self.blue:02X}{self.alpha:02X}"

    @property
    def display_hex(self) -> str:
        """The CSS form the UI paints a swatch with."""
        return f"#{self.red:02X}{self.green:02X}{self.blue:02X}"

    @property
    def is_dark(self) -> bool:
        luma = self.red * _LUMA_RED + self.green * _LUMA_GREEN + self.blue * _LUMA_BLUE
        return luma < _DARK_THRESHOLD

    @property
    def foreground(self) -> Colour:
        """A legible text colour for this swatch.

        Exists so that a label over black filament and a label over white filament are both
        readable without the UI reimplementing contrast maths per view.
        """
        return WHITE if self.is_dark else BLACK

    def __str__(self) -> str:
        return self.display_hex


BLACK = Colour(0, 0, 0)
WHITE = Colour(_CHANNEL_MAX, _CHANNEL_MAX, _CHANNEL_MAX)
