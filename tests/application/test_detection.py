"""UC-02 / UC-03, automatic paths, on real SQLite.

The printer gateway does not exist yet; these tests drive `DetectSpool` with the
`TrayReading` it will deliver, which is the whole point of the port — the behaviour is
pinned before the adapter is written, against the same schema and constraints production
runs on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest

from custom_components.filament_ledger.application.adjust_spool import (
    DiscardFilamentCommand,
    DiscardMode,
)
from custom_components.filament_ledger.application.detect_spool import DetectSpool
from custom_components.filament_ledger.application.register_spool import (
    RegisterSpool,
    RegisterSpoolCommand,
)
from custom_components.filament_ledger.domain.error import DuplicateTagNotConfirmedError
from custom_components.filament_ledger.domain.event import (
    AmbiguousTagDetected,
    SpoolDeleted,
    SpoolDetected,
    SpoolMounted,
    SpoolRegistered,
    SpoolUnmounted,
    UnknownSpoolDetected,
)
from custom_components.filament_ledger.domain.model.spool import Spool
from custom_components.filament_ledger.domain.port.repositories import SpoolFilter
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    ReelUid,
    SpoolId,
    TagSource,
    TagUid,
)
from custom_components.filament_ledger.domain.value.location import AmsSlot, Location, Storage
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.movement_type import (
    MovementSource,
    MovementType,
)
from custom_components.filament_ledger.domain.value.tray_reading import TrayReading
from custom_components.filament_ledger.infrastructure.persistence.spool_repository import (
    SqliteSpoolRepository,
)

from .conftest import Ledger, a_tray

TAG = TagUid("A1B2C3D4")

# One physical reel, read from its two sides. A Bambu reel's tag is readable from either
# side of the hub and the AMS has two reader boards between four trays, so trays 1 and 3
# reach one chip and trays 2 and 4 the other — the reel is the same, the chip UID is not.
# Measured on the reference machine, three reels out of three (docs/12-field-notes.md).
REEL = ReelUid("C53610CFFA094C67AABC13AD9B661C04")
TAG_ODD_SIDE = TagUid("8C55BC6400000100")
TAG_EVEN_SIDE = TagUid("2C9CB8DD00000100")

# A different reel entirely, for the scenarios that must still tell two reels apart.
OTHER_REEL = ReelUid("1BCE3430A9864C73A21C16A72232E17F")
OTHER_TAG = TagUid("EC1325E200000100")


async def a_spool(ledger: Ledger, **overrides: object) -> SpoolId:
    command = RegisterSpoolCommand(
        material=Material.of(MaterialKind.PLA),
        colour=Colour.parse("000000"),
        opening_weight=Grams.of(1000),
        core_weight=Grams.of(250),
        vendor="Bambu Lab",
        **overrides,  # type: ignore[arg-type]
    )
    return await ledger.use_cases.register_spool.execute(command)


def an_occupied_tray(slot: int, tag: TagUid | None = TAG) -> TrayReading:
    return TrayReading(tray=a_tray(slot), tag=tag, empty=False)


def an_empty_tray(slot: int) -> TrayReading:
    return TrayReading(tray=a_tray(slot), tag=None, empty=True)


async def located(ledger: Ledger, spool_id: SpoolId) -> Spool:
    detail = await ledger.use_cases.queries.detail(spool_id)
    return detail.summary.spool


class TestATagAppears:
    async def test_a_single_match_is_mounted_without_a_movement(self, ledger: Ledger) -> None:
        """Moving a spool consumes no filament — the location changes, the ledger does not."""
        spool_id = await a_spool(ledger, tag_uid=TAG)

        await ledger.use_cases.detect_spool.execute(an_occupied_tray(2))

        assert (await located(ledger, spool_id)).location == AmsSlot(a_tray(2))
        assert len((await ledger.use_cases.queries.detail(spool_id)).lines) == 1
        [mounted] = ledger.events.of(SpoolMounted)
        assert isinstance(mounted, SpoolMounted)
        assert mounted == SpoolMounted(spool_id=spool_id, tray=a_tray(2))

    async def test_the_occupant_of_the_slot_is_displaced_to_storage(self, ledger: Ledger) -> None:
        """Same displacement semantics as the manual mount — one implementation, shared."""
        occupant = await a_spool(ledger, label="already there")
        await ledger.use_cases.mount_spool.execute(occupant, a_tray(2))
        arriving = await a_spool(ledger, label="tagged", tag_uid=TAG)

        await ledger.use_cases.detect_spool.execute(an_occupied_tray(2))

        assert (await located(ledger, occupant)).location == Storage()
        assert (await located(ledger, arriving)).location == AmsSlot(a_tray(2))
        assert SpoolUnmounted(spool_id=occupant) in ledger.events.published

    async def test_a_spool_follows_its_tag_between_slots(self, ledger: Ledger) -> None:
        """The tag travels with the reel: seen in another tray means it moved there."""
        spool_id = await a_spool(ledger, tag_uid=TAG)
        await ledger.use_cases.detect_spool.execute(an_occupied_tray(1))

        await ledger.use_cases.detect_spool.execute(an_occupied_tray(3))

        assert (await located(ledger, spool_id)).location == AmsSlot(a_tray(3))

    async def test_redetection_changes_nothing(self, ledger: Ledger) -> None:
        """The gateway replays `current_trays()` on startup; the second sighting of a
        spool already in its slot must not announce a mount that did not happen."""
        spool_id = await a_spool(ledger, tag_uid=TAG)
        await ledger.use_cases.detect_spool.execute(an_occupied_tray(2))

        await ledger.use_cases.detect_spool.execute(an_occupied_tray(2))

        assert (await located(ledger, spool_id)).location == AmsSlot(a_tray(2))
        assert len(ledger.events.of(SpoolMounted)) == 1
        assert ledger.events.of(SpoolUnmounted) == []

    async def test_an_unknown_tag_is_reported_never_invented(self, ledger: Ledger) -> None:
        """No spool is created: a bare tag names material and colour for nobody, so there
        is nothing honest to register — a guessed identity is a fabricated fact, and a
        fabricated fact in a ledger is worse than a missing one. The fully described case
        is `TestAutoRegister`'s, and it is the only opening in this rule."""
        await ledger.use_cases.detect_spool.execute(an_occupied_tray(4))

        assert await ledger.use_cases.queries.overview() == []
        [event] = ledger.events.of(UnknownSpoolDetected)
        assert isinstance(event, UnknownSpoolDetected)
        assert event == UnknownSpoolDetected(tag_uid=TAG, tray=a_tray(4))

    async def test_an_ambiguous_tag_asks_instead_of_guessing(self, ledger: Ledger) -> None:
        """Two spools from one batch legally share a tag. Picking one silently means every
        later print deducts from a spool sitting on a shelf."""
        first = await a_spool(ledger, label="first", tag_uid=TAG)
        second = await a_spool(ledger, label="second", tag_uid=TAG, confirm_duplicate_tag=True)

        await ledger.use_cases.detect_spool.execute(an_occupied_tray(2))

        assert (await located(ledger, first)).location == Storage()
        assert (await located(ledger, second)).location == Storage()
        assert ledger.events.of(SpoolMounted) == []
        [event] = ledger.events.of(AmbiguousTagDetected)
        assert isinstance(event, AmbiguousTagDetected)
        assert event.tray == a_tray(2)
        assert set(event.candidates) == {first, second}

    async def test_a_discarded_spool_cannot_be_the_answer(self, ledger: Ledger) -> None:
        """Discarded means out of inventory. A tag matching only a discarded spool is an
        unknown tag, not a mount."""
        spool_id = await a_spool(ledger, tag_uid=TAG)
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(spool_id=spool_id, mode=DiscardMode.WHOLE_SPOOL, reason="gone")
        )

        await ledger.use_cases.detect_spool.execute(an_occupied_tray(2))

        assert len(ledger.events.of(UnknownSpoolDetected)) == 1
        assert ledger.events.of(SpoolMounted) == []

    async def test_a_discarded_twin_does_not_make_a_tag_ambiguous(self, ledger: Ledger) -> None:
        binned = await a_spool(ledger, label="binned", tag_uid=TAG)
        kept = await a_spool(ledger, label="kept", tag_uid=TAG, confirm_duplicate_tag=True)
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(spool_id=binned, mode=DiscardMode.WHOLE_SPOOL, reason="gone")
        )

        await ledger.use_cases.detect_spool.execute(an_occupied_tray(2))

        assert (await located(ledger, kept)).location == AmsSlot(a_tray(2))
        assert ledger.events.of(AmbiguousTagDetected) == []


