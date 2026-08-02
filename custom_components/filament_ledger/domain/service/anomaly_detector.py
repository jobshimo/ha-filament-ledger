"""Flags spools whose state is physically implausible.

A negative balance is **permitted, not rejected**. If the ledger says −40 g, the physical
truth is that the opening weight was wrong or a movement was missed. Refusing to record it
would force the system to display a number it knows is false. Recording it and raising an
anomaly tells the user exactly what to do: weigh the spool.

That is the difference between a system that is correct and one that merely looks correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from ..value.grams import Grams
from ..value.identifiers import SpoolId
from ..value.location import Location, is_mounted

DEFAULT_RECONCILIATION_DELTA_RATIO = Decimal("0.15")


class AnomalyKind(StrEnum):
    NEGATIVE_BALANCE = "NEGATIVE_BALANCE"
    DEPLETED_WHILE_LOADED = "DEPLETED_WHILE_LOADED"
    LARGE_RECONCILIATION_DELTA = "LARGE_RECONCILIATION_DELTA"


@dataclass(frozen=True, slots=True)
class Anomaly:
    spool_id: SpoolId
    kind: AnomalyKind
    detail: str


@dataclass(frozen=True, slots=True)
class AnomalyDetector:
    reconciliation_delta_ratio: Decimal = DEFAULT_RECONCILIATION_DELTA_RATIO

    def inspect(
        self,
        *,
        spool_id: SpoolId,
        balance: Grams,
        location: Location,
        is_printing: bool = False,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []

        if balance.is_negative:
            anomalies.append(
                Anomaly(
                    spool_id=spool_id,
                    kind=AnomalyKind.NEGATIVE_BALANCE,
                    detail=(
                        f"balance is {balance.as_decimal} g — the opening weight was wrong "
                        f"or a movement was missed. Weigh this spool."
                    ),
                )
            )

        if balance.is_zero and is_mounted(location) and is_printing:
            anomalies.append(
                Anomaly(
                    spool_id=spool_id,
                    kind=AnomalyKind.DEPLETED_WHILE_LOADED,
                    detail=(
                        f"the ledger says empty, but this spool is loaded in {location} and "
                        f"a print is running. Something is unaccounted for."
                    ),
                )
            )

        return anomalies

    def inspect_reconciliation(
        self,
        *,
        spool_id: SpoolId,
        delta: Grams,
        opening_weight: Grams,
    ) -> Anomaly | None:
        """A large correction means something upstream is systematically wrong.

        The delta is not an embarrassment to be hidden. It is the system's error signal —
        the only honest measure of how wrong the estimates have been.

        The comparison is **inclusive**: a delta of exactly the threshold flags. An anomaly
        is a prompt to look, not an accusation, so the boundary errs toward telling the user.
        """
        if abs(delta).ratio_to(opening_weight) < self.reconciliation_delta_ratio:
            return None
        return Anomaly(
            spool_id=spool_id,
            kind=AnomalyKind.LARGE_RECONCILIATION_DELTA,
            detail=(
                f"the scale disagreed with the ledger by {delta.as_decimal} g, more than "
                f"{self.reconciliation_delta_ratio:%} of the opening weight."
            ),
        )
