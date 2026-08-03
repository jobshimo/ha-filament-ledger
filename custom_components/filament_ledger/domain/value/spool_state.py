"""The lifecycle position of a spool.

**Derived, not stored.** Three of the five values are a function of the movement history and
the balance it produces; `discarded_at` and `deleted_at` are stored facts, because throwing
a spool away and retracting its registration are both decisions a human made at a particular
moment rather than computations.

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
    # A registration retracted: the spool was never really here (docs/14 §14.4.3).
    # Deliberately distinct from DISCARDED, which is a real-world event that counts as
    # waste in every statistic. DELETED counts as nothing, anywhere — and unlike a
    # discard it is restorable from the Trash, with its history intact.
    DELETED = "DELETED"

    @classmethod
    def derive(
        cls,
        *,
        discarded_at: datetime | None,
        balance: Grams,
        movement_count: int,
        deleted_at: datetime | None = None,
    ) -> SpoolState:
        """Total function: every spool lands on exactly one state.

        Evaluated top-down, first match wins. `movement_count` is 1 for a spool whose only
        movement is its OPENING_BALANCE, which is what SEALED means.

        `DELETED` is tested first. The two stored facts are mutually exclusive by flow —
        the intent modal is the only entry point, and a discarded spool never offers the
        delete affordance — but if a defect ever sets both, docs/14 §14.4.3 names the
        winner rather than leaving it to whichever branch an implementer wrote first.
        """
        if deleted_at is not None:
            return cls.DELETED
        if discarded_at is not None:
            return cls.DISCARDED
        if not balance.is_positive:
            return cls.DEPLETED
        if movement_count <= 1:
            return cls.SEALED
        return cls.ACTIVE

    @property
    def is_terminal(self) -> bool:
        """DISCARDED is the only state nothing leaves — the physical object is gone.

        `DELETED` is deliberately *not* terminal: a retraction is a bookkeeping statement
        about the ledger, not about the world, and the Trash exists to take it back.
        (Even DISCARDED has one narrow way out — voiding the whole-spool `DISCARD`
        movement, docs/14 §14.4.1 — but that undoes the *entry*, and the entry is what
        this property is silent about.)
        """
        return self is SpoolState.DISCARDED

    @property
    def counts_as_stock(self) -> bool:
        """Whether this spool's balance belongs in the total-stock figure.

        Neither retired state does: a discarded spool's grams are waste, and a deleted
        spool's grams were never really here (docs/14 §14.4.5).
        """
        return self not in (SpoolState.DISCARDED, SpoolState.DELETED)
