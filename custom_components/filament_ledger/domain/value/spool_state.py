"""The lifecycle position of a spool.

**Derived, not stored.** Three of the four values are a function of the movement history and
the balance it produces; only `discarded_at` is a stored fact, because discarding is a
decision a human made at a particular moment rather than a computation.

Storing the state alongside a computed balance is the two-sources-of-truth arrangement
ADR-0001 rejects: a bad write flips a spool to DEPLETED while its movements sum to 340 g,
and nothing detects the disagreement. Deriving it means the disagreement cannot exist.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from .grams import Grams


class SpoolState(StrEnum):
    SEALED = "SEALED"
    ACTIVE = "ACTIVE"
    DEPLETED = "DEPLETED"
    DISCARDED = "DISCARDED"

    @classmethod
    def derive(
        cls,
        *,
        discarded_at: datetime | None,
        balance: Grams,
        movement_count: int,
    ) -> SpoolState:
        """Total function: every spool lands on exactly one state.

        Evaluated top-down, first match wins. `movement_count` is 1 for a spool whose only
        movement is its OPENING_BALANCE, which is what SEALED means.
        """
        if discarded_at is not None:
            return cls.DISCARDED
        if not balance.is_positive:
            return cls.DEPLETED
        if movement_count <= 1:
            return cls.SEALED
        return cls.ACTIVE

    @property
    def is_terminal(self) -> bool:
        """DISCARDED is the only state nothing leaves — the physical object is gone."""
        return self is SpoolState.DISCARDED

    @property
    def counts_as_stock(self) -> bool:
        """Whether this spool's balance belongs in the total-stock figure."""
        return self is not SpoolState.DISCARDED
