"""UC-02 · MountSpool and UC-03 · UnmountSpool, automatic paths.

The printer gateway reports what one tray looks like now; this use case reconciles the
ledger with that observation. It refuses in one direction on purpose (docs/04-use-cases.md
UC-02): an ambiguous tag never picks a candidate, because choosing wrong means every
subsequent print deducts from a spool sitting on a shelf.

An unknown tag is refused more narrowly than it used to be. When the reading carries the
full Bambu payload — a material string *and* a colour — and `auto_register_on_detect` is
on, the spool registers itself with the configured defaults and then mounts through the
ordinary path: nothing there is a guess, because the opening weight is the default the
user already stated and everything else is what the RFID said. A reading missing either
hint still only reports, because a spool the system cannot describe is a spool it must
not invent.

**Nothing here records a movement itself.** Both paths only move spools; moving a spool
consumes no filament. (Auto-registration delegates to `RegisterSpool`, whose opening
balance is UC-01's own rule, not this use case's.)

An occupied tray whose tag is unreadable changes nothing. The use cases in
docs/04-use-cases.md authorise automatic action on a tag appearing (UC-02) or the tray
emptying (UC-03) — an unreadable tag is neither. It identifies no spool to mount, and the
spool the ledger has in that tray may well be the untagged one the user mounted by hand;
unmounting it on silence would fight every manual mount of a third-party spool.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..domain.error import DuplicateTagNotConfirmedError
from ..domain.event import (
    AmbiguousTagDetected,
    DomainEvent,
    EventPublisher,
    SpoolDetected,
    SpoolMounted,
    SpoolUnmounted,
    UnknownSpoolDetected,
)
from ..domain.port.repositories import SpoolRepository
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.value.grams import Grams
from ..domain.value.identifiers import TagSource, TagUid, TrayRef
from ..domain.value.location import AmsSlot, Storage
from ..domain.value.material import Material
from ..domain.value.movement_type import MovementSource
from ..domain.value.tray_reading import TrayReading
from .move_spool import displace_and_mount
from .register_spool import RegisterSpool, RegisterSpoolCommand

LOGGER = logging.getLogger(__name__)

#: What auto-registration writes for the maker, because the payload that triggers it —
#: a tag plus name, material and colour — is what a Bambu tray reports about Bambu's own
#: filament. A third-party reel carries no such tag, so it never reaches this path.
BAMBU_VENDOR = "Bambu Lab"


@dataclass(frozen=True, slots=True)
class DetectSpool:
    """One tray change, resolved against the inventory.

    Idempotent by design: the gateway replays `current_trays()` on startup, so the same
    reading arrives more than once and must change nothing the second time — including
    registering nothing twice, which the unknown-tag check below guarantees.
    """

    spools: SpoolRepository
    events: EventPublisher
    uow: UnitOfWork
    # A plain value, not a callable: changing options reloads the config entry (see
    # `_reload_on_options_change` in the package root), which rebuilds every use case with
    # fresh settings — so this can never go stale.
    auto_mount: bool
    # UC-01 as a collaborator, invoked *outside* this use case's unit of work: it opens
    # its own, and nesting the two would deadlock the connection on itself.
    register_spool: RegisterSpool
    # The configured defaults, resolved by the composition root from the same settings the
    # register form reads. Plain values for the reason `auto_mount` is one.
    default_opening_weight: Grams
    default_core_weight: Grams
    auto_register: bool

    async def execute(self, reading: TrayReading) -> None:
        if reading.empty:
            await self._tray_emptied(reading.tray)
            return
        if reading.tag is None:
            return  # unreadable tag: nothing automatic is possible — see the module docstring
        # Before the mount/report decision, so a fully described unknown spool enters the
        # inventory whichever way `auto_mount` points: with it on the resolution below
        # mounts what was just registered, with it off the sighting is reported and the
        # AMS view offers the manual [ Mount ] button for a spool that now exists.
        await self._register_unknown(reading)
        if not self.auto_mount:
            # Informational, and deliberately unresolved: the user asked the system not to
            # move spools, so it reports the sighting and the AMS view offers a manual
            # [ Mount ] button instead.
            await self.events.publish(SpoolDetected(tag_uid=reading.tag, tray=reading.tray))
            return
        await self._tag_appeared(reading.tag, reading.tray)

    async def _tray_emptied(self, tray: TrayRef) -> None:
        """UC-03, automatic: the spool left the machine, so it is in storage now.

        No occupant, no work — the replayed reading of an empty tray must not invent an
        unmount for a spool that was never there.
        """
        async with self.uow:
            occupant = await self.spools.find_by_location(AmsSlot(tray))
            if occupant is not None:
                await self.spools.save(occupant.unmounted())
        # Published after the commit — never for a write that could still roll back.
        if occupant is not None:
            await self.events.publish(SpoolUnmounted(spool_id=occupant.id))

    async def _register_unknown(self, reading: TrayReading) -> None:
        """UC-01 on the printer's say-so, when the reading describes the spool in full.

        Registered into storage, never straight into the slot: the ordinary mount path
        below owns displacement and the one-spool-per-tray index, and giving registration
        a second way into a tray would give that rule a second implementation.

        The idempotence hinge is the *location-aware* lookup: a replayed reading finds
        the spool it registered last time in this tray or in storage and does nothing,
        while a match mounted only in other trays is a second reel of the same batch —
        a Bambu tag identifies a batch, not a unit. The lookup runs *outside* this use case's
        unit of work because `RegisterSpool` opens its own — one connection cannot hold
        both — which leaves a window where someone else registers the same tag first.
        `RegisterSpool`'s duplicate-tag guard closes it, and losing that race is not a
        failure: the spool exists, which is all this method wants.
        """
        if not self.auto_register or reading.tag is None:
            return
        if reading.material is None or reading.colour is None:
            return  # a spool the system cannot describe is a spool it must not invent
        candidates = await self.spools.find_by_tag(reading.tag)
        if any(spool.location in (AmsSlot(reading.tray), Storage()) for spool in candidates):
            # A replay (already in this tray) or an ordinary move (waiting in storage):
            # the resolution below handles both, and registering would mint a twin.
            return
        try:
            await self.register_spool.execute(
                RegisterSpoolCommand(
                    material=Material.from_printer(reading.material),
                    colour=reading.colour,
                    opening_weight=self.default_opening_weight,
                    core_weight=self.default_core_weight,
                    vendor=BAMBU_VENDOR,
                    label=reading.name,
                    tag_uid=reading.tag,
                    # The serial came off the tray reading, not off a keyboard
                    # (docs/14 §14.2) — the same provenance the register-from-sync
                    # dialog states.
                    tag_source=TagSource.DETECTED,
                    # The opening balance is the configured default nobody confirmed
                    # today, and the history's provenance label must say so.
                    movement_source=MovementSource.AUTOMATIC,
                    # Candidates mounted only in *other* trays mean this reading is a
                    # second reel of the batch, so the duplicate is deliberate — the
                    # resolution then sees both and asks (`AmbiguousTagDetected`)
                    # instead of merging two physical reels into one ledger row.
                    confirm_duplicate_tag=bool(candidates),
                )
            )
        except DuplicateTagNotConfirmedError:
            # The race above, lost: startup replay and a live tray event both saw the
            # unknown tag before either registered it. The spool exists now, so the
            # resolution below finds it — proceeding is the correct outcome, not a shrug.
            LOGGER.debug(
                "tag %s was registered concurrently; using the existing spool", reading.tag
            )
        except Exception:
            # A failed registration must not swallow the sighting: fall through, so the
            # resolution below still reports the unknown tag or mounts what does exist —
            # the gateway logs and drops readings, so nothing upstream would retry this.
            LOGGER.exception("auto-registration of tag %s failed", reading.tag)

    async def _tag_appeared(self, tag: TagUid, tray: TrayRef) -> None:
        """UC-02, automatic: resolve the tag, then mount — or ask.

        The whole read-compute-write runs inside one unit of work: resolution and
        displacement must see the same inventory, or a concurrent mount could interleave
        between the lookup and the write.
        """
        to_publish: list[DomainEvent] = []
        async with self.uow:
            # `find_by_tag` already excludes discarded spools: a discarded spool is out of
            # inventory, and a tag matching only discarded spools is an unknown tag.
            candidates = await self.spools.find_by_tag(tag)
            if not candidates:
                to_publish.append(UnknownSpoolDetected(tag_uid=tag, tray=tray))
            elif len(candidates) > 1:
                to_publish.append(
                    AmbiguousTagDetected(
                        tag_uid=tag,
                        tray=tray,
                        candidates=tuple(spool.id for spool in candidates),
                    )
                )
            elif (spool := candidates[0]).location != AmsSlot(tray):
                displaced = await displace_and_mount(self.spools, spool, tray)
                if displaced is not None:
                    to_publish.append(SpoolUnmounted(spool_id=displaced))
                to_publish.append(SpoolMounted(spool_id=spool.id, tray=tray))
            # Already mounted here: the replayed reading confirms the ledger. No write,
            # no event — announcing a mount that did not happen would be a lie.
        for event in to_publish:
            await self.events.publish(event)