class TestATrayEmpties:
    async def test_the_recorded_occupant_returns_to_storage(self, ledger: Ledger) -> None:
        """UC-03, automatic: RFID absence detected. No movement — unmounting consumes no
        filament."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, a_tray(2))

        await ledger.use_cases.detect_spool.execute(an_empty_tray(2))

        assert (await located(ledger, spool_id)).location == Storage()
        assert len((await ledger.use_cases.queries.detail(spool_id)).lines) == 1
        assert len(ledger.events.of(SpoolUnmounted)) == 1

    async def test_an_empty_slot_with_nothing_recorded_is_a_no_op(self, ledger: Ledger) -> None:
        """Startup replays every tray; an empty tray over an empty slot must not invent an
        unmount for a spool that was never there."""
        await ledger.use_cases.detect_spool.execute(an_empty_tray(4))

        assert ledger.events.published == []


class TestAnUnreadableTag:
    async def test_an_occupied_tray_with_no_tag_changes_nothing(self, ledger: Ledger) -> None:
        """Nothing automatic is possible: the reading identifies no spool, and the spool
        recorded in that slot may well be the untagged one the user mounted by hand.
        docs/04-use-cases.md authorises automatic action on a tag appearing or a tray
        emptying — an unreadable tag is neither, so the ledger is left alone."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, a_tray(3))
        before = len(ledger.events.published)

        await ledger.use_cases.detect_spool.execute(an_occupied_tray(3, tag=None))

        assert (await located(ledger, spool_id)).location == AmsSlot(a_tray(3))
        assert len(ledger.events.published) == before


