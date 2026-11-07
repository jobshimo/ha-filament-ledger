"""Pure current-stock and crossing decisions; quantities are integer milligrams."""

from ..model.spool import Spool
from ..value.spool_state import SpoolState


def is_low_stock(spool: Spool, state: SpoolState, balance_mg: int, threshold_mg: int) -> bool:
    return (
        spool.is_in_inventory
        and state in (SpoolState.ACTIVE, SpoolState.DEPLETED)
        and balance_mg < threshold_mg
    )


def crossing(
    before_mg: int, after_mg: int, threshold_mg: int, *, armed: bool, eligible: bool
) -> tuple[bool, bool]:
    """Return (occurrence, next armed state); exact equality re-arms."""
    emit = eligible and armed and before_mg >= threshold_mg and after_mg < threshold_mg
    return emit, eligible and after_mg >= threshold_mg
