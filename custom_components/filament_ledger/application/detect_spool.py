"""UC-02 · MountSpool and UC-03 · UnmountSpool, automatic paths.

The printer gateway reports what one tray looks like now; this use case reconciles the
ledger with that observation. It refuses in one direction on purpose (docs/04-use-cases.md
UC-02): an ambiguous tag never picks a candidate, because choosing wrong means every
subsequent print deducts from a spool sitting on a shelf.

An unknown tag is refused more narrowly than it used to be. When the reading carries the
full Bambu payload — a material string *and* a colour — and `auto_register_on_detect` is
on, the spool registers itself and then mounts through the ordinary path: nothing there
is a guess, because everything is what the RFID said — the opening weight is the reel's
own `tray_weight`, falling back to the configured default for a tag that declines to
give one. A reading missing either hint still only reports, because a spool the system
cannot describe is a spool it must not invent.

**That opening weight is what the reel held new, and a reel is not always new.** Meeting
a half-used spool for the first time therefore opens it overstated — exactly as the
configured default always has, and no worse. Nothing here invents a correction for it:
the printer has no scale and `remain` is useless on this hardware (docs/12), so any
compensation would be a fabricated number. The confidence badge and the *needs weighing*
prompt are the honest answer, and they already cover this spool from its first entry.

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

from ..domain.error import DomainError, DuplicateTagNotConfirmedError
from ..domain.event import (
    AmbiguousTagDetected,
    DomainEvent,
    EventPublisher,
    SpoolDeleted,
    SpoolDetected,
    SpoolMounted,
    SpoolUnmounted,
    UnknownSpoolDetected,
)
from ..domain.model.spool import Spool
from ..domain.port.clock import Clock
from ..domain.port.repositories import SpoolRepository
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.value.colour_name import label_with_colour
from ..domain.value.grams import Grams
from ..domain.value.identifiers import TagSource, TagUid, TrayRef
from ..domain.value.location import AmsSlot
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

#: The caption on a row this release retires by itself, in the instance's language.
#:
#: Every word of it is aimed at the one person who will ever read it: somebody who opened the
#: Trash and found a spool there they did not put there. It says what happened, it names the
#: row that survived so they can check, and it says the undo out loud — because a deletion
#: nobody asked for is only defensible if the person it happened to can reverse it without
#: asking anyone how.
_RETIRED_BY_UPGRADE: dict[str, str] = {
    "en": (
        "Retired automatically when this ledger learned to recognise reels by their own "
        "identity instead of by the RFID chip the AMS happened to read. This row and "
        "{survivor} were always one physical reel, met from its two sides. Restore it from "
        "the Trash if that is wrong."
    ),
    "es": (
        "Retirada automáticamente al aprender este inventario a reconocer las bobinas por su "
        "identidad propia en vez de por el chip RFID que leyera el AMS. Esta fila y "
        "{survivor} siempre fueron una sola bobina física, vista por sus dos caras. "
        "Restáurala desde la Papelera si eso no es correcto."
    ),
}


def _retired_reason(survivor: str, language: str) -> str:
    """The caption, in Spanish for a Spanish instance and English for everything else —
    the same fallback `colour_name` makes, and for the same reason: this is stored data,
    written once, in the language the household spoke when it happened."""
    template = _RETIRED_BY_UPGRADE[
        "es" if language.replace("_", "-").split("-")[0].strip().lower() == "es" else "en"
    ]
    return template.format(survivor=survivor)


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
    # Stamps the retirement of a phantom row. The same clock every other use case takes,
    # so a Trash entry this module writes sorts against the ones the user wrote.
    clock: Clock
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
    # The instance's configured language, baked into the labels this use case writes: a
    # label is stored user data rather than a translated view, so it speaks the language
    # the household spoke when the spool appeared. Defaults English — the same fallback
    # the panel translator makes.
    language: str = "en"

    async def execute(self, reading: TrayReading) -> None:
        if reading.empty:
            await self._tray_emptied(reading.tray)
            return
        if reading.tag is None:
            return  # unreadable tag: nothing automatic is possible — see the module docstring
        # Before anything reads the inventory: a row that predates v2.6 knows its chip but
        # not its reel, and every question below is asked of the reel first. Healing here
        # means the rest of this use case sees one consistent world instead of each branch
        # having to remember that legacy rows answer to a different question.
        await self._adopt_reel(reading)
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
        await self._tag_appeared(reading, reading.tag, reading.tray)

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

    async def _adopt_reel(self, reading: TrayReading) -> None:
        """Teach the ledger which physical reel a chip belongs to. Writes nothing else.

        Two jobs, both of them healing, and both idempotent so the startup replay and every
        republish can run them harmlessly:

        **The second side.** A reel already known by its `reel_uid` turns up in a tray of
        the opposite parity, so the AMS reads its other chip. That chip is recorded against
        the reel, and from then on the reel resolves by either side even where no reel id is
        reported.

        **The legacy row.** A reel this ledger has never identified reports a chip that
        resolves to exactly one spool with no reel id of its own. That spool *is* this reel:
        it was registered before v2.6, when the ledger stored the chip and threw the reel
        away. It learns its identity here rather than in a migration, because a migration
        would have had to guess and this does not — the printer is holding the reel up and
        naming it.

        **Exactly one, or nothing.** An ambiguous chip is left alone: picking one of two
        candidates to adopt the reel onto would be this method deciding the very question
        `AmbiguousTagDetected` exists to refuse to decide. It stays ambiguous, and the user
        resolves it.

        Failure is not fatal. `identified_as` refuses to re-point a row that already names a
        different reel, and that refusal means *this is not the reel I thought it was* — the
        resolution below then treats it as an unknown reel, which registers rather than
        corrupts.
        """
        if reading.reel is None or reading.tag is None:
            return
        retired: list[DomainEvent] = []
        async with self.uow:
            known = await self.spools.find_by_reel(reading.reel)
            if known:
                # Everything this reel could be, gathered before anything is chosen.
                #
                # `known` is only the rows that have *learned* the reel, and a row learns it
                # by being read — so which rows are in here is a fact about which trays the
                # user happened to use, not about which row is genuine. A twin answers to
                # the hub's other chip and therefore cannot be in `known` at all: it turns
                # up through `find_by_tag` below, on the reading that finally comes from its
                # side.
                candidates = list(known)
                for other in await self.spools.find_by_tag(reading.tag):
                    if any(c.id == other.id for c in candidates):
                        continue
                    if other.reel_uid is not None and other.reel_uid != reading.reel:
                        # It speaks for a different reel. Whatever that is, it is not this
                        # reel's twin, and retiring it would be this method destroying a
                        # row on a guess.
                        continue
                    candidates.append(other)
                # **The oldest row survives, and it is chosen from the whole set.**
                #
                # This is the correction v2.6 needed. That release took `known[0]` — the
                # oldest row that had learned the reel — and a reel whose twin learned it
                # first therefore made the *twin* the survivor and retired the genuine row.
                # On the reference ledger that was live: the phantom minted on 13-08 held
                # the reel while the real row from 10-08 had learned nothing, so the next
                # reading from the other side would have binned three weeks of history.
                #
                # Age decides because age cannot be anything else: a twin exists only
                # because the reel crossed into a tray of the other parity *after* the
                # genuine row was already there, so it is always the younger of the two.
                survivor, *twins = sorted(candidates, key=lambda spool: spool.registered_at)
                # The survivor may be a row that never learned the reel — that is exactly
                # the case v2.6 got wrong. Teach it *first*, so the next reading resolves
                # straight to it and so the sentence written on each retired twin names a
                # row that already speaks for this reel.
                if survivor.reel_uid is None:
                    survivor = survivor.identified_as(reading.reel)
                    await self.spools.save(survivor)
                # Record the side we just read, onto whichever row survives. Claimed after
                # the scan above so the survivor's own row cannot be mistaken for a second
                # claimant of its own chip.
                await self.spools.claim_tag(survivor.id, reading.tag)
                for twin in twins:
                    retirement = await self._retire_phantom(twin, survivor)
                    if retirement is not None:
                        retired.append(retirement)
            else:
                await self._adopt_onto_legacy_row(reading)
        # Published after the commit — never for a write that could still roll back.
        for announcement in retired:
            await self.events.publish(announcement)

    async def _adopt_onto_legacy_row(self, reading: TrayReading) -> None:
        """A reel nobody has identified, whose chip resolves to exactly one unidentified row.

        That row *is* this reel: it was registered before v2.6, when the ledger stored the
        chip and threw the reel away. It learns its identity from the printer holding the
        reel up and naming it — which is why no migration had to guess one.

        Runs inside the caller's unit of work.
        """
        if reading.reel is None or reading.tag is None:  # pragma: no cover - caller checked
            return
        candidates = await self.spools.find_by_tag(reading.tag)
        if len(candidates) != 1:
            return
        spool = candidates[0]
        if spool.reel_uid is not None:
            return  # already speaks for another reel; not ours to re-point
        try:
            await self.spools.save(spool.identified_as(reading.reel))
        except DomainError:
            # Discarded, deleted, or already identified between the read and the write.
            # Nothing to heal, and nothing that should stop the tray being resolved.
            LOGGER.debug("could not identify %s as reel %s", spool.id, reading.reel)
            return
        await self.spools.claim_tag(spool.id, reading.tag)

    async def _retire_phantom(self, phantom: Spool, survivor: Spool) -> SpoolDeleted | None:
        """Send a row that was never a separate reel to the Trash, and say why.

        **Deleted, not merged.** A merge would have to decide what happens to two opening
        balances and two half-histories, and every rule for doing that is a rule invented
        on the user's behalf about grams the ledger cannot weigh. Deletion decides nothing:
        the row keeps its whole history, stops counting as stock, and sits in the Trash
        where the user can read the caption and put it back in one click if this release
        got it wrong. That reversibility is the entire licence for doing it unasked.

        **What it costs, stated plainly.** Movements charged to the phantom go out of the
        stock figures with it, so a reel whose printing was recorded against the phantom
        will read high until it is weighed. That is a visible, correctable number, and the
        *needs weighing* prompt already asks for exactly that — unlike a merged history
        stitched from two opening balances, which would be wrong in a way nothing surfaces.

        Anything already retired is left alone: a discarded or deleted row is out of
        inventory and has no claim on the reel to give up.

        Returns the event to announce, or `None` when there was nothing to retire. The
        caller publishes it *after* the unit of work commits — never for a write that could
        still roll back, which is this module's rule everywhere else too.
        """
        if not phantom.is_in_inventory:
            return None
        reason = _retired_reason(survivor.display_name, self.language)
        await self.spools.save(phantom.deleted(self.clock.now(), reason))
        LOGGER.info(
            "retired %s: it and %s are one reel (%s)",
            phantom.display_name,
            survivor.display_name,
            survivor.reel_uid,
        )
        return SpoolDeleted(spool_id=phantom.id, display_name=phantom.display_name)

    async def _register_unknown(self, reading: TrayReading) -> None:
        """UC-01 on the printer's say-so, when the reading describes the spool in full.

        Registered into storage, never straight into the slot: the ordinary mount path
        below owns displacement and the one-spool-per-tray index, and giving registration
        a second way into a tray would give that rule a second implementation.

        The idempotence hinge is the **reel** lookup, and that is the whole of this
        release's correction. A reel the ledger already owns is recognised wherever it
        sits — in this tray, in another tray, on a shelf — because `reel_uid` names the
        reel rather than the side of it the AMS happened to reach. The rule it replaces
        asked whether a *chip* matched something in this tray or in storage, and a reel
        moved from an odd tray to an even one answered no to both while being the same reel:
        that is how a ledger came to hold two rows, two opening balances and half a history
        each (docs/12-field-notes.md).

        **A reel with no identity keeps the old, weaker rule**, and keeps it knowingly.
        Third-party and refilled reels report no `tray_uuid`, so a chip is all there is to
        go on and the location heuristic is the best available answer — the same answer, and
        the same limits, as before v2.6.

        The lookups run *outside* this use case's unit of work because `RegisterSpool` opens
        its own — one connection cannot hold both — which leaves a window where someone else
        registers the same reel first. `RegisterSpool`'s duplicate-tag guard closes it, and
        losing that race is not a failure: the spool exists, which is all this method wants.
        """
        if not self.auto_register or reading.tag is None:
            return
        if reading.material is None or reading.colour is None:
            return  # a spool the system cannot describe is a spool it must not invent
        if reading.reel is not None:
            if await self.spools.find_by_reel(reading.reel):
                # We own this reel. Where it is standing is the resolution's business, not
                # registration's — and asking would reintroduce the very question that made
                # a moved reel look new.
                return
            candidates = await self.spools.find_by_tag(reading.tag)
        else:
            candidates = await self.spools.find_by_tag(reading.tag)
            if candidates:
                # A chip we know is a reel we know, wherever it is standing.
                #
                # This used to ask a further question — *is the match in this tray or in
                # storage?* — and register a twin when the answer was no, on the ground
                # that a Bambu tag identified a product batch and a match mounted elsewhere
                # was therefore a second reel of that batch. That ground does not exist.
                # A chip UID lives in block 0 of the tag, which is read-only and written by
                # the chip's manufacturer, so it identifies one chip; and a chip is glued
                # into one reel. Twelve reels on the reference machine carried twelve
                # distinct chip UIDs, including three reels of one product in one colour
                # (docs/12-field-notes.md). Nothing was ever observed to share one.
                #
                # So the location question could only ever produce the wrong answer, and it
                # produced it in the most ordinary situation there is: a reel moved from one
                # tray to another. The resolution below moves it instead.
                #
                # A user who genuinely wants two rows for one chip still gets them — by
                # saying so in the register form, which is what `confirm_duplicate_tag` is
                # for. What is gone is the *automatic* duplicate nobody asked for.
                return
        try:
            await self.register_spool.execute(
                RegisterSpoolCommand(
                    material=Material.from_printer(reading.material),
                    colour=reading.colour,
                    # The reel's own figure when the tag carries one: a 250 g or 750 g
                    # spool is born at its size rather than at a default that happens to
                    # be right only for the 1 kg reels. The configured default is the
                    # fallback for tags that decline to say — and absence really is
                    # absence here, because the boundary turns `tray_weight: "0"` into
                    # `None` rather than passing a zero the domain would refuse.
                    opening_weight=(
                        reading.weight
                        if reading.weight is not None
                        else self.default_opening_weight
                    ),
                    core_weight=self.default_core_weight,
                    vendor=BAMBU_VENDOR,
                    # The product name alone would be born twice for two reels of one
                    # product in two colours, so the colour's name rides along — in the
                    # instance's language, decided now, because a label is stored data.
                    label=label_with_colour(reading.name, reading.colour, self.language),
                    tag_uid=reading.tag,
                    # Born knowing which reel it is, so it is recognised from either side
                    # of the hub from its very first mount — the healing path above exists
                    # only for the rows that could not be.
                    reel_uid=reading.reel,
                    # The serial came off the tray reading, not off a keyboard
                    # (docs/14 §14.2) — the same provenance the register-from-sync
                    # dialog states.
                    tag_source=TagSource.DETECTED,
                    # The opening balance is the configured default nobody confirmed
                    # today, and the history's provenance label must say so.
                    movement_source=MovementSource.AUTOMATIC,
                    # Reaching here with candidates means the chip is spoken for but the
                    # reel is genuinely new — two reels whose chip UIDs collide, or a
                    # legacy row that speaks for a different reel. Either way the duplicate
                    # is deliberate, and the resolution then sees both and asks
                    # (`AmbiguousTagDetected`) instead of merging two reels into one row.
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

    async def _tag_appeared(self, reading: TrayReading, tag: TagUid, tray: TrayRef) -> None:
        """UC-02, automatic: resolve the reel, then mount — or ask.

        The whole read-compute-write runs inside one unit of work: resolution and
        displacement must see the same inventory, or a concurrent mount could interleave
        between the lookup and the write.

        **By reel when the printer named one, by chip otherwise.** The two are not
        alternatives of equal standing: the reel id answers *which reel is this* and holds
        still across trays, while the chip answers only *which side did the AMS read* and
        changes with the tray's parity. Leading with the chip is what used to make a reel
        that crossed from an odd tray to an even one resolve to nothing and be mounted as a
        stranger. The chip remains the answer for reels that report no identity at all —
        third-party and refilled — where it is the only fact available.
        """
        to_publish: list[DomainEvent] = []
        async with self.uow:
            # Both lookups already exclude discarded and deleted spools: those are out of
            # inventory, and a reel matching only retired rows is an unknown reel.
            candidates = (
                await self.spools.find_by_reel(reading.reel)
                if reading.reel is not None
                else await self.spools.find_by_tag(tag)
            )
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