class TestAutoMountDisabled:
    """Some users keep spools registered to a shelf and load them briefly; silently
    rewriting their locations is not a service. The scenarios build their own `DetectSpool`
    with the flag off, the way `TestAtomicity` builds its own `RegisterSpool`."""

    def detect_spool_with_auto_mount_off(self, ledger: Ledger) -> DetectSpool:
        return DetectSpool(
            spools=SqliteSpoolRepository(ledger.database),
            events=ledger.events,
            uow=ledger.database,
            clock=ledger.clock,
            auto_mount=False,
            register_spool=ledger.use_cases.register_spool,
            default_opening_weight=Grams.of(1000),
            default_core_weight=Grams.of(250),
            auto_register=False,
        )

    async def test_a_known_tag_is_announced_but_not_moved(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger, tag_uid=TAG)

        await self.detect_spool_with_auto_mount_off(ledger).execute(an_occupied_tray(2))

        assert (await located(ledger, spool_id)).location == Storage()
        assert ledger.events.of(SpoolMounted) == []
        [event] = ledger.events.of(SpoolDetected)
        assert isinstance(event, SpoolDetected)
        assert event == SpoolDetected(tag_uid=TAG, tray=a_tray(2))

    async def test_the_option_gates_mounting_not_unmounting(self, ledger: Ledger) -> None:
        """UC-03 carries no such precondition: a tray reporting empty means the spool left
        the machine, and recording that rewrites nothing the user chose."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, a_tray(1))

        await self.detect_spool_with_auto_mount_off(ledger).execute(an_empty_tray(1))

        assert (await located(ledger, spool_id)).location == Storage()
        assert len(ledger.events.of(SpoolUnmounted)) == 1


def a_full_reading(
    slot: int,
    *,
    material: str = "PLA",
    name: str = "Bambu PLA Basic",
    weight: Grams | None = None,
    tag: TagUid = TAG,
    reel: ReelUid | None = None,
) -> TrayReading:
    """What a Bambu reel actually produces: tag, name, material and colour, all present.

    `weight` defaults absent, which is the tag declining to say — the boundary turns
    `tray_weight: "0"` into exactly that, and the configured default stands in.

    `reel` defaults absent so that every scenario written before v2.6 goes on describing
    exactly what it always described: a reel with no reported identity, resolved by its
    chip. The scenarios that are *about* the reel pass one.
    """
    return TrayReading(
        tray=a_tray(slot),
        tag=tag,
        empty=False,
        reel=reel,
        name=name,
        material=material,
        colour=Colour.parse("00FF00FF"),
        weight=weight,
    )


@dataclass
class RacingRegisterSpool:
    """Loses the duplicate-tag race on purpose: the competitor lands first, then the
    refusal arrives — exactly what `DetectSpool` sees when startup replay and a live tray
    event both read the same unknown tag before either registered it."""

    ledger: Ledger

    async def execute(self, command: RegisterSpoolCommand) -> SpoolId:
        await self.ledger.use_cases.register_spool.execute(command)
        msg = f"tag {command.tag_uid} already belongs to 1 spool(s)"
        raise DuplicateTagNotConfirmedError(msg)


class TestAutoRegister:
    """The one opening in "an unknown tag never creates a spool": a reading naming
    material and colour describes the spool in full, so nothing about registering it is a
    guess — the opening weight is the configured default the user already stated. The
    scenarios build their own `DetectSpool` with the flag on, the way the auto-mount-off
    ones do; the fixture wires it off so the reporting branches stay observable."""

    def detection(
        self,
        ledger: Ledger,
        *,
        auto_mount: bool = True,
        auto_register: bool = True,
        register_spool: RegisterSpool | None = None,
        language: str = "en",
    ) -> DetectSpool:
        # Defaults distinct from the register form's 1000/250, so the assertions prove
        # the *configured* figures flowed through rather than a coincidence.
        return DetectSpool(
            spools=SqliteSpoolRepository(ledger.database),
            events=ledger.events,
            uow=ledger.database,
            clock=ledger.clock,
            auto_mount=auto_mount,
            register_spool=(
                register_spool if register_spool is not None else ledger.use_cases.register_spool
            ),
            default_opening_weight=Grams.of(800),
            default_core_weight=Grams.of(200),
            auto_register=auto_register,
            language=language,
        )

    async def test_a_fully_described_unknown_tag_registers_and_mounts(self, ledger: Ledger) -> None:
        """The whole path in one pass: the spool is born from the reading and the
        configured defaults, and the ordinary mount path puts it in the tray."""
        await self.detection(ledger).execute(a_full_reading(2))

        [summary] = await ledger.use_cases.queries.overview()
        spool = summary.spool
        assert spool.tag_uid == TAG
        assert spool.tag_source is TagSource.DETECTED
        assert spool.vendor == "Bambu Lab"
        # The product name alone would be born twice for two reels in two colours, so
        # the label carries the colour's name — the reading's #00FF00 is green.
        assert spool.label == "Bambu PLA Basic Green"
        assert spool.material == Material.of(MaterialKind.PLA)
        assert spool.colour == Colour.parse("00FF00FF")
        assert spool.opening_weight == Grams.of(800)
        assert spool.core_weight == Grams.of(200)
        assert spool.location == AmsSlot(a_tray(2))
        assert len(ledger.events.of(SpoolRegistered)) == 1
        [mounted] = ledger.events.of(SpoolMounted)
        assert mounted == SpoolMounted(spool_id=spool.id, tray=a_tray(2))
        assert ledger.events.of(UnknownSpoolDetected) == []

    async def test_the_reels_own_weight_opens_the_balance(self, ledger: Ledger) -> None:
        """A 250 g reel is born at 250 g: the tag carried the figure, so the configured
        default — right only for the kilo spools — has nothing to stand in for."""
        await self.detection(ledger).execute(a_full_reading(2, weight=Grams.of(250)))

        [summary] = await ledger.use_cases.queries.overview()
        assert summary.spool.opening_weight == Grams.of(250)
        assert summary.balance == Grams.of(250)

    async def test_a_tag_that_gives_no_weight_falls_back_to_the_default(
        self, ledger: Ledger
    ) -> None:
        """`tray_weight: "0"`, an unparseable figure and a missing attribute all reach
        this use case as the same absence, and absence is what the default is for."""
        await self.detection(ledger).execute(a_full_reading(2))

        [summary] = await ledger.use_cases.queries.overview()
        assert summary.spool.opening_weight == Grams.of(800)

    async def test_the_label_speaks_the_instances_language(self, ledger: Ledger) -> None:
        """A label is stored data, not a translated view: a Spanish instance writes
        Spanish once, and changing the language later renames nothing."""
        await self.detection(ledger, language="es").execute(a_full_reading(2))

        [summary] = await ledger.use_cases.queries.overview()
        assert summary.spool.label == "Bambu PLA Basic Verde"

    async def test_the_opening_balance_is_labelled_automatic(self, ledger: Ledger) -> None:
        """Provenance stays honest: nobody confirmed the default weight today, and the
        history's *automatic* label is how the reader learns that."""
        await self.detection(ledger).execute(a_full_reading(2))

        [summary] = await ledger.use_cases.queries.overview()
        [line] = (await ledger.use_cases.queries.detail(summary.spool.id)).lines
        assert line.movement.type is MovementType.OPENING_BALANCE
        assert line.movement.source is MovementSource.AUTOMATIC

    async def test_a_variant_material_registers_truthfully_as_other(self, ledger: Ledger) -> None:
        """ "PLA-CF" is not PLA, and filing it under the nearest kind would be a fabricated
        fact with a plausible face. OTHER carrying the printer's exact words is the truth."""
        await self.detection(ledger).execute(a_full_reading(2, material="PLA-CF"))

        [summary] = await ledger.use_cases.queries.overview()
        assert summary.spool.material == Material.other("PLA-CF")

    @pytest.mark.parametrize(
        ("material", "colour"),
        [
            pytest.param("PLA", None, id="a-reading-with-no-colour"),
            pytest.param(None, Colour.parse("00FF00FF"), id="a-reading-with-no-material"),
        ],
    )
    async def test_a_partial_reading_reports_instead_of_inventing(
        self, ledger: Ledger, material: str | None, colour: Colour | None
    ) -> None:
        """Either hint missing means the system cannot describe the spool, and a spool it
        cannot describe is a spool it must not invent — exactly the old rule."""
        reading = TrayReading(
            tray=a_tray(4), tag=TAG, empty=False, material=material, colour=colour
        )

        await self.detection(ledger).execute(reading)

        assert await ledger.use_cases.queries.overview() == []
        [event] = ledger.events.of(UnknownSpoolDetected)
        assert event == UnknownSpoolDetected(tag_uid=TAG, tray=a_tray(4))

    async def test_the_option_off_keeps_todays_behaviour(self, ledger: Ledger) -> None:
        """A full reading with `auto_register` off is an unknown tag, reported exactly as
        it always was — the option governs the opening, not the rule."""
        await self.detection(ledger, auto_register=False).execute(a_full_reading(4))

        assert await ledger.use_cases.queries.overview() == []
        assert len(ledger.events.of(UnknownSpoolDetected)) == 1
        assert ledger.events.of(SpoolRegistered) == []

    async def test_with_auto_mount_off_the_spool_registers_to_storage(self, ledger: Ledger) -> None:
        """The two options are independent: the spool enters the inventory, but the user
        asked the system not to move spools, so the sighting is reported and the AMS view
        offers the manual [ Mount ] button for a spool that now exists."""
        await self.detection(ledger, auto_mount=False).execute(a_full_reading(2))

        [summary] = await ledger.use_cases.queries.overview()
        assert summary.spool.location == Storage()
        assert ledger.events.of(SpoolMounted) == []
        [event] = ledger.events.of(SpoolDetected)
        assert event == SpoolDetected(tag_uid=TAG, tray=a_tray(2))

    async def test_the_same_chip_in_a_second_tray_is_the_same_reel_moved(
        self, ledger: Ledger
    ) -> None:
        """**Reversed in v2.6, on evidence.** This test used to assert the opposite — that
        one chip UID seen in two trays was two reels of a shared product batch, registered
        as a deliberate duplicate for the user to disambiguate.

        That premise was never measured and is false. A chip UID lives in block 0 of the
        tag, which is read-only and written by the chip's manufacturer, so it names one
        chip; and a chip is glued into one reel. On the reference machine twelve reels
        carried twelve distinct chip UIDs — including three reels of the same product in
        the same colour, which is precisely the case the batch story predicted would
        collide (docs/12-field-notes.md).

        What the old rule actually produced, every time, was a duplicate for the most
        ordinary event there is: a reel moved from one tray to another. So a recognised chip
        now means a recognised reel, and the reel is mounted where it was found.

        A user who really wants two rows for one chip still gets them, by saying so in the
        register form — `confirm_duplicate_tag`. What is gone is the automatic duplicate
        nobody asked for.
        """
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(1))

        await detection.execute(a_full_reading(2))

        [summary] = await ledger.use_cases.queries.overview()
        assert summary.spool.location == AmsSlot(a_tray(2))
        assert len(ledger.events.of(SpoolRegistered)) == 1
        assert ledger.events.of(AmbiguousTagDetected) == []

    async def test_a_replayed_reading_registers_nothing_twice(self, ledger: Ledger) -> None:
        """Startup replays every tray. The second pass finds the tag it registered on the
        first, confirms the mount, and writes and announces nothing."""
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(2))

        await detection.execute(a_full_reading(2))

        assert len(await ledger.use_cases.queries.overview()) == 1
        assert len(ledger.events.of(SpoolRegistered)) == 1
        assert len(ledger.events.of(SpoolMounted)) == 1

    async def test_losing_the_registration_race_mounts_the_winner(self, ledger: Ledger) -> None:
        """Between the unknown-tag check and the write, someone else registered the tag.
        The refusal is the race being lost, not a failure: the spool exists, so the
        ordinary resolution mounts it and nothing crashes."""
        racing = cast(RegisterSpool, RacingRegisterSpool(ledger))

        await self.detection(ledger, register_spool=racing).execute(a_full_reading(2))

        [summary] = await ledger.use_cases.queries.overview()
        assert summary.spool.location == AmsSlot(a_tray(2))
        assert len(ledger.events.of(SpoolRegistered)) == 1


