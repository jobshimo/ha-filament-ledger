"""The central rule of the system, in one place.

    balance(spool) = Σ signed_amount(movements of spool)

The opening weight is **not** a separate term. It enters the ledger as the spool's first
movement, so the balance is a plain sum with no special case and nothing to keep in sync.

An earlier draft of the specification wrote this as `opening_weight − Σ(movements)`, which
is wrong in a way worth remembering: amounts are already signed, so subtracting a negative
consumption would have made every print increase the balance, and the opening weight would
have been counted twice into the bargain.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..model.movement import Movement, MovementLine
from ..value.grams import Grams


def balance(movements: Iterable[Movement]) -> Grams:
    """Sum a spool's movements. Exact, because the addends are integer milligrams."""
    return Grams(sum(movement.amount.milligrams for movement in movements))


def running_balances(movements: Sequence[Movement]) -> list[MovementLine]:
    """Pair each movement with the balance that stood immediately after it.

    Returned oldest-first. The history view reads it bottom-up as a derivation: opening
    balance, then every gram that left, arriving at the number in the header. Without this
    the immutability of the ledger is cost with no benefit.
    """
    lines: list[MovementLine] = []
    total = Grams.zero()
    for movement in movements:
        total = total + movement.amount
        lines.append(MovementLine(movement=movement, balance_after=total))
    return lines


def consumed(movements: Iterable[Movement]) -> Grams:
    """Total mass that *left* the spool, as a positive quantity.

    Increases are ignored rather than netted off: a reconciliation that adds 6 g does not
    mean 6 g fewer were printed, and confidence is a question about how much has been drawn
    since the last known-good measurement.
    """
    return Grams(
        sum(-movement.amount.milligrams for movement in movements if movement.amount.is_negative)
    )
