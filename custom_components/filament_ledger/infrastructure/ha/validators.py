"""Bounded exact quantities at Home Assistant input boundaries."""

import voluptuous as vol

from ...domain.error import InvalidValueError
from ...domain.value.grams import Grams


def low_stock_threshold(value: object) -> float:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise vol.Invalid("Threshold must be positive grams")
    try:
        quantity = Grams.of(value)
    except (InvalidValueError, TypeError) as exc:
        raise vol.Invalid(str(exc)) from exc
    if not 0 < quantity.milligrams <= 10_000_000:
        raise vol.Invalid("Threshold must be positive and at most 10000 g")
    return float(quantity.as_decimal)