class TestAtomicityOfDetection:
    async def test_a_failed_displacement_mounts_nothing(self, ledger: Ledger) -> None:
        """The displaced occupant and the mounted spool commit together or not at all: a
        crash between the two writes must not leave the occupant unmounted with the
        arriving spool still in storage."""
        occupant = await a_spool(ledger, label="already there")
        await ledger.use_cases.mount_spool.execute(occupant, a_tray(2))
        arriving = await a_spool(ledger, label="tagged", tag_uid=TAG)
        events_before = len(ledger.events.published)

        detection = DetectSpool(
            spools=UnsavableSpools(SqliteSpoolRepository(ledger.database), fail_after=1),
            events=ledger.events,
            uow=ledger.database,
            clock=ledger.clock,
            auto_mount=True,
            register_spool=ledger.use_cases.register_spool,
            default_opening_weight=Grams.of(1000),
            default_core_weight=Grams.of(250),
            auto_register=False,
        )
        with pytest.raises(RuntimeError, match="unavailable"):
            await detection.execute(an_occupied_tray(2))

        assert (await located(ledger, occupant)).location == AmsSlot(a_tray(2))
        assert (await located(ledger, arriving)).location == Storage()
        assert len(ledger.events.published) == events_before


@dataclass
class UnsavableSpools:
    """A spool repository whose save starts failing after `fail_after` calls — the
    injected crash between the displacement write and the mount write."""

    inner: SqliteSpoolRepository
    fail_after: int

    async def get(self, spool_id: SpoolId) -> Spool | None:
        return await self.inner.get(spool_id)

    async def find_by_tag(self, tag: TagUid) -> list[Spool]:
        return await self.inner.find_by_tag(tag)

    async def find_by_reel(self, reel: ReelUid) -> list[Spool]:
        return await self.inner.find_by_reel(reel)

    async def claim_tag(self, spool_id: SpoolId, tag: TagUid) -> None:
        # Passed through rather than counted against `fail_after`: the injected crash this
        # double exists to stage is the one *between the two location writes*, and a chip
        # index that failed alongside it would blur which write the assertions are about.
        await self.inner.claim_tag(spool_id, tag)

    async def find_by_location(self, location: Location) -> Spool | None:
        return await self.inner.find_by_location(location)

    async def list(self, criteria: SpoolFilter) -> list[Spool]:
        return await self.inner.list(criteria)

    async def save(self, spool: Spool) -> None:
        if self.fail_after <= 0:
            msg = "the ledger is unavailable"
            raise RuntimeError(msg)
        self.fail_after -= 1
        await self.inner.save(spool)


