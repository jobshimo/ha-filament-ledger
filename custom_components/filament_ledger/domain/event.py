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
from .value.identifiers import MovementId, PrintJobId, ReviewId, SlotIndex, SpoolId, TagUid
from .value.movement_type import MovementType
from .value.review import ReviewReason, ReviewState


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
class MovementVoided(DomainEvent):
    """An entry left the history the user sees, and the grams came back (docs/14 §14.4.1).

    `returned` is `None` for a void without restitution — the spool was out of inventory,
    nothing was reversed, and the entry still sums into its balance. An automation that
    treats a missing figure as zero would be wrong in exactly the case that matters, so
    the absence travels as an absence.
    """

    movement_id: MovementId
    spool_id: SpoolId
    returned: Grams | None


@dataclass(frozen=True, slots=True)
class MovementReinstated(DomainEvent):
    """A void chapter was closed: the entry is back and the grams went out again."""

    movement_id: MovementId
    spool_id: SpoolId
    deducted: Grams


@dataclass(frozen=True, slots=True)
class MovementReassigned(DomainEvent):
    """A charge moved to the spool that actually fed the print (docs/14 §14.3).

    `amount` is the magnitude that moved, stated positive: it was credited to
    `from_spool_id` and debited from `to_spool_id`, and naming it once with both spools
    beside it is clearer than two signed figures a listener has to pair up.
    """

    movement_id: MovementId
    from_spool_id: SpoolId
    to_spool_id: SpoolId
    amount: Grams


@dataclass(frozen=True, slots=True)
class SpoolDeleted(DomainEvent):
    """A registration was retracted — the spool was never really here (docs/14 §14.4.3)."""

    spool_id: SpoolId
    display_name: str  # always Spool.display_name, which is never None


@dataclass(frozen=True, slots=True)
class SpoolRestored(DomainEvent):
    """A spool came back to inventory, with its history intact.

    Raised by both routes out of retirement: restoring a deleted spool from the Trash, and
    the un-discard that voiding a whole-spool `DISCARD` performs. One fact — *this spool
    counts again* — so one event, rather than two an automation would have to handle
    identically.
    """

    spool_id: SpoolId
    display_name: str  # always Spool.display_name, which is never None


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
class ReviewOpened(DomainEvent):
    """An interrupted job, or unmapped usage, needs attention.

    Carries the job's name so the obvious automation — a notification saying *which*
    print wants a decision — needs no follow-up query.
    """

    review_id: ReviewId
    job_id: PrintJobId
    job_name: str
    reason: ReviewReason


@dataclass(frozen=True, slots=True)
class ReviewResolved(DomainEvent):
    """A review was approved or dismissed. `state` says which — always terminal."""

    review_id: ReviewId
    job_id: PrintJobId
    state: ReviewState


@dataclass(frozen=True, slots=True)
class SpoolDetected(DomainEvent):
    """A recognisable RFID appeared in a slot while `auto_mount_on_rfid` is off.

    Informational — no location changes. Some users keep spools registered to a shelf and
    load them briefly; silently rewriting their locations is not a service. The AMS view
    answers this event with a manual **[ Mount ]** button instead (docs/04-use-cases.md
    UC-02).
    """

    tag_uid: TagUid
    slot: SlotIndex


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
