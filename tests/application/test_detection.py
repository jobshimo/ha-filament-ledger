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
    slot: int, *, material: str = "PLA", name: str = "Bambu PLA Basic"
) -> TrayReading:
    """What a Bambu reel actually produces: tag, name, material and colour, all present."""
    return TrayReading(
        tray=a_tray(slot),
        tag=TAG,
        empty=False,
        name=name,
        material=material,
        colour=Colour.parse("00FF00FF"),
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
    ) -> DetectSpool:
        # Defaults distinct from the register form's 1000/250, so the assertions prove
        # the *configured* figures flowed through rather than a coincidence.
        return DetectSpool(
            spools=SqliteSpoolRepository(ledger.database),
            events=ledger.events,
            uow=ledger.database,
            auto_mount=auto_mount,
            register_spool=(
                register_spool if register_spool is not None else ledger.use_cases.register_spool
            ),
            default_opening_weight=Grams.of(800),
            default_core_weight=Grams.of(200),
            auto_register=auto_register,
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
        assert spool.label == "Bambu PLA Basic"
        assert spool.material == Material.of(MaterialKind.PLA)
        assert spool.colour == Colour.parse("00FF00FF")
        assert spool.opening_weight == Grams.of(800)
        assert spool.core_weight == Grams.of(200)
        assert spool.location == AmsSlot(a_tray(2))
        assert len(ledger.events.of(SpoolRegistered)) == 1
        [mounted] = ledger.events.of(SpoolMounted)
        assert mounted == SpoolMounted(spool_id=spool.id, tray=a_tray(2))
        assert ledger.events.of(UnknownSpoolDetected) == []

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

    async def test_a_second_tray_with_the_same_batch_tag_is_a_second_reel(
        self, ledger: Ledger
    ) -> None:
        """A Bambu tag identifies a batch, not a unit: the same unregistered tag in two
        trays is two physical reels. The second registers as a deliberate duplicate and
        the resolution asks — merging both into one row would let tray 2 silently steal
        tray 1's spool."""
        detection = self.detection(ledger)
        await detection.execute(a_full_reading(1))

        await detection.execute(a_full_reading(2))

        summaries = await ledger.use_cases.queries.overview()
        assert len(summaries) == 2
        # The first reel stays where it was mounted; the second waits in storage.
        assert {s.spool.location for s in summaries} == {AmsSlot(a_tray(1)), Storage()}
        [event] = ledger.events.of(AmbiguousTagDetected)
        assert event.tray == a_tray(2)
        assert len(event.candidates) == 2
        assert ledger.events.of(SpoolUnmounted) == []

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