class TestAReelIsNotItsChip:
    """docs/12-field-notes.md — the defect this release exists to end.

    A Bambu reel's RFID is readable from either side of its hub, and the AMS has two reader
    boards serving four trays: slots 1 and 3 reach one chip, slots 2 and 4 the other. So a
    reel that changes tray across that boundary reports a **different `tag_uid`** while
    remaining the same reel — and a ledger that recognised reels by chip therefore met a
    stranger every time a reel moved, opened it a second balance, and split its history.

    `tray_uuid` was in every one of those readings and nothing read it. It names the reel,
    it does not move, and it is what recognition leads with now.
    """

    def detection(self, ledger: Ledger, *, auto_register: bool = True) -> DetectSpool:
        return DetectSpool(
            spools=SqliteSpoolRepository(ledger.database),
            events=ledger.events,
            uow=ledger.database,
            clock=ledger.clock,
            auto_mount=True,
            register_spool=ledger.use_cases.register_spool,
            default_opening_weight=Grams.of(1000),
            default_core_weight=Grams.of(250),
            auto_register=auto_register,
        )

    async def test_the_same_reel_read_from_its_other_side_is_not_a_new_spool(
        self, ledger: Ledger
    ) -> None:
        """**The regression.** Tray 3 reads one chip, tray 4 reads the other, and it is one
        reel: it moves, it does not multiply. Before v2.6 this produced two rows, two
        opening balances of 1 kg and an `AmbiguousTagDetected` the user had to clean up by
        hand."""
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        [summary] = await ledger.use_cases.queries.overview()
        assert summary.spool.location == AmsSlot(a_tray(4))
        assert len(ledger.events.of(SpoolRegistered)) == 1
        assert ledger.events.of(AmbiguousTagDetected) == []
        assert ledger.events.of(UnknownSpoolDetected) == []

    async def test_the_reel_is_recognised_by_either_side_once_both_are_known(
        self, ledger: Ledger
    ) -> None:
        """Having met both sides, the reel resolves by either chip — which is what carries
        a reading whose identity is missing but whose chip is not."""
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))
        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        # No reel id this time: only the chip the even side carries.
        await detection.execute(a_full_reading(2, tag=TAG_EVEN_SIDE))

        [summary] = await ledger.use_cases.queries.overview()
        assert summary.spool.location == AmsSlot(a_tray(2))
        assert len(ledger.events.of(SpoolRegistered)) == 1

    async def test_a_row_that_predates_the_reel_id_learns_which_reel_it_is(
        self, ledger: Ledger
    ) -> None:
        """The healing path, and the reason no migration had to guess. A row registered
        before v2.6 stored the chip and threw the reel away; the first reading that names
        both adopts the reel onto the row the chip already resolves to."""
        legacy = await a_spool(ledger, tag_uid=TAG_ODD_SIDE)
        assert (await located(ledger, legacy)).reel_uid is None

        await self.detection(ledger).execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        assert (await located(ledger, legacy)).reel_uid == REEL
        assert len(await ledger.use_cases.queries.overview()) == 1

    async def test_a_healed_legacy_row_then_survives_the_side_change(self, ledger: Ledger) -> None:
        """The whole point of healing: the pre-v2.6 row is now protected by the same rule a
        row born today is, so the move that used to split it no longer can."""
        legacy = await a_spool(ledger, tag_uid=TAG_ODD_SIDE)
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        summaries = await ledger.use_cases.queries.overview()
        assert len(summaries) == 1
        assert (await located(ledger, legacy)).location == AmsSlot(a_tray(4))
        assert ledger.events.of(AmbiguousTagDetected) == []

    async def test_two_genuinely_different_reels_still_register_separately(
        self, ledger: Ledger
    ) -> None:
        """The correction must not overshoot: recognising a moved reel is not the same as
        merging two reels. Different identity, different chip — two rows, as it should be."""
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        await detection.execute(a_full_reading(4, tag=OTHER_TAG, reel=OTHER_REEL))

        summaries = await ledger.use_cases.queries.overview()
        assert len(summaries) == 2
        assert {s.spool.reel_uid for s in summaries} == {REEL, OTHER_REEL}

    async def test_a_reel_that_reports_no_identity_keeps_the_chip_rule(
        self, ledger: Ledger
    ) -> None:
        """Third-party and refilled reels report no `tray_uuid`, so the chip is all there
        is and the pre-v2.6 rule stands for them — unchanged, and knowingly weaker."""
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(3))

        await detection.execute(a_full_reading(3))

        assert len(await ledger.use_cases.queries.overview()) == 1
        assert len(ledger.events.of(SpoolRegistered)) == 1

    async def test_a_row_already_speaking_for_one_reel_is_never_re_pointed(
        self, ledger: Ledger
    ) -> None:
        """A chip resolving to a spool that claims another reel is a contradiction — two
        reels sharing a chip UID, or a hub moved between reels. Overwriting would hide it,
        so the reading is treated as a reel we do not know and registers instead."""
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        # The same chip, now claiming to belong to a different reel.
        await detection.execute(a_full_reading(4, tag=TAG_ODD_SIDE, reel=OTHER_REEL))

        summaries = await ledger.use_cases.queries.overview()
        assert len(summaries) == 2
        assert {s.spool.reel_uid for s in summaries} == {REEL, OTHER_REEL}

    async def test_a_replayed_reading_of_a_known_reel_writes_nothing(self, ledger: Ledger) -> None:
        """Startup replays every tray, and every republish re-observes the same chip. The
        second pass must confirm and stay silent."""
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        assert len(await ledger.use_cases.queries.overview()) == 1
        assert len(ledger.events.of(SpoolRegistered)) == 1
        assert len(ledger.events.of(SpoolMounted)) == 1

    async def test_the_reel_is_recognised_after_a_spell_in_storage(self, ledger: Ledger) -> None:
        """The reported symptom, in miniature: take the reel out, put it back later in a
        tray of the other parity. It comes home to its row."""
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))
        await detection.execute(an_empty_tray(3))

        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        [summary] = await ledger.use_cases.queries.overview()
        assert summary.spool.location == AmsSlot(a_tray(4))
        assert len(ledger.events.of(SpoolRegistered)) == 1


