"""What kind of ledger entry a movement is, and which way it points."""

from __future__ import annotations

from enum import StrEnum


class Direction(StrEnum):
    INCREASE = "INCREASE"
    DECREASE = "DECREASE"
    EITHER = "EITHER"


class MovementSource(StrEnum):
    """Whether a machine or a person put this entry in the ledger.

    This is a **provenance record for the reader**: the history view labels every row
    *automatic* or *confirmed by you*, which is the difference between a plan the printer
    carried out and a decision somebody made.

    It is deliberately *not* what `ConfidenceEvaluator` reads. Confidence turns on whether
    an entry was an `ESTIMATED_CONSUMPTION` — a question about the movement's **type**, not
    its source. Those two happen to correlate today, and conflating them would break the
    moment they stop: a user-confirmed reconciliation and a user-confirmed estimate carry
    opposite implications for how much a balance can be trusted.
    """

    AUTOMATIC = "AUTOMATIC"
    USER_CONFIRMED = "USER_CONFIRMED"


class MovementType(StrEnum):
    OPENING_BALANCE = "OPENING_BALANCE"
    PRINT_CONSUMPTION = "PRINT_CONSUMPTION"
    PURGE_WASTE = "PURGE_WASTE"
    ESTIMATED_CONSUMPTION = "ESTIMATED_CONSUMPTION"
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"
    RECONCILIATION = "RECONCILIATION"
    DISCARD = "DISCARD"

    @property
    def direction(self) -> Direction:
        return _DIRECTION[self]

    @property
    def requires_approval(self) -> bool:
        """The operational form of "the system never guesses silently".

        Exactly two types enter the ledger unattended, for two different reasons:

        - `PRINT_CONSUMPTION` — the job ran to completion, so the slicer's plan was carried
          out in full. Nothing was inferred.
        - `OPENING_BALANCE` — the user typed the number when registering the spool. Asking
          them to approve it would be asking them to approve themselves.

        Everything else is either inferred, or a correction whose approval *is* the act of
        entering it.
        """
        return self not in (MovementType.PRINT_CONSUMPTION, MovementType.OPENING_BALANCE)

    def permits(self, milligrams: int) -> bool:
        match self.direction:
            case Direction.INCREASE:
                return milligrams > 0
            case Direction.DECREASE:
                return milligrams < 0
            case Direction.EITHER:
                return milligrams != 0


_DIRECTION: dict[MovementType, Direction] = {
    # Always positive: a spool cannot be born owing filament (`opening_weight > 0`).
    MovementType.OPENING_BALANCE: Direction.INCREASE,
    MovementType.PRINT_CONSUMPTION: Direction.DECREASE,
    MovementType.PURGE_WASTE: Direction.DECREASE,
    MovementType.ESTIMATED_CONSUMPTION: Direction.DECREASE,
    MovementType.DISCARD: Direction.DECREASE,
    # A correction and a scale reading can both go either way.
    MovementType.MANUAL_ADJUSTMENT: Direction.EITHER,
    MovementType.RECONCILIATION: Direction.EITHER,
}
