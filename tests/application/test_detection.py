"""UC-02 / UC-03, automatic paths, on real SQLite.

The printer gateway does not exist yet; these tests drive `DetectSpool` with the
`TrayReading` it will deliver, which is the whole point of the port — the behaviour is
pinned before the adapter is written, against the same schema and constraints production
runs on.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from custom_components.filament_ledger.application.adjust_spool import (
    DiscardFilamentCommand,
    DiscardMode,
)
from custom_components.filament_ledger.application.detect_spool import DetectSpool
from custom_components.filament_ledger.application.register_spool import RegisterSpoolCommand
from custom_components.filament_ledger.domain.event import (
    AmbiguousTagDetected,
    SpoolDetected,
    SpoolMounted,
    SpoolUnmounted,
    UnknownSpoolDetected,
)
from custom_components.filament_ledger.domain.model.spool import Spool
from custom_components.filament_ledger.domain.port.repositories import SpoolFilter
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import SpoolId, TagUid
from custom_components.filament_ledger.domain.value.location import AmsSlot, Location, Storage
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
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
        """No spool is created: a guessed opening weight is a fabricated number, and a
        fabricated number in a ledger is worse than a missing one."""
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