class TestThePhantomIsRetiredNotMerged:
    """What happens to a ledger that already holds the pair the old rule minted.

    The two rows are not stitched together. A merge would have to rule on two opening
    balances and two half-histories, and every rule for that is one the ledger invents on
    the user's behalf about grams it cannot weigh. So the newer row — the phantom, which by
    construction could only have been born when the reel first crossed into a tray of the
    other parity — goes to the Trash with its history intact and a sentence saying why, and
    the user puts it back in one click if this release got it wrong.
    """

    def detection(self, ledger: Ledger, *, language: str = "en") -> DetectSpool:
        return DetectSpool(
            spools=SqliteSpoolRepository(ledger.database),
            events=ledger.events,
            uow=ledger.database,
            clock=ledger.clock,
            auto_mount=True,
            register_spool=ledger.use_cases.register_spool,
            default_opening_weight=Grams.of(1000),
            default_core_weight=Grams.of(250),
            auto_register=True,
            language=language,
        )

    async def a_split_pair(self, ledger: Ledger) -> tuple[SpoolId, SpoolId]:
        """The exact shape a pre-v2.6 ledger arrives in: one reel, two rows, one per side.

        Built through the *old* path deliberately — two separate registrations, neither
        knowing a reel — because a pair assembled any other way would only prove the test
        agrees with itself.
        """
        older = await a_spool(ledger, label="the real one", tag_uid=TAG_ODD_SIDE)
        newer = await a_spool(ledger, label="the phantom", tag_uid=TAG_EVEN_SIDE)
        return older, newer

    async def test_the_newer_row_goes_to_the_trash_and_the_older_survives(
        self, ledger: Ledger
    ) -> None:
        """Age decides, and nothing else does — not the balance, not which row is mounted."""
        older, newer = await self.a_split_pair(ledger)
        detection = self.detection(ledger)

        # Odd side first: the older row learns the reel.
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))
        # Even side: the phantom is recognised as the same reel.
        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        survivors = await ledger.use_cases.queries.overview()
        assert [s.spool.id for s in survivors] == [older]
        retired = await ledger.use_cases.queries.detail(newer)
        assert retired.summary.spool.is_deleted

    async def test_the_retired_row_says_why_and_names_the_survivor(self, ledger: Ledger) -> None:
        """A deletion nobody asked for is only defensible if the person it happened to can
        read what happened and undo it."""
        _, newer = await self.a_split_pair(ledger)
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        reason = (await ledger.use_cases.queries.detail(newer)).summary.spool.deleted_reason
        assert reason is not None
        assert "the real one" in reason, "the surviving row is named so the user can check"
        assert "Trash" in reason, "the undo is stated out loud"

    async def test_the_reason_speaks_the_instances_language(self, ledger: Ledger) -> None:
        """Stored data, written once, in the language the household spoke — the same rule
        the auto-registered label follows."""
        _, newer = await self.a_split_pair(ledger)
        detection = self.detection(ledger, language="es")
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        reason = (await ledger.use_cases.queries.detail(newer)).summary.spool.deleted_reason
        assert reason is not None
        assert "Papelera" in reason

    async def test_the_retirement_is_announced(self, ledger: Ledger) -> None:
        """Same event a user-driven deletion raises, so an automation that notifies on
        `SpoolDeleted` covers this too — the whole point of not inventing a second event."""
        _, newer = await self.a_split_pair(ledger)
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        [event] = ledger.events.of(SpoolDeleted)
        assert isinstance(event, SpoolDeleted)
        assert event.spool_id == newer

    async def test_the_user_can_put_it_back(self, ledger: Ledger) -> None:
        """The reversibility that licenses doing this unasked. Restoring also clears the
        caption: the row is in inventory on the user's own say-so now."""
        _, newer = await self.a_split_pair(ledger)
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))
        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        await ledger.use_cases.restore_spool.execute(newer)

        restored = (await ledger.use_cases.queries.detail(newer)).summary.spool
        assert not restored.is_deleted
        assert restored.deleted_reason is None

    async def test_its_history_survives_the_retirement(self, ledger: Ledger) -> None:
        """Deleted, not erased. The movements stay on the row so the user can read what was
        charged to it before deciding whether to restore it."""
        _, newer = await self.a_split_pair(ledger)
        lines_before = len((await ledger.use_cases.queries.detail(newer)).lines)
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        assert len((await ledger.use_cases.queries.detail(newer)).lines) == lines_before

    async def test_a_row_speaking_for_another_reel_is_never_retired(self, ledger: Ledger) -> None:
        """The one guard on a destructive automatic act: a row that names a *different*
        reel is not this reel's phantom, whatever chip it answers to."""
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))
        await detection.execute(a_full_reading(1, tag=OTHER_TAG, reel=OTHER_REEL))

        # A reading of the first reel that happens to carry the other reel's chip.
        await detection.execute(a_full_reading(4, tag=OTHER_TAG, reel=REEL))

        surviving = {s.spool.reel_uid for s in await ledger.use_cases.queries.overview()}
        assert OTHER_REEL in surviving, "the other reel's row was retired on a guess"

    async def test_running_twice_retires_nothing_further(self, ledger: Ledger) -> None:
        """Startup replays every tray. The second pass finds one row and must leave it."""
        older, _ = await self.a_split_pair(ledger)
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))
        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        assert [s.spool.id for s in await ledger.use_cases.queries.overview()] == [older]
        assert len(ledger.events.of(SpoolDeleted)) == 1


