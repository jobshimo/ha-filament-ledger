"""Filament mass.

Stored as an integer number of milligrams. Floating point across thousands of accumulated
movements drifts, and a ledger that drifts is a ledger nobody trusts.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Self

MILLIGRAMS_PER_GRAM = 1000


@dataclass(frozen=True, order=True, slots=True)
class Grams:
    """A signed quantity of filament mass, exact to the milligram.

    Signed values are permitted because a reconciliation may *increase* a balance, if the
    opening weight was understated. Rejecting negatives here would force the ledger to lie.

    Cannot be combined with a bare number. That is the point of the type: a `float` lets
    you add grams to a percentage and find out in production.
    """

    milligrams: int

    def __post_init__(self) -> None:
        if not isinstance(self.milligrams, int) or isinstance(self.milligrams, bool):
            msg = (
                f"Grams requires an int number of milligrams, got {type(self.milligrams).__name__}"
            )
            raise TypeError(msg)

    # -- construction ------------------------------------------------------------------

    @classmethod
    def of(cls, grams: int | float | str | Decimal) -> Self:
        """Build from a gram figure.

        Conversion goes through `Decimal` rather than `float` arithmetic so that
        `Grams.of(24.5)` is exactly 24 500 mg and not 24 499 or 24 501.
        """
        if isinstance(grams, bool):
            msg = "Grams.of() does not accept a bool"
            raise TypeError(msg)
        decimal_grams = Decimal(str(grams)) if isinstance(grams, float) else Decimal(grams)
        milligrams = (decimal_grams * MILLIGRAMS_PER_GRAM).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
        return cls(int(milligrams))

    @classmethod
    def zero(cls) -> Self:
        return cls(0)

    # -- inspection --------------------------------------------------------------------

    @property
    def as_decimal(self) -> Decimal:
        """The exact gram value. Use this for display; never for accumulation."""
        return Decimal(self.milligrams) / MILLIGRAMS_PER_GRAM

    @property
    def is_zero(self) -> bool:
        return self.milligrams == 0

    @property
    def is_negative(self) -> bool:
        return self.milligrams < 0

    @property
    def is_positive(self) -> bool:
        return self.milligrams > 0

    # -- arithmetic --------------------------------------------------------------------

    def __add__(self, other: Grams) -> Grams:
        if not isinstance(other, Grams):
            return NotImplemented
        return Grams(self.milligrams + other.milligrams)

    def __sub__(self, other: Grams) -> Grams:
        if not isinstance(other, Grams):
            return NotImplemented
        return Grams(self.milligrams - other.milligrams)

    def __neg__(self) -> Grams:
        return Grams(-self.milligrams)

    def __abs__(self) -> Grams:
        return Grams(abs(self.milligrams))

    def scaled_by(self, ratio: Decimal | int | float) -> Grams:
        """Multiply by a dimensionless ratio, rounding to the nearest milligram.

        Named rather than `__mul__` so that `grams * grams` — which would be an area — is
        not expressible at all.
        """
        factor = Decimal(str(ratio)) if isinstance(ratio, float) else Decimal(ratio)
        scaled = (Decimal(self.milligrams) * factor).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return Grams(int(scaled))

    def ratio_to(self, whole: Grams) -> Decimal:
        """This quantity as a fraction of `whole`. Zero when `whole` is zero."""
        if whole.milligrams == 0:
            return Decimal(0)
        return Decimal(self.milligrams) / Decimal(whole.milligrams)

    # -- display -----------------------------------------------------------------------

    def __str__(self) -> str:
        return f"{self.as_decimal:.1f} g"

    def __repr__(self) -> str:
        return f"Grams.of('{self.as_decimal}')"


def total(quantities: list[Grams]) -> Grams:
    """Sum a list of quantities. Exact, because the addends are integers."""
    return Grams(sum(quantity.milligrams for quantity in quantities))
