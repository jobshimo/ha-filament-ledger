"""A physical reel of filament.

Identity is a generated `SpoolId`, **not** the RFID tag. A Bambu tag identifies a product
batch rather than a physical unit, so two identical black PLA spools can carry the same
payload; using it as identity would silently merge two spools into one and corrupt both
balances.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from ..error import (
    InvalidValueError,
    SpoolDeletedError,
    SpoolDiscardedError,
    TagNotEditableError,
)
from ..value.colour import Colour
from ..value.grams import Grams
from ..value.identifiers import (
    PrinterSerial,
    SpoolId,
    TagSource,
    TagUid,
    TrayRef,
    new_spool_id,
)
from ..value.location import AmsSlot, ExternalSpool, Location, Storage
from ..value.material import Material
from ..value.percentage import Percentage
from ..value.spool_state import SpoolState


@dataclass(frozen=True, slots=True)
class Spool:
    """Immutable in shape; every change returns a new instance.

    Frozen because a spool is only ever mutated through an application use case that also
    writes to the repository, and an entity that cannot be half-updated in memory is one
    fewer thing to reason about.
    """

    id: SpoolId
    material: Material
    colour: Colour
    opening_weight: Grams
    core_weight: Grams
    location: Location
    registered_at: datetime
    vendor: str | None = None
    label: str | None = None
    tag_uid: TagUid | None = None
    tag_source: TagSource | None = None
    discarded_at: datetime | None = None
    # The registration, retracted (docs/14 §14.4.3). Stored separately from
    # `discarded_at` on purpose: a discard is a real-world event that counts as waste,
    # a deletion is a bookkeeping statement that counts as nothing, anywhere — and only
    # one of the two is meant to come back.
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.opening_weight.is_positive:
            msg = f"opening_weight must be > 0, got {self.opening_weight.as_decimal} g"
            raise InvalidValueError(msg)
        if self.core_weight.is_negative:
            msg = f"core_weight must be >= 0, got {self.core_weight.as_decimal} g"
            raise InvalidValueError(msg)
        # SQLite's `ADD COLUMN` cannot carry a cross-column CHECK, so migration 0003's
        # column check covers only the value set and this is where the pairing is
        # enforced (docs/14 §14.2). A tag with no provenance is the state the column
        # exists to end; a provenance with no tag describes nothing.
        if (self.tag_uid is None) != (self.tag_source is None):
            msg = (
                f"tag_uid and tag_source are set together or not at all, "
                f"got tag_uid={self.tag_uid}, tag_source={self.tag_source}"
            )
            raise InvalidValueError(msg)

    # -- derived -----------------------------------------------------------------------

    @property
    def is_discarded(self) -> bool:
        return self.discarded_at is not None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_in_inventory(self) -> bool:
        """Neither thrown away nor retracted — the only state grams can return to.

        The one question three separate rules ask (docs/14 §14.3, §14.4.1, §14.4.2), so
        it is asked in one place: a reassignment target, a restitution and a
        reinstatement all require it, and each for the same reason — a balance change on
        a retired spool is a balance change nobody can see.
        """
        return not self.is_discarded and not self.is_deleted

    @property
    def is_tag_editable(self) -> bool:
        """Everything except a tag the printer attached (docs/14 §14.2).

        A spool with no tag is editable — it can be given one, and the tag it is given is
        MANUAL by definition.
        """
        return self.tag_source is not TagSource.DETECTED

    def state(self, *, balance: Grams, movement_count: int) -> SpoolState:
        """The lifecycle state. Derived — see `SpoolState.derive`."""
        return SpoolState.derive(
            discarded_at=self.discarded_at,
            balance=balance,
            movement_count=movement_count,
            deleted_at=self.deleted_at,
        )

    def remaining_percentage(self, balance: Grams) -> Percentage:
        return Percentage.from_ratio(balance.ratio_to(self.opening_weight))

    def net_from_gross(self, gross: Grams) -> Grams:
        """Subtract the core so a scale reading becomes filament mass.

        This exists because reconciliation is done with a kitchen scale, and a scale weighs
        the whole spool. Without it the user does arithmetic the system should be doing.
        """
        return gross - self.core_weight

    @property
    def display_name(self) -> str:
        if self.label:
            return self.label
        vendor = f"{self.vendor} " if self.vendor else ""
        return f"{vendor}{self.material.display_name}"

    # -- transitions -------------------------------------------------------------------

    def _guard_not_discarded(self) -> None:
        if self.is_discarded:
            msg = f"spool {self.id} was discarded on {self.discarded_at:%Y-%m-%d}"
            raise SpoolDiscardedError(msg)

    def _guard_in_inventory(self) -> None:
        """Refuse every ordinary transition on a retired spool, by either route.

        Deletion has to guard as tightly as discarding does, and one guard short is the
        whole bug: the partial unique indexes learned to ignore deleted spools
        (migration 0003), so a deleted spool mounted into a slot would sit there
        *alongside* whatever the index still sees — two spools in slot 1, and the
        invariant that says otherwise looking away.
        """
        self._guard_not_discarded()
        if self.is_deleted:
            msg = (
                f"spool {self.id} was deleted on {self.deleted_at:%Y-%m-%d} — "
                f"restore it from the trash first"
            )
            raise SpoolDeletedError(msg)

    def moved_to(self, location: Location) -> Spool:
        """Change location. Records no movement — moving a spool consumes no filament.

        Keeping *location change* and *quantity change* strictly separate is how an
        inventory system avoids starting to lie.
        """
        self._guard_in_inventory()
        return replace(self, location=location)

    def mounted_in(self, tray: TrayRef) -> Spool:
        return self.moved_to(AmsSlot(tray))

    def mounted_externally(self, printer: PrinterSerial) -> Spool:
        """On the direct feed of the machine named — one feed per printer (docs/02 §2.2)."""
        return self.moved_to(ExternalSpool(printer))

    def unmounted(self) -> Spool:
        return self.moved_to(Storage())

    def discarded(self, at: datetime) -> Spool:
        """Thrown away. Retained in full, with its history intact, but out of active stock.

        Counts as waste in every statistic, which is the difference between this and
        `deleted` below — the two answers to the intent modal's one question, *did you
        throw it away, or was it registered by mistake?* (docs/14 §14.4.3).
        """
        self._guard_in_inventory()
        return replace(self, location=Storage(), discarded_at=at)

    def deleted(self, at: datetime) -> Spool:
        """Retract the registration: the spool was never really here (docs/14 §14.4.3).

        **Frees the slot in the same breath.** Location is cleared to storage because a
        spool that was never here cannot be occupying a tray, and because the partial
        unique indexes now ignore deleted rows — leaving the slot recorded would keep a
        ghost in an AMS position no index is watching any more.

        Writes no movement. Deletion is a location-and-state change, and UC-03's strict
        separation of location change from quantity change extends to it: the grams are
        not consumed, they simply stop being counted, and the spool's whole history comes
        back the moment it is restored.
        """
        self._guard_in_inventory()
        return replace(self, location=Storage(), deleted_at=at)

    def restored(self) -> Spool:
        """Bring a deleted spool back — and its history with it.

        The old slot is *not* reclaimed. It was freed on delete and something else may be
        in it; silently displacing that spool would be the ledger making a physical claim
        it has no way to check. The spool returns to storage, where the user puts it back.
        """
        if not self.is_deleted:
            msg = f"spool {self.id} is not deleted, so there is nothing to restore"
            raise InvalidValueError(msg)
        return replace(self, deleted_at=None)

    def restored_from_discard(self) -> Spool:
        """The un-discard, used only by the void of a whole-spool `DISCARD`.

        Not an operation of its own and not offered anywhere: voiding the discard entry
        returns the entire balance, and leaving the spool `DISCARDED` would strand those
        grams outside inventory. The void of the discard *is* the restore — one recorded
        operation, not two (docs/14 §14.4.1).
        """
        if not self.is_discarded:
            msg = f"spool {self.id} is not discarded, so there is no discard to undo"
            raise InvalidValueError(msg)
        return replace(self, discarded_at=None)

    def with_details(
        self,
        *,
        label: str | None = None,
        vendor: str | None = None,
        colour: Colour | None = None,
        material: Material | None = None,
        core_weight: Grams | None = None,
    ) -> Spool:
        """Edit metadata. **Never the balance** — that requires a movement, and that is the
        whole design."""
        self._guard_in_inventory()
        return replace(
            self,
            label=label if label is not None else self.label,
            vendor=vendor if vendor is not None else self.vendor,
            colour=colour if colour is not None else self.colour,
            material=material if material is not None else self.material,
            core_weight=core_weight if core_weight is not None else self.core_weight,
        )

    def with_tag(self, tag: TagUid | None, source: TagSource | None) -> Spool:
        """Attach, replace or clear the RFID tag.

        A **separate** transition rather than another `with_details` parameter, because
        there `None` means "leave unchanged" and clearing a tag needs `None` to mean
        *cleared*. Overloading one method with two meanings of `None` is how the next
        defect gets written (docs/14 §14.2).

        Refuses to touch a `DETECTED` tag: the printer read it off the tray, and a ledger
        tag that no longer matches the reel in the machine mounts the wrong spool on the
        next sync. The guard lives here rather than only in the use case so that no future
        caller can route around it.
        """
        self._guard_in_inventory()
        if not self.is_tag_editable:
            msg = (
                f"tag {self.tag_uid} on {self.display_name} was attached by the printer "
                f"and cannot be edited here"
            )
            raise TagNotEditableError(msg)
        if (tag is None) != (source is None):
            msg = "a tag and its provenance are set together or cleared together"
            raise InvalidValueError(msg)
        return replace(self, tag_uid=tag, tag_source=source)


def register(
    *,
    material: Material,
    colour: Colour,
    opening_weight: Grams,
    core_weight: Grams,
    registered_at: datetime,
    location: Location | None = None,
    vendor: str | None = None,
    label: str | None = None,
    tag_uid: TagUid | None = None,
    tag_source: TagSource | None = None,
) -> Spool:
    """Build a new spool, generating its identity.

    `core_weight` is mandatory and has no fallback here. The configured per-vendor default
    is resolved by the application layer, above the domain: a silent zero would report every
    reconciliation as roughly 250 g heavier than reality, forever, and the error would look
    like drift rather than like a bug.

    `tag_source` is the one field that *does* default, and only when a tag is supplied
    without one: unstated provenance is MANUAL. That is migration 0003's argument applied
    to new rows — MANUAL over-grants edit rights, DETECTED would invent a printer reading
    nobody made. A provenance supplied with no tag is left to fail the pairing check
    rather than silently dropped, because it means the caller believes something untrue.
    """
    return Spool(
        id=new_spool_id(),
        material=material,
        colour=colour,
        opening_weight=opening_weight,
        core_weight=core_weight,
        location=location if location is not None else Storage(),
        registered_at=registered_at,
        vendor=vendor,
        label=label,
        tag_uid=tag_uid,
        tag_source=((tag_source or TagSource.MANUAL) if tag_uid is not None else tag_source),
    )
