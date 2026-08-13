"""A physical reel of filament.

Identity is a generated `SpoolId`, **not** anything the printer reports. The ledger owns
its own identifiers, so a reel keeps one row through every firmware quirk, re-read and
restore.

Two printer-reported identifiers ride along, and the difference between them is the whole
of docs/12-field-notes.md's correction:

- `reel_uid` — Bambu's `tray_uuid`, which names **this reel**. Stable across trays,
  removals and restarts, and the value automatic recognition resolves by.
- `tag_uid` — the UID of the RFID **chip** that was read. A reel's tag is readable from
  either side of its hub, the AMS has two reader boards between four trays, and slots 1
  and 3 read the opposite side from slots 2 and 4 — so one reel answers with two different
  chip UIDs depending on which tray it sits in. Recognising by this was the defect that let
  a reel change tray and come back as a stranger.

Both are optional, and for the same reason: third-party reels, refills and unreadable hubs
carry neither. A reel with no `reel_uid` still resolves by chip, which is why the two live
side by side rather than one replacing the other.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from ..error import (
    InvalidValueError,
    ReelAlreadyIdentifiedError,
    SpoolDeletedError,
    SpoolDiscardedError,
    TagNotEditableError,
)
from ..value.colour import Colour
from ..value.grams import Grams
from ..value.identifiers import (
    PrinterSerial,
    ReelUid,
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
    # Which physical reel this is, as the printer knows it (module docstring). Null for
    # every row written before v2.6 and for every reel with no factory RFID; `DetectSpool`
    # adopts it onto a legacy row the first time the printer reads that reel again.
    #
    # Deliberately **not** paired with a source column the way `tag_uid` is. A reel id can
    # only ever come from the printer — there is no dialog that asks the user to type a
    # `tray_uuid`, because a hand-typed reel id is a claim nobody can check and the tag
    # dialog already exists for the case a user wants to assert identity by hand.
    reel_uid: ReelUid | None = None
    discarded_at: datetime | None = None
    # The registration, retracted (docs/14 §14.4.3). Stored separately from
    # `discarded_at` on purpose: a discard is a real-world event that counts as waste,
    # a deletion is a bookkeeping statement that counts as nothing, anywhere — and only
    # one of the two is meant to come back.
    deleted_at: datetime | None = None
    # Why, when it was not a person who decided. Set only by the automatic retirement of a
    # phantom row (see `deleted`); `None` for every deletion a user performed, and cleared
    # again on restore.
    deleted_reason: str | None = None

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

    def deleted(self, at: datetime, reason: str | None = None) -> Spool:
        """Retract the registration: the spool was never really here (docs/14 §14.4.3).

        **Frees the slot in the same breath.** Location is cleared to storage because a
        spool that was never here cannot be occupying a tray, and because the partial
        unique indexes now ignore deleted rows — leaving the slot recorded would keep a
        ghost in an AMS position no index is watching any more.

        Writes no movement. Deletion is a location-and-state change, and UC-03's strict
        separation of location change from quantity change extends to it: the grams are
        not consumed, they simply stop being counted, and the spool's whole history comes
        back the moment it is restored.

        `reason` is for the deletions **nobody asked for**: the phantom rows v2.6 retires
        once it can see that two rows were one reel all along. A user deleting a spool
        supplies none, because the Trash entry is already their own act and needs no
        caption. A row that retired itself owes the user a sentence.
        """
        self._guard_in_inventory()
        return replace(self, location=Storage(), deleted_at=at, deleted_reason=reason)

    def restored(self) -> Spool:
        """Bring a deleted spool back — and its history with it.

        The old slot is *not* reclaimed. It was freed on delete and something else may be
        in it; silently displacing that spool would be the ledger making a physical claim
        it has no way to check. The spool returns to storage, where the user puts it back.
        """
        if not self.is_deleted:
            msg = f"spool {self.id} is not deleted, so there is nothing to restore"
            raise InvalidValueError(msg)
        # The caption goes with the deletion it explained. A restored spool is in inventory
        # again on the user's own say-so, and a row still carrying "retired by the upgrade"
        # would be describing a state it is no longer in.
        return replace(self, deleted_at=None, deleted_reason=None)

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

    def identified_as(self, reel: ReelUid) -> Spool:
        """Learn which physical reel this row is — the healing step for a legacy row.

        Every row written before v2.6 has no `reel_uid`, because the ledger never stored
        the field. Rather than guess one in a migration, the row learns it the first time
        the printer reads that reel again: the chip still resolves to this spool, and the
        reading carries the reel id the chip belongs to.

        **Write-once, and never re-written.** A row that already names a reel refuses a
        different one instead of quietly re-pointing: a chip resolving to a spool that
        claims another reel means either two reels genuinely share a chip UID or a hub was
        swapped between reels, and both of those are situations where overwriting turns a
        visible contradiction into an invisible one. The caller — `DetectSpool` — treats
        the refusal as *this is not the reel I am looking at* and falls through to the
        unknown-reel path, which registers rather than corrupts.

        Idempotent for the reel it already names, because the detection path re-observes
        the same reel on every republish and must not care.
        """
        self._guard_in_inventory()
        if self.reel_uid is not None and self.reel_uid != reel:
            msg = (
                f"{self.display_name} is already reel {self.reel_uid} and cannot be "
                f"re-identified as {reel}"
            )
            raise ReelAlreadyIdentifiedError(msg)
        return replace(self, reel_uid=reel)


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
    reel_uid: ReelUid | None = None,
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
        # No default and no inference: a reel id is either what the printer said or absent.
        # Unlike `tag_source` there is nothing sensible to fall back to — a reel identity
        # the ledger made up would be indistinguishable from one the machine reported.
        reel_uid=reel_uid,
    )
