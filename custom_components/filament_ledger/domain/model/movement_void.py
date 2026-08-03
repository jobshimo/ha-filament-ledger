"""The record that a ledger entry was deleted from the history the user sees.

**Not a ledger.** A movement stays immutable at all three layers; this is a *status record
about* one — when it was voided, why, which reversal returned the grams, and whether the
chapter has since been closed by a reinstatement (docs/adr/0007, docs/14 §14.4.1).

That separation is the whole point of the design. `movement` needs no `voided` flag, so its
triggers need no exception, so the rule that makes every number in this product trustworthy
keeps holding without a carve-out. The two reinstatement fields are the only values in the
correction design that are ever written after insert, and they are written *here*, on the
status record, never on the entry it describes.

Balances never consult this table. A voided entry and its reversal sum to zero, so
`balance = Σ(movements)` needs no amendment and no second source of truth — a forgotten
filter can mis-*display*, never mis-*count*.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from ..error import InvalidValueError
from ..value.identifiers import MovementId


@dataclass(frozen=True, slots=True)
class MovementVoid:
    """One void chapter. Open until a reinstatement closes it.

    `reversal_movement_id` is `None` exactly when the entry was voided **without
    restitution** — the spool was already out of inventory, there was nothing to return
    to, and `reason` is mandatory in that branch because a null reversal with no
    explanation reads as a bug six months later.
    """

    movement_id: MovementId
    voided_at: datetime
    reason: str | None = None
    reversal_movement_id: MovementId | None = None
    reinstated_at: datetime | None = None
    reinstatement_movement_id: MovementId | None = None

    def __post_init__(self) -> None:
        # The same two rules migration 0003 spells as CHECK clauses. Stated here as well
        # because a constraint name is not an answer a user can act on, and because the
        # entity is what the use cases reason about.
        if (self.reinstated_at is None) != (self.reinstatement_movement_id is None):
            msg = "a chapter is closed by both reinstatement facts together or by neither"
            raise InvalidValueError(msg)
        if self.reinstatement_movement_id is not None and self.reversal_movement_id is None:
            msg = (
                "a void without restitution returned nothing, so there is nothing to "
                "deduct again: it can never be reinstated"
            )
            raise InvalidValueError(msg)
        if self.reversal_movement_id is None and not (self.reason or "").strip():
            msg = "a void without restitution needs a reason — it must say why nothing came back"
            raise InvalidValueError(msg)

    @property
    def is_open(self) -> bool:
        """Whether the entry is currently out of the default views.

        A closed chapter is history like any other: all three rows show in the global
        History, labelled, and the net is honest.
        """
        return self.reinstatement_movement_id is None

    @property
    def had_restitution(self) -> bool:
        """Whether voiding returned the grams. The other kind can never be reinstated."""
        return self.reversal_movement_id is not None

    def reinstated(self, movement_id: MovementId, at: datetime) -> MovementVoid:
        """Close the chapter. Refuses to close one that is already closed or terminal."""
        if not self.is_open:
            msg = f"the void of movement {self.movement_id} was already reinstated"
            raise InvalidValueError(msg)
        return replace(self, reinstated_at=at, reinstatement_movement_id=movement_id)
