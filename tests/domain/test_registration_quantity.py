import pytest

from custom_components.filament_ledger.domain.error import InvalidValueError
from custom_components.filament_ledger.domain.service.registration_quantity import (
    registration_quantity,
)
from custom_components.filament_ledger.domain.value.grams import Grams


def test_gross_preview_and_create_share_exact_quantity() -> None:
    assert registration_quantity("MEASURED_GROSS", None, Grams.of(670), Grams.of(250)) == Grams.of(
        420
    )


@pytest.mark.parametrize(("gross", "tare"), [(250, 250), (200, 250)])
def test_invalid_gross_is_not_an_opening_quantity(gross: int, tare: int) -> None:
    with pytest.raises(InvalidValueError):
        registration_quantity("MEASURED_GROSS", None, Grams.of(gross), Grams.of(tare))


@pytest.mark.parametrize("net", ["0", "-1", "10000.001", "NaN", "Infinity"])
def test_net_quantity_is_finite_positive_and_bounded(net: str) -> None:
    with pytest.raises(InvalidValueError):
        registration_quantity("DECLARED_NET", Grams.of(net))
