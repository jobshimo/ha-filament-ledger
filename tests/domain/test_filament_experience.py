from dataclasses import replace

import pytest

from custom_components.filament_ledger.domain.error import InvalidValueError
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.spool_state import SpoolState
from tests.domain.conftest import EPOCH, a_spool


def test_partly_used_opening_is_active_without_extra_consumption() -> None:
    spool = replace(a_spool(), opened_at=EPOCH, colour_known=False)
    assert spool.state(balance=Grams.of(420), movement_count=1) is SpoolState.ACTIVE
    assert not spool.colour_known
    assert a_spool().state(balance=Grams.of(420), movement_count=1) is SpoolState.SEALED


def test_threshold_cannot_be_non_positive() -> None:
    with pytest.raises(InvalidValueError):
        replace(a_spool(), low_stock_threshold_mg=0)
