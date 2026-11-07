"""The single registration calculation used by preview and commit."""

from ..error import QuantityInputError
from ..value.grams import Grams

NET_BASES = frozenset(
    {"DECLARED_NET", "MEASURED_NET", "CONFIGURED_DEFAULT", "DETECTED_NOMINAL", "LEGACY_UNSPECIFIED"}
)


def registration_quantity(
    basis: str, net: Grams | None, gross: Grams | None = None, tare: Grams | None = None
) -> Grams:
    if basis == "MEASURED_GROSS":
        if gross is None or tare is None:
            raise QuantityInputError(
                "gross_weight_g" if gross is None else "core_weight_g",
                "Gross quantity requires gross and tare",
            )
        if tare.is_negative or gross <= tare or gross.milligrams > 10_000_000:
            raise QuantityInputError(
                "gross_weight_g", "Gross must exceed non-negative tare and be at most 10000 g"
            )
        quantity = gross - tare
        if net is not None and net != quantity:
            raise QuantityInputError("opening_weight_g", "Net quantity must match gross minus tare")
    elif basis in NET_BASES:
        if net is None or gross is not None or tare is not None:
            raise QuantityInputError(
                "opening_weight_g", "Net quantity requires net, without gross or tare"
            )
        quantity = net
    else:
        raise QuantityInputError("opening_basis", "Unknown opening quantity basis")
    if not 0 < quantity.milligrams <= 10_000_000:
        raise QuantityInputError(
            "opening_weight_g", "Opening quantity must be positive and at most 10000 g"
        )
    return quantity
