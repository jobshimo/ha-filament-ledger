"""Identity types.

All of these are strings underneath. They are distinct types so that passing a `ReviewId`
where a `SpoolId` belongs is a type error rather than a silent lookup miss — which is the
entire argument for `mypy --strict` in docs/11-development.md §11.3.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import NewType

from ..error import InvalidValueError

SpoolId = NewType("SpoolId", str)
MovementId = NewType("MovementId", str)
PrintJobId = NewType("PrintJobId", str)
ReviewId = NewType("ReviewId", str)

MIN_AMS_SLOT = 1
MAX_AMS_SLOT = 4

# An AMS unit is numbered from one by the machine itself; there is no AMS 0. No upper bound
# is stated, because how many units a printer can carry is the machine's business and a
# ceiling invented here would refuse a real tray on a machine nobody has tested yet.
MIN_AMS_INDEX = 1


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


@dataclass(frozen=True, order=True, slots=True)
class PrinterSerial:
    """Which machine — the serial the printer reports about itself.

    A value object rather than a bare `str` for the reason every identity here is one: it
    is a dictionary key and a component of `TrayRef`, and a `SpoolId` landing in that
    position must be a type error rather than a tray that matches nothing.

    Ordered, because `TrayRef` is ordered and a reference cannot sort without its first
    component sorting. Blank is refused: a serial that names no machine is the one value
    that could never be resolved back to a printer.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            msg = "PrinterSerial cannot be blank"
            raise InvalidValueError(msg)

    def __str__(self) -> str:
        return self.value


# The printer a ledger has always talked to but never recorded the serial of. Written by
# migration 0007 into every row that predates the three-part reference, and by the panel
# when no printer has been discovered at all.
#
# **Accepted rather than refused, which is the opposite of `ABSENT_TAG_SENTINEL` below,
# and the difference is the point.** Sixteen zeros denotes the *absence* of a tag, so
# treating it as an identity would merge every untagged spool into one. This sentinel
# denotes a real machine whose name is unknown — a single-printer ledger has exactly one
# printer, so every row carrying it belongs to the same one, and the uniqueness of a tray
# holds under it exactly as it holds under a real serial. It is a name, not a gap.
#
# **That argument is why it names at most one live machine.** It is sound only while there
# is one machine for it to mean, so discovery hands it to a printer whose serial it could
# not read only when that printer is the only one there is; with several, an unnamed machine
# is not followed, because two machines answering to one name is precisely the merge this
# sentinel is safe from being (`bambu_gateway._resolve_names`).
#
# Public because the whole boundary shares the fact: the migration writes it, the gateway
# replaces it once discovery resolves a real serial, and the panel falls back to it while
# there is no printer to ask.
UNIDENTIFIED_PRINTER = PrinterSerial("UNIDENTIFIED")


@dataclass(frozen=True, order=True, slots=True)
class AmsIndex:
    """Which AMS unit, numbered as the printer numbers it — `AMS 1 Tray 4` is index 1.

    Distinct from the AMS unit's own serial, which the entity registry carries and which
    nothing here needs: the ordinal is what the printer reports its trays under, and it is
    the half of the reference a user can point at on the machine.
    """

    value: int

    def __post_init__(self) -> None:
        if self.value < MIN_AMS_INDEX:
            msg = f"AMS index must be >= {MIN_AMS_INDEX}, got {self.value}"
            raise InvalidValueError(msg)

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, order=True, slots=True)
class TrayRef:
    """Which tray, on which AMS, on which printer — what physically identifies a tray.

    A bare `SlotIndex` was a correct v1 decision and is no longer a true one: two printers
    both have a tray 1, and so do two AMS units on one printer. Every surface that used to
    name a slot names one of these instead — the location a spool is mounted at, the key
    of a job's per-tray usage, the key of a review's per-tray estimate — because a figure
    keyed by an ambiguous name is a figure that lands on the wrong spool the first time a
    second machine appears.

    Ordered, so that every reader — the deduction loop, the review card, the persisted
    JSON — sees one canonical tray order without each imposing its own. Ordering by printer
    first is what makes a listing of several machines' trays group itself.

    **Supported since v2.0.** The gateway resolves every machine the registry describes and
    keys each one's trays under its own serial (docs/05 §5.8), so two printers' trays live
    in one mapping without colliding and nothing has to decide which machine a tray 1 is on.
    """

    printer: PrinterSerial
    ams: AmsIndex
    slot: SlotIndex

    def __str__(self) -> str:
        return f"AMS {self.ams} tray {self.slot} on printer {self.printer}"


# What the printer reports for a tray holding a spool with no readable tag. Sixteen zeros
# is a sentinel for "nothing was read", never a serial — see docs/12-field-notes.md.
# Public because the whole boundary shares the fact: the gateway translates it to `None`
# on the way in, and hydration tolerates it in rows saved before `TagUid` refused it.
ABSENT_TAG_SENTINEL = "0000000000000000"


@dataclass(frozen=True, slots=True)
class TagUid:
    """The RFID serial read from a spool.

    Optional on a `Spool`: third-party and refilled spools have none. Crucially it is
    *not* identity — a Bambu tag identifies a product batch, so two physical spools can
    carry the same payload. See docs/02-domain-model.md §2.3.

    Sixteen zeros is refused outright. The printer reports `0000000000000000` for a tray
    whose spool has no readable tag, so it denotes *absence*, not identity — matching on it
    would merge every untagged spool the owner ever buys into one. The gateway translates
    the sentinel to `None` at the boundary; this check is the backstop that makes the bug
    unrepresentable rather than merely unlikely.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            msg = "TagUid cannot be blank"
            raise InvalidValueError(msg)
        if self.value == ABSENT_TAG_SENTINEL:
            msg = (
                f"TagUid {ABSENT_TAG_SENTINEL!r} denotes an absent tag, not an identity; "
                f"represent it as None"
            )
            raise InvalidValueError(msg)

    def __str__(self) -> str:
        return self.value


class TagSource(StrEnum):
    """Who attached the tag — the owner's rule, in two values (docs/14 §14.2).

    > A tag the printer attached is the printer's statement. A tag I typed is mine to
    > change.

    `DETECTED` is only ever written by the register-from-sync path, where the serial came
    off a tray reading; it makes the tag read-only, so the ledger's tag never drifts from
    the physical spool. `MANUAL` is everything else, including every tag that predates
    this column: migration 0003 backfills them all as MANUAL because provenance was never
    recorded, and claiming DETECTED for a tag whose origin nobody knows would be invented
    history. It over-grants edit rights once rather than storing a lie forever.

    Paired with `TagUid` on a `Spool`: both set, or both `None`. A provenance with no tag
    describes nothing, and a tag with no provenance is the gap this enum exists to close.
    """

    MANUAL = "MANUAL"
    DETECTED = "DETECTED"
