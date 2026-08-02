"""Identity types.

All of these are strings underneath. They are distinct types so that passing a `ReviewId`
where a `SpoolId` belongs is a type error rather than a silent lookup miss — which is the
entire argument for `mypy --strict` in docs/11-development.md §11.3.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import NewType

from ..error import InvalidValueError

SpoolId = NewType("SpoolId", str)
MovementId = NewType("MovementId", str)
PrintJobId = NewType("PrintJobId", str)
ReviewId = NewType("ReviewId", str)

MIN_AMS_SLOT = 1
MAX_AMS_SLOT = 4


def new_spool_id() -> SpoolId:
    return SpoolId(str(uuid.uuid4()))


def new_movement_id() -> MovementId:
    return MovementId(str(uuid.uuid4()))


def new_print_job_id() -> PrintJobId:
    return PrintJobId(str(uuid.uuid4()))


def new_review_id() -> ReviewId:
    return ReviewId(str(uuid.uuid4()))


@dataclass(frozen=True, order=True, slots=True)
class SlotIndex:
    """An AMS tray position, 1..4.

    A value object rather than a bare `int` because it is used as a dictionary key in
    `PendingReview` (docs/02-domain-model.md §2.3), and because slot 0 and slot 9 are not
    things that exist.
    """

    value: int

    def __post_init__(self) -> None:
        if not MIN_AMS_SLOT <= self.value <= MAX_AMS_SLOT:
            msg = f"AMS slot must be {MIN_AMS_SLOT}..{MAX_AMS_SLOT}, got {self.value}"
            raise InvalidValueError(msg)

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class TagUid:
    """The RFID serial read from a spool.

    Optional on a `Spool`: third-party and refilled spools have none. Crucially it is
    *not* identity — a Bambu tag identifies a product batch, so two physical spools can
    carry the same payload. See docs/02-domain-model.md §2.3.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            msg = "TagUid cannot be blank"
            raise InvalidValueError(msg)

    def __str__(self) -> str:
        return self.value
