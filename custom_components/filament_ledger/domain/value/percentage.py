"""A ratio in 0..100.

Kept distinct from `Grams` deliberately. The whole reason `Grams` exists is that a `float`
lets you add a mass to a percentage; leaving the percentage a bare `float` would keep half
the hole open.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Self

from ..error import InvalidValueError

MIN_PERCENT = Decimal(0)
MAX_PERCENT = Decimal(100)


@dataclass(frozen=True, order=True, slots=True)
class Percentage:
    value: Decimal

    def __post_init__(self) -> None:
        # Only the range is checked here. That `value` is a Decimal is enforced statically by
        # `mypy --strict`, and re-asserting it at runtime would be unreachable code that
        # `warn_unreachable` correctly rejects. Use `of()` to convert from anything else.
        if not MIN_PERCENT <= self.value <= MAX_PERCENT:
            msg = f"Percentage must be 0..100, got {self.value}"
            raise InvalidValueError(msg)

    @classmethod
    def of(cls, value: int | float | str | Decimal) -> Self:
        return cls(Decimal(str(value)) if isinstance(value, float) else Decimal(value))

    @classmethod
    def from_ratio(cls, ratio: Decimal) -> Self:
        """Build from a 0..1 fraction, clamping so a negative balance reads as 0%.

        Clamping is a display decision, not an accounting one: the ledger still records the
        negative balance and `AnomalyDetector` still flags it. A progress bar simply has
        nowhere to draw -4%.
        """
        return cls(min(max(ratio * 100, MIN_PERCENT), MAX_PERCENT))

    @property
    def rounded(self) -> int:
        """Whole percent, rounded to nearest — the display rule in docs/06-ui-spec.md §6.7."""
        return int(self.value.quantize(Decimal(1), rounding=ROUND_HALF_UP))

    def __str__(self) -> str:
        return f"{self.rounded}%"
