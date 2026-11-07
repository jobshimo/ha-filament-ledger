import pytest

from custom_components.filament_ledger.domain.service.low_stock import crossing


@pytest.mark.parametrize(
    ("before", "after", "armed", "eligible", "expected"),
    [
        (100000, 99000, True, True, (True, False)),
        (99000, 98000, False, True, (False, False)),
        (99000, 100000, False, True, (False, True)),
        (100000, 99000, True, False, (False, False)),
    ],
)
def test_integer_threshold_boundary(
    before: int, after: int, armed: bool, eligible: bool, expected: tuple[bool, bool]
) -> None:
    assert crossing(before, after, 100000, armed=armed, eligible=eligible) == expected
