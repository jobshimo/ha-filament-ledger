"""Domain events.

Raised by the domain, translated to Home Assistant events by infrastructure. The domain does
not know Home Assistant consumes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .service.anomaly_detector import Anomaly
from .value.confidence import Confidence
from .value.grams import Grams
from .value.identifiers import SlotIndex, SpoolId, TagUid
from .value.movement_type import MovementType


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Marker base. Subclasses carry only data — no behaviour, no framework types."""


@dataclass(frozen=True, slots=True)
class SpoolRegistered(DomainEvent):
    spool_id: SpoolId
    display_name: str  # always Spool.display_name, which is never None


@dataclass(frozen=True, slots=True)
class SpoolMounted(DomainEvent):
    spool_id: SpoolId
    slot: SlotIndex


@dataclass(frozen=True, slots=True)
class SpoolUnmounted(DomainEvent):
    spool_id: SpoolId


@dataclass(frozen=True, slots=True)
class MovementRecorded(DomainEvent):
    spool_id: SpoolId
    movement_type: MovementType
    amount: Grams
    new_balance: Grams


@dataclass(frozen=True, slots=True)
class SpoolDepleted(DomainEvent):
    spool_id: SpoolId
    display_name: str  # always Spool.display_name, which is never None


@dataclass(frozen=True, slots=True)
class ConfidenceDegraded(DomainEvent):
    spool_id: SpoolId
    previous: Confidence
    current: Confidence


@dataclass(frozen=True, slots=True)
class AnomalyDetected(DomainEvent):
    anomaly: Anomaly


@dataclass(frozen=True, slots=True)
class UnknownSpoolDetected(DomainEvent):
    """An unrecognised RFID appeared in a slot.

    The system **does not auto-create a spool**. A guessed opening weight is a fabricated
    number, and a fabricated number in a ledger is worse than a missing one — it looks
    authoritative.
    """

    tag_uid: TagUid
    slot: SlotIndex


@dataclass(frozen=True, slots=True)
class AmbiguousTagDetected(DomainEvent):
    """A recognised RFID resolved to more than one non-discarded spool, which is legal.

    The system does not pick the newest, the fullest, or the first. It names the candidates
    and asks, because choosing wrong means every subsequent print deducts from a spool
    sitting on a shelf while the one in the machine runs out unannounced.
    """

    tag_uid: TagUid
    slot: SlotIndex
    candidates: tuple[SpoolId, ...]


class EventPublisher(Protocol):
    """Where domain events go.

    A port, so the application layer can emit without knowing whether the other end is a
    Home Assistant bus, a test double, or nothing at all.
    """

    async def publish(self, event: DomainEvent) -> None: ...
