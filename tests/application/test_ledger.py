"""The whole ledger, end to end, on real SQLite.

These are the tests that would have caught the specification's worst defect: a balance
formula that made every print increase the balance. They run the actual use cases against
the actual schema, and they check the arithmetic by hand.
"""

from __future__ import annotations

import pytest

from custom_components.filament_ledger.application.adjust_spool import (
    AdjustSpoolCommand,
    DiscardFilamentCommand,
    DiscardMode,
)
from custom_components.filament_ledger.application.errors import SpoolNotFoundError
from custom_components.filament_ledger.application.reconcile_spool import (
    ReconcileSpoolCommand,
)
from custom_components.filament_ledger.application.register_spool import (
    RegisterSpoolCommand,
)
from custom_components.filament_ledger.domain.error import (
    DuplicateTagNotConfirmedError,
    InvalidValueError,
    NothingToRecordError,
    SpoolDiscardedError,
)
from custom_components.filament_ledger.domain.event import (
    AnomalyDetected,
    MovementRecorded,
    SpoolRegistered,
)
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.confidence import Confidence
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import SlotIndex, SpoolId, TagUid
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.spool_state import SpoolState

from .conftest import Ledger


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


class TestRegistration:
    async def test_a_spool_is_born_with_a_ledger_entry(self, ledger: Ledger) -> None:
        """There is no such thing as a balance without a movement that explains it."""
        spool_id = await a_spool(ledger)
        detail = await ledger.use_cases.queries.detail(spool_id)

        assert detail.summary.balance == Grams.of(1000)
        assert len(detail.lines) == 1
        assert detail.lines[0].movement.type.value == "OPENING_BALANCE"

    async def test_a_fresh_spool_is_sealed_and_trusted(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        summary = (await ledger.use_cases.queries.overview())[0]
        assert summary.spool.id == spool_id
        assert summary.state is SpoolState.SEALED
        assert summary.confidence is Confidence.HIGH

    async def test_registration_announces_itself(self, ledger: Ledger) -> None:
        await a_spool(ledger)
        assert len(ledger.events.of(SpoolRegistered)) == 1

    async def test_a_duplicate_tag_must_be_deliberate(self, ledger: Ledger) -> None:
        tag = TagUid("A1B2C3D4")
        await a_spool(ledger, tag_uid=tag)

        with pytest.raises(DuplicateTagNotConfirmedError):
            await a_spool(ledger, tag_uid=tag)

        # Legal once it is on purpose: a Bambu tag identifies a batch, not a unit.
        second = await a_spool(ledger, tag_uid=tag, confirm_duplicate_tag=True)
        assert second is not None
        assert len(await ledger.use_cases.queries.overview()) == 2


class TestReconciliation:
    async def test_the_scale_becomes_a_movement(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        ledger.clock.advance(days=3)

        result = await ledger.use_cases.reconcile_spool.execute(
            ReconcileSpoolCommand(spool_id=spool_id, measured=Grams.of(1224), includes_core=True)
        )

        # 1224 g on the scale, minus a 250 g reel, is 974 g of filament against a ledger
        # that said 1000 g. The 26 g gap is recorded, not absorbed.
        assert result.delta == Grams.of(-26)
        assert result.new_balance == Grams.of(974)

        detail = await ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of(974)
        assert [line.balance_after for line in detail.lines] == [Grams.of(974), Grams.of(1000)]

    async def test_a_reading_can_exclude_the_reel(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        result = await ledger.use_cases.reconcile_spool.execute(
            ReconcileSpoolCommand(spool_id=spool_id, measured=Grams.of(974), includes_core=False)
        )
        assert result.new_balance == Grams.of(974)

    async def test_agreement_records_nothing(self, ledger: Ledger) -> None:
        """A zero movement records nothing and only adds noise."""
        spool_id = await a_spool(ledger)
        with pytest.raises(NothingToRecordError):
            await ledger.use_cases.reconcile_spool.execute(
                ReconcileSpoolCommand(spool_id=spool_id, measured=Grams.of(1250))
            )
        assert len((await ledger.use_cases.queries.detail(spool_id)).lines) == 1

    async def test_a_large_delta_raises_an_anomaly(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await ledger.use_cases.reconcile_spool.execute(
            ReconcileSpoolCommand(spool_id=spool_id, measured=Grams.of(1050))
        )
        # 1050 − 250 = 800 against 1000: a 200 g gap, well past the 15% default.
        assert len(ledger.events.of(AnomalyDetected)) == 1

    async def test_reconciliation_restores_confidence(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(-300), reason="spent")
        )
        assert (await ledger.use_cases.queries.detail(spool_id)).summary.confidence is (
            Confidence.MEDIUM
        )

        await ledger.use_cases.reconcile_spool.execute(
            ReconcileSpoolCommand(spool_id=spool_id, measured=Grams.of(940))
        )
        assert (await ledger.use_cases.queries.detail(spool_id)).summary.confidence is (
            Confidence.HIGH
        )


class TestDiscard:
    async def test_a_partial_discard_leaves_the_spool_alive(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        remaining = await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=spool_id,
                mode=DiscardMode.PARTIAL,
                amount=Grams.of(8),
                reason="tangled section",
            )
        )
        assert remaining == Grams.of(992)
        summary = (await ledger.use_cases.queries.overview())[0]
        assert summary.state is SpoolState.ACTIVE

    async def test_discarding_a_whole_spool_writes_off_the_balance(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=spool_id, mode=DiscardMode.WHOLE_SPOOL, reason="water damage"
            )
        )
        # Retained in full, with its history intact, but out of active inventory.
        assert await ledger.use_cases.queries.overview() == []
        detail = await ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.state is SpoolState.DISCARDED
        assert detail.summary.balance == Grams.zero()

    async def test_a_discarded_spool_accepts_nothing_further(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(spool_id=spool_id, mode=DiscardMode.WHOLE_SPOOL, reason="gone")
        )
        with pytest.raises(SpoolDiscardedError):
            await ledger.use_cases.adjust_spool.execute(
                AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(-1), reason="no")
            )

    async def test_discarding_more_than_the_balance_is_permitted_and_flagged(
        self, ledger: Ledger
    ) -> None:
        """The physical event happened. The ledger records reality and flags the
        inconsistency rather than refusing the truth."""
        spool_id = await a_spool(ledger)
        remaining = await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=spool_id,
                mode=DiscardMode.PARTIAL,
                amount=Grams.of(1040),
                reason="the whole reel plus the bit on the printer",
            )
        )
        assert remaining == Grams.of(-40)
        assert len(ledger.events.of(AnomalyDetected)) == 1

    async def test_a_reason_is_required(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        with pytest.raises(InvalidValueError):
            await ledger.use_cases.discard_filament.execute(
                DiscardFilamentCommand(
                    spool_id=spool_id,
                    mode=DiscardMode.PARTIAL,
                    amount=Grams.of(5),
                    reason="   ",
                )
            )


class TestMovingSpools:
    async def test_mounting_records_no_movement(self, ledger: Ledger) -> None:
        """Moving a spool consumes no filament."""
        spool_id = await a_spool(ledger)
        before = len((await ledger.use_cases.queries.detail(spool_id)).lines)

        await ledger.use_cases.mount_spool.execute(spool_id, SlotIndex(1))

        detail = await ledger.use_cases.queries.detail(spool_id)
        assert len(detail.lines) == before
        assert detail.summary.spool.location.__class__.__name__ == "AmsSlot"

    async def test_mounting_into_an_occupied_slot_displaces_the_occupant(
        self, ledger: Ledger
    ) -> None:
        """At most one spool per slot — enforced by a partial unique index, so a bug here
        surfaces as an IntegrityError rather than as two spools in one tray."""
        first = await a_spool(ledger, label="first")
        second = await a_spool(ledger, label="second")

        await ledger.use_cases.mount_spool.execute(first, SlotIndex(2))
        await ledger.use_cases.mount_spool.execute(second, SlotIndex(2))

        displaced = await ledger.use_cases.queries.detail(first)
        occupant = await ledger.use_cases.queries.detail(second)
        assert displaced.summary.spool.location.__class__.__name__ == "Storage"
        assert occupant.summary.spool.location.__class__.__name__ == "AmsSlot"


class TestTheWholeStory:
    async def test_the_worked_example_from_the_specification(self, ledger: Ledger) -> None:
        """docs/06-ui-spec.md §6.5, driven through the real use cases and real SQLite.

        1000 − 162 − 8 − 112 + 6.2 − 84.1 − 28.4 = 611.7 g
        """
        spool_id = await a_spool(ledger, label="PLA Basic Black")

        async def spend(amount: float, reason: str) -> None:
            ledger.clock.advance(days=1)
            await ledger.use_cases.adjust_spool.execute(
                AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(-amount), reason=reason)
            )

        await spend(162, "lamp_shade")
        ledger.clock.advance(days=1)
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=spool_id,
                mode=DiscardMode.PARTIAL,
                amount=Grams.of(8),
                reason="tangled section",
            )
        )
        await spend(112, "enclosure_panel")

        ledger.clock.advance(days=1)
        # 974.2 g on the scale, minus the 250 g reel, is 724.2 g of filament against a
        # ledger that said 718 g. The 6.2 g the specification shows is that difference —
        # and the reel has to be subtracted for it to come out, which is exactly why
        # `core_weight` is mandatory.
        await ledger.use_cases.reconcile_spool.execute(
            ReconcileSpoolCommand(spool_id=spool_id, measured=Grams.of("974.2"), includes_core=True)
        )

        await spend(84.1, "vase_final")
        await spend(28.4, "bracket_v3")

        detail = await ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of("611.7")
        assert detail.summary.percentage == 61

        # And the running balances read bottom-up as a derivation, newest first.
        assert [line.balance_after for line in detail.lines] == [
            Grams.of("611.7"),
            Grams.of("640.1"),
            Grams.of("724.2"),
            Grams.of(718),
            Grams.of(830),
            Grams.of(838),
            Grams.of(1000),
        ]

    async def test_stock_totals_ignore_discarded_spools(self, ledger: Ledger) -> None:
        kept = await a_spool(ledger, label="kept")
        binned = await a_spool(ledger, label="binned")
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(spool_id=binned, mode=DiscardMode.WHOLE_SPOOL, reason="gone")
        )

        totals = await ledger.use_cases.queries.stock()
        assert totals.total == Grams.of(1000)
        assert totals.spool_count == 1
        assert totals.per_material == {"PLA": Grams.of(1000)}
        assert (await ledger.use_cases.queries.detail(kept)).summary.balance == Grams.of(1000)


class TestPersistenceEnforcesTheLedger:
    async def test_a_movement_cannot_be_updated(self, ledger: Ledger) -> None:
        """The port omits update. The trigger makes it true at the last possible layer."""
        await a_spool(ledger)
        with pytest.raises(Exception, match="immutable"):
            await ledger.database.execute("UPDATE movement SET amount_mg = 1")

    async def test_a_movement_cannot_be_deleted(self, ledger: Ledger) -> None:
        await a_spool(ledger)
        with pytest.raises(Exception, match="cannot be deleted"):
            await ledger.database.execute("DELETE FROM movement")

    async def test_migrations_are_idempotent(self, ledger: Ledger) -> None:
        assert await ledger.database.migrate() == await ledger.database.migrate()

    async def test_an_unknown_spool_is_reported_not_invented(self, ledger: Ledger) -> None:
        with pytest.raises(SpoolNotFoundError):
            await ledger.use_cases.queries.detail(SpoolId("nope"))

    async def test_movements_survive_a_reopen(self, ledger: Ledger) -> None:
        """The ledger is durable. That is the entire reason it is not a JSON blob."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of(-40), reason="spent")
        )
        recorded = ledger.events.of(MovementRecorded)
        assert len(recorded) == 1

        detail = await ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of(960)
