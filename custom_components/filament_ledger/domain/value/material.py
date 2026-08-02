"""Filament material.

Carries a nominal density because density is a property of the material, not of whichever
estimator happens to need it. Scattering it across estimator implementations is how two of
them end up disagreeing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Self

from ..error import InvalidValueError


class MaterialKind(StrEnum):
    PLA = "PLA"
    PETG = "PETG"
    ABS = "ABS"
    ASA = "ASA"
    TPU = "TPU"
    PC = "PC"
    PA = "PA"
    PVA = "PVA"
    SUPPORT = "SUPPORT"
    OTHER = "OTHER"


# Nominal densities in g/cm³. Vendor figures vary by a few percent; these are only used by
# estimators that work in length rather than mass, and never to derive a balance.
_NOMINAL_DENSITY: dict[MaterialKind, Decimal] = {
    MaterialKind.PLA: Decimal("1.24"),
    MaterialKind.PETG: Decimal("1.27"),
    MaterialKind.ABS: Decimal("1.04"),
    MaterialKind.ASA: Decimal("1.07"),
    MaterialKind.TPU: Decimal("1.21"),
    MaterialKind.PC: Decimal("1.20"),
    MaterialKind.PA: Decimal("1.14"),
    MaterialKind.PVA: Decimal("1.23"),
    MaterialKind.SUPPORT: Decimal("1.24"),
    MaterialKind.OTHER: Decimal("1.24"),
}


@dataclass(frozen=True, slots=True)
class Material:
    """A material type, with a free-text name when the kind is `OTHER`."""

    kind: MaterialKind
    other_name: str | None = None

    def __post_init__(self) -> None:
        if self.kind is MaterialKind.OTHER:
            if not (self.other_name or "").strip():
                msg = "Material.OTHER requires a name"
                raise InvalidValueError(msg)
        elif self.other_name is not None:
            msg = f"other_name is only valid for OTHER, not {self.kind}"
            raise InvalidValueError(msg)

    @classmethod
    def of(cls, kind: MaterialKind) -> Self:
        return cls(kind)

    @classmethod
    def other(cls, name: str) -> Self:
        return cls(MaterialKind.OTHER, name)

    @property
    def density_g_cm3(self) -> Decimal:
        return _NOMINAL_DENSITY[self.kind]

    @property
    def display_name(self) -> str:
        return self.other_name if self.kind is MaterialKind.OTHER and self.other_name else self.kind

    def __str__(self) -> str:
        return self.display_name