class TestTheOldestRowSurvivesWhicheverSideWasReadFirst:
    """The defect v2.6 shipped, reproduced from the ledger it was found on.

    v2.6 chose the survivor as `find_by_reel(...)[0]` — the oldest row **that had already
    learned the reel**. Which rows have learned it is a fact about which trays the user
    happened to use, not about which row is genuine, so a reel whose *twin* was read first
    made the twin the survivor and queued the real row for retirement.

    On the reference ledger that was live and loaded: the phantom minted on 13-08 held reel
    `C53610CF…` because it sat in tray 4, while the genuine row from 10-08 sat in storage
    having learned nothing. The next reading from an odd tray would have binned three weeks
    of history and kept the row whose only entry was an opening balance.

    Age decides, and it can decide because a twin exists only by being born later — the
    reel had to already be in the ledger before it could cross into a tray of the other
    parity and be met a second time.
    """

    def detection(self, ledger: Ledger) -> DetectSpool:
        return DetectSpool(
            spools=SqliteSpoolRepository(ledger.database),
            events=ledger.events,
            uow=ledger.database,
            clock=ledger.clock,
            auto_mount=True,
            register_spool=ledger.use_cases.register_spool,
            default_opening_weight=Grams.of(1000),
            default_core_weight=Grams.of(250),
            auto_register=True,
        )

    async def a_split_pair(self, ledger: Ledger) -> tuple[SpoolId, SpoolId]:
        """One reel, two rows, one per side — the shape a pre-2.6 ledger arrives in."""
        older = await a_spool(ledger, label="the real one", tag_uid=TAG_ODD_SIDE)
        ledger.clock.advance(hours=72)
        newer = await a_spool(ledger, label="the phantom", tag_uid=TAG_EVEN_SIDE)
        return older, newer

    async def test_the_twin_learning_the_reel_first_does_not_make_it_the_survivor(
        self, ledger: Ledger
    ) -> None:
        """**The regression.** The even side is read first, so the younger row learns the
        reel; the odd side follows. The older row must still be the one left standing."""
        older, newer = await self.a_split_pair(ledger)
        detection = self.detection(ledger)

        # Even tray first: the phantom is the only row that knows the reel.
        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))
        # Then the reel moves to an odd tray, which is where the twin finally surfaces.
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        survivors = [s.spool.id for s in await ledger.use_cases.queries.overview()]
        assert survivors == [older], "the genuine row was retired in favour of its twin"
        assert (await ledger.use_cases.queries.detail(newer)).summary.spool.is_deleted

    async def test_the_surviving_row_ends_up_owning_the_reel(self, ledger: Ledger) -> None:
        """It has to, or the next reading would resolve to nothing and register a third."""
        older, _ = await self.a_split_pair(ledger)
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        assert (await located(ledger, older)).reel_uid == REEL

    async def test_the_reel_then_resolves_to_the_survivor_from_either_side(
        self, ledger: Ledger
    ) -> None:
        """Both chips belong to the surviving row now, so neither side can mint anything."""
        older, _ = await self.a_split_pair(ledger)
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))
        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        await detection.execute(a_full_reading(2, tag=TAG_EVEN_SIDE, reel=REEL))
        await detection.execute(a_full_reading(1, tag=TAG_ODD_SIDE, reel=REEL))

        survivors = [s.spool.id for s in await ledger.use_cases.queries.overview()]
        assert survivors == [older]

    async def test_the_history_kept_is_the_older_rows(self, ledger: Ledger) -> None:
        """The point of choosing by age, measured where it hurts: the row that survives is
        the one carrying the movements."""
        await self.a_split_pair(ledger)
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))

        [surviving] = await ledger.use_cases.queries.overview()
        assert surviving.spool.label == "the real one"

    async def test_reading_the_odd_side_first_reaches_the_same_answer(self, ledger: Ledger) -> None:
        """Order of discovery must not change the outcome — that was the whole defect."""
        older, newer = await self.a_split_pair(ledger)
        detection = self.detection(ledger)

        await detection.execute(a_full_reading(3, tag=TAG_ODD_SIDE, reel=REEL))
        await detection.execute(a_full_reading(4, tag=TAG_EVEN_SIDE, reel=REEL))

        survivors = [s.spool.id for s in await ledger.use_cases.queries.overview()]
        assert survivors == [older]
        assert (await ledger.use_cases.queries.detail(newer)).summary.spool.is_deleted
