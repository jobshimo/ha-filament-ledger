"""UC-05 / UC-06 / UC-07 on real SQLite: the queue that refuses to guess.

The printer gateway does not classify job endings yet; these tests drive
`OpenPendingReview` with the `PrintJob` and `ReviewReason` it will deliver — the behaviour
pinned before the adapter is written, against the same schema and constraints production
runs on, exactly as `test_detection.py` did for trays.
"""

from __future__ import annotations

import asyncio

import pytest

from custom_components.filament_ledger.application.adjust_spool import (
    DiscardFilamentCommand,
    DiscardMode,
)
from custom_components.filament_ledger.application.errors import ReviewNotFoundError
from custom_components.filament_ledger.application.reconcile_spool import ReconcileSpoolCommand
from custom_components.filament_ledger.application.register_spool import RegisterSpoolCommand
from custom_components.filament_ledger.application.review_queue import (
    ApproveReviewCommand,
    DismissReviewCommand,
    OpenPendingReviewCommand,
)
from custom_components.filament_ledger.domain.error import (
    InvalidValueError,
    ReviewAlreadyPendingError,
    ReviewAlreadyResolvedError,
    SpoolDiscardedError,
    SpoolReconciledSinceReviewError,
    UnresolvedSlotError,
)
from custom_components.filament_ledger.domain.event import (
    AnomalyDetected,
    MovementRecorded,
    ReviewOpened,
    ReviewResolved,
    SpoolDepleted,
)
from custom_components.filament_ledger.domain.model.pending_review import (
    PendingReview,
    ReviewCharge,
)
from custom_components.filament_ledger.domain.model.print_job import PrintJob
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.confidence import Confidence
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    PrintJobId,
    ReviewId,
    SpoolId,
    TrayRef,
)
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.percentage import Percentage
from custom_components.filament_ledger.domain.value.print_job_state import PrintJobState
from custom_components.filament_ledger.domain.value.review import (
    EstimatorKind,
    ReviewReason,
    ReviewState,
)
from custom_components.filament_ledger.infrastructure.persistence.print_job_repository import (
    SqlitePrintJobRepository,
)
from custom_components.filament_ledger.infrastructure.persistence.review_repository import (
    SqliteReviewRepository,
)

from .conftest import EPOCH, Ledger, a_tray

TRAY_1 = a_tray(1)
TRAY_2 = a_tray(2)


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


def a_job(
    job_id: str = "job-1",
    *,
    layer_reached: int | None = 71,
    total_layers: int | None = 209,
    reported_usage: dict[TrayRef, Grams] | None = None,
    state: PrintJobState = PrintJobState.CANCELLED,
) -> PrintJob:
    return PrintJob(
        id=PrintJobId(job_id),
        name="bracket_v3.gcode.3mf",
        state=state,
        started_at=EPOCH,
        layer_reached=layer_reached,
        total_layers=total_layers,
        reported_usage=reported_usage,
        raw_gcode_state="PAUSE",
        raw_print_error=83935234,
    )


async def opened(
    ledger: Ledger,
    job: PrintJob,
    *,
    reason: ReviewReason = ReviewReason.CANCELLED,
    amounts: dict[TrayRef, Grams] | None = None,
) -> ReviewId:
    return await ledger.use_cases.open_pending_review.execute(
        OpenPendingReviewCommand(job=job, reason=reason, amounts=amounts)
    )


async def stored_review(ledger: Ledger, review_id: ReviewId) -> PendingReview:
    review = await SqliteReviewRepository(ledger.database).get(review_id)
    assert review is not None
    return review


async def estimated_consumption_rows(ledger: Ledger) -> list[dict[str, object]]:
    rows = await ledger.database.fetch_all(
        "SELECT spool_id, amount_mg, source, job_id, review_id FROM movement "
        "WHERE type = 'ESTIMATED_CONSUMPTION' ORDER BY rowid"
    )
    return [dict(row) for row in rows]


class TestOpeningAReview:
    async def test_an_interrupted_print_becomes_a_pending_item(self, ledger: Ledger) -> None:
        """UC-05 end to end: estimate frozen, resolution frozen, nothing deducted."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        job = a_job(reported_usage={TRAY_1: Grams.of(209)})

        review_id = await opened(ledger, job)

        review = await stored_review(ledger, review_id)
        assert review.state is ReviewState.PENDING
        assert review.reason is ReviewReason.CANCELLED
        # 71 of 209 layers of a 209 g plan: 71 g, frozen to the mounted spool as one
        # charge for the whole estimate.
        assert review.estimated_usage == {TRAY_1: Grams.of(71)}
        assert review.charges == [(TRAY_1, ReviewCharge(spool_id, Grams.of(71)))]
        assert review.estimator_used is EstimatorKind.LINEAR_PROGRESS
        # No movement. No balance changed. That is the whole point of PENDING.
        assert (await ledger.use_cases.queries.detail(spool_id)).summary.balance == Grams.of(1000)
        [event] = ledger.events.of(ReviewOpened)
        assert isinstance(event, ReviewOpened)
        assert event.review_id == review_id
        assert event.job_name == "bracket_v3.gcode.3mf"
        assert event.reason is ReviewReason.CANCELLED

    async def test_the_job_row_lands_with_the_review(self, ledger: Ledger) -> None:
        """`pending_review.job_id` has a foreign key; the use case saves the job first so
        the ordering is impossible to get wrong from any caller."""
        job = a_job(reported_usage={TRAY_1: Grams.of(209)})

        await opened(ledger, job)

        assert await SqlitePrintJobRepository(ledger.database).get(job.id) == job

    async def test_estimation_unavailable_still_opens_with_an_explicit_flag(
        self, ledger: Ledger
    ) -> None:
        """A zero estimate plus `NONE` — the user is asked; nothing is guessed."""
        job = a_job(
            layer_reached=None,
            total_layers=None,
            reported_usage={TRAY_1: Grams.of(120), TRAY_2: Grams.of(30)},
        )

        review = await stored_review(ledger, await opened(ledger, job))

        assert review.estimated_usage == {TRAY_1: Grams.zero(), TRAY_2: Grams.zero()}
        assert review.estimator_used is EstimatorKind.NONE

    async def test_no_data_at_all_opens_an_empty_review(self, ledger: Ledger) -> None:
        """Even the slots are unknown. The review still documents that a loss happened —
        the one failure that must not vanish without a trace."""
        job = a_job(layer_reached=None, total_layers=None, reported_usage=None)

        review = await stored_review(ledger, await opened(ledger, job))

        assert review.lines == ()
        assert review.estimator_used is EstimatorKind.NONE

    async def test_caller_supplied_amounts_skip_the_estimator(self, ledger: Ledger) -> None:
        """UC-04's channel: the printer reported figures, and estimating over a report
        would replace a fact with a guess. The estimator would have said 71 g here."""
        job = a_job(reported_usage={TRAY_1: Grams.of(209)})

        review = await stored_review(
            ledger,
            await opened(
                ledger,
                job,
                reason=ReviewReason.UNMAPPED_USAGE,
                amounts={TRAY_1: Grams.of("12.1")},
            ),
        )

        assert review.estimated_usage == {TRAY_1: Grams.of("12.1")}
        assert review.estimator_used is EstimatorKind.NONE

    async def test_an_unoccupied_slot_freezes_as_unresolved(self, ledger: Ledger) -> None:
        """A fact worth recording, not an error — this is the case the queue exists for."""
        job = a_job(reported_usage={TRAY_1: Grams.of(209)})

        review = await stored_review(ledger, await opened(ledger, job))

        assert review.charges == []

    async def test_the_ambient_channel_refuses_to_estimate(self, ledger: Ledger) -> None:
        """`open_within_unit` runs while the caller holds the ledger's one write lock,
        where estimation — file I/O in Phase 4 — must never happen. Amounts are mandatory
        there: refused outright, not documented and hoped for."""
        with pytest.raises(InvalidValueError):
            await ledger.use_cases.open_pending_review.open_within_unit(
                OpenPendingReviewCommand(
                    job=a_job(reported_usage={TRAY_1: Grams.of(209)}),
                    reason=ReviewReason.UNMAPPED_USAGE,
                )
            )

        assert await SqliteReviewRepository(ledger.database).list_pending() == []

    async def test_a_job_cannot_hold_two_open_reviews(self, ledger: Ledger) -> None:
        job = a_job(reported_usage={TRAY_1: Grams.of(209)})
        await opened(ledger, job)

        with pytest.raises(ReviewAlreadyPendingError):
            await opened(ledger, job)

        assert len(await SqliteReviewRepository(ledger.database).list_pending()) == 1

    async def test_a_resolved_review_clears_the_way_for_a_new_one(self, ledger: Ledger) -> None:
        """The index is partial on PENDING: history accumulates, doubt does not."""
        job = a_job(reported_usage={TRAY_1: Grams.of(209)})
        first = await opened(ledger, job)
        await ledger.use_cases.dismiss_review.execute(DismissReviewCommand(review_id=first))

        second = await opened(ledger, job)

        assert second != first
        assert (await stored_review(ledger, second)).state is ReviewState.PENDING

    async def test_the_database_enforces_the_same_rule_at_the_last_layer(
        self, ledger: Ledger
    ) -> None:
        """`idx_review_job_pending`, checked the way `test_ledger` checks the triggers:
        the use case says it first, in the language of the problem; the index makes it
        true against any writer."""
        job = a_job(reported_usage={TRAY_1: Grams.of(209)})
        await opened(ledger, job)

        with pytest.raises(Exception, match=r"UNIQUE constraint failed: pending_review\.job_id"):
            await ledger.database.execute(
                "INSERT INTO pending_review (id, job_id, reason, estimated_usage, "
                "slot_resolution, estimator_used, state, opened_at) "
                "VALUES ('rogue', 'job-1', 'CANCELLED', '{}', '[]', 'NONE', 'PENDING', "
                "'2026-08-02T12:00:00+00:00')"
            )


class TestApprovingAReview:
    async def test_approval_turns_the_estimate_into_a_traceable_movement(
        self, ledger: Ledger
    ) -> None:
        """UC-06 happy path: one ESTIMATED_CONSUMPTION per non-zero slot, carrying both
        `job_id` and `review_id` — and the balance now rests on an estimate, which is
        exactly what LOW confidence means."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        review_id = await opened(ledger, a_job(reported_usage={TRAY_1: Grams.of(209)}))
        ledger.clock.advance(days=2)

        await ledger.use_cases.approve_review.execute(
            ApproveReviewCommand(review_id=review_id, note="looks right")
        )

        [row] = await estimated_consumption_rows(ledger)
        assert row["spool_id"] == spool_id
        assert row["amount_mg"] == -71000
        assert row["source"] == "USER_CONFIRMED"
        assert row["job_id"] == "job-1"
        assert row["review_id"] == review_id

        detail = await ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of(929)
        assert detail.summary.confidence is Confidence.LOW
        # And the badge can say which of the two routes to LOW was taken. 71 g is nowhere
        # near the consumption rung, so only the estimate explains this one — and the card
        # names the day it was approved rather than the day the spool was registered.
        basis = detail.summary.confidence_basis
        assert basis.estimates_since == 1
        assert basis.latest_estimate_at == ledger.clock.now()
        assert basis.consumed_since == Grams.of(71)

        review = await stored_review(ledger, review_id)
        assert review.state is ReviewState.APPROVED
        assert review.resolved_at is not None
        assert review.resolution_note == "looks right"

        [recorded] = ledger.events.of(MovementRecorded)
        assert isinstance(recorded, MovementRecorded)
        assert recorded.new_balance == Grams.of(929)
        [resolved] = ledger.events.of(ReviewResolved)
        assert isinstance(resolved, ReviewResolved)
        assert resolved.state is ReviewState.APPROVED

    async def test_the_users_number_overrides_the_estimate(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        review_id = await opened(ledger, a_job(reported_usage={TRAY_1: Grams.of(209)}))

        await ledger.use_cases.approve_review.execute(
            ApproveReviewCommand(review_id=review_id, amounts={TRAY_1: Grams.of(31)})
        )

        detail = await ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of(969)
        review = await stored_review(ledger, review_id)
        assert review.confirmed_usage == {TRAY_1: Grams.of(31)}
        assert review.estimated_usage == {TRAY_1: Grams.of(71)}

    async def test_an_unresolved_nonzero_slot_blocks_and_nothing_is_written(
        self, ledger: Ledger
    ) -> None:
        """Refused rather than rounded: inventing a spool or dropping a real consumption
        are the two failures this project exists to prevent."""
        review_id = await opened(ledger, a_job(reported_usage={TRAY_1: Grams.of(209)}))

        with pytest.raises(UnresolvedSlotError):
            await ledger.use_cases.approve_review.execute(ApproveReviewCommand(review_id=review_id))

        assert await estimated_consumption_rows(ledger) == []
        assert (await stored_review(ledger, review_id)).state is ReviewState.PENDING
        assert ledger.events.of(ReviewResolved) == []

    async def test_an_assignment_supplies_the_missing_spool(self, ledger: Ledger) -> None:
        """The user is one dropdown away from the answer; this is that dropdown."""
        spool_id = await a_spool(ledger)
        review_id = await opened(ledger, a_job(reported_usage={TRAY_1: Grams.of(209)}))

        await ledger.use_cases.approve_review.execute(
            ApproveReviewCommand(review_id=review_id, assignments={TRAY_1: spool_id})
        )

        [row] = await estimated_consumption_rows(ledger)
        assert row["spool_id"] == spool_id
        assert (await stored_review(ledger, review_id)).charges == [
            (TRAY_1, ReviewCharge(spool_id, Grams.of(71)))
        ]

    async def test_a_split_tray_deducts_from_both_spools_in_one_approval(
        self, ledger: Ledger
    ) -> None:
        """A spool emptied mid-print and was replaced in the same tray. The printer
        reported one figure; it belongs to two spools, and one decision says so."""
        emptied = await a_spool(ledger)
        replacement = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(emptied, TRAY_1)
        review_id = await opened(ledger, a_job(reported_usage={TRAY_1: Grams.of(209)}))

        await ledger.use_cases.approve_review.execute(
            ApproveReviewCommand(
                review_id=review_id,
                charges={
                    TRAY_1: (
                        ReviewCharge(emptied, Grams.of(11)),
                        ReviewCharge(replacement, Grams.of(60)),
                    )
                },
            )
        )

        rows = await estimated_consumption_rows(ledger)
        assert [(row["spool_id"], row["amount_mg"]) for row in rows] == [
            (emptied, -11000),
            (replacement, -60000),
        ]
        # Both legs name the print, so per-print accounting still follows the material.
        assert {row["job_id"] for row in rows} == {"job-1"}
        assert {row["review_id"] for row in rows} == {review_id}
        assert (await ledger.use_cases.queries.detail(emptied)).summary.balance == Grams.of(989)
        assert (await ledger.use_cases.queries.detail(replacement)).summary.balance == Grams.of(940)

    async def test_a_split_that_leaves_grams_unattributed_writes_nothing(
        self, ledger: Ledger
    ) -> None:
        """The remainder came off something. Accepting the shortfall would lose it."""
        emptied = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(emptied, TRAY_1)
        review_id = await opened(ledger, a_job(reported_usage={TRAY_1: Grams.of(209)}))

        with pytest.raises(UnresolvedSlotError, match="must add up"):
            await ledger.use_cases.approve_review.execute(
                ApproveReviewCommand(
                    review_id=review_id,
                    charges={TRAY_1: (ReviewCharge(emptied, Grams.of(11)),)},
                )
            )

        assert await estimated_consumption_rows(ledger) == []
        assert (await stored_review(ledger, review_id)).state is ReviewState.PENDING

    async def test_a_double_click_cannot_deduct_twice(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        review_id = await opened(ledger, a_job(reported_usage={TRAY_1: Grams.of(209)}))
        command = ApproveReviewCommand(review_id=review_id)
        await ledger.use_cases.approve_review.execute(command)

        with pytest.raises(ReviewAlreadyResolvedError):
            await ledger.use_cases.approve_review.execute(command)

        assert len(await estimated_consumption_rows(ledger)) == 1
        assert (await ledger.use_cases.queries.detail(spool_id)).summary.balance == Grams.of(929)

    async def test_zero_amount_slots_are_skipped_entirely(self, ledger: Ledger) -> None:
        """A zero movement records nothing and only adds noise — and the schema's
        `amount_mg != 0` CHECK would refuse it anyway. Approving a no-data review is
        legal and writes nothing."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        job = a_job(layer_reached=None, total_layers=None, reported_usage={TRAY_1: Grams.of(120)})
        review_id = await opened(ledger, job)

        await ledger.use_cases.approve_review.execute(ApproveReviewCommand(review_id=review_id))

        assert await estimated_consumption_rows(ledger) == []
        assert (await stored_review(ledger, review_id)).state is ReviewState.APPROVED
        [resolved] = ledger.events.of(ReviewResolved)
        assert isinstance(resolved, ReviewResolved)
        assert resolved.state is ReviewState.APPROVED

    async def test_an_estimate_past_the_balance_is_recorded_and_flagged(
        self, ledger: Ledger
    ) -> None:
        """The physical event happened; the ledger records reality and flags the
        inconsistency rather than refusing the truth."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        review_id = await opened(
            ledger,
            a_job(reported_usage=None),
            reason=ReviewReason.UNMAPPED_USAGE,
            amounts={TRAY_1: Grams.of(1100)},
        )

        await ledger.use_cases.approve_review.execute(ApproveReviewCommand(review_id=review_id))

        detail = await ledger.use_cases.queries.detail(spool_id)
        assert detail.summary.balance == Grams.of(-100)
        assert len(ledger.events.of(AnomalyDetected)) == 1
        assert len(ledger.events.of(SpoolDepleted)) == 1

    async def test_a_spool_discarded_while_the_review_waited_blocks_approval(
        self, ledger: Ledger
    ) -> None:
        """Discarding wrote off the whole balance; charging the estimate afterwards would
        count the loss twice. The user zeroes the amount, reassigns, or dismisses."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        review_id = await opened(ledger, a_job(reported_usage={TRAY_1: Grams.of(209)}))
        await ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(
                spool_id=spool_id, mode=DiscardMode.WHOLE_SPOOL, reason="water damage"
            )
        )

        with pytest.raises(SpoolDiscardedError):
            await ledger.use_cases.approve_review.execute(ApproveReviewCommand(review_id=review_id))

        assert await estimated_consumption_rows(ledger) == []
        assert (await stored_review(ledger, review_id)).state is ReviewState.PENDING

    async def test_a_spool_weighed_while_the_review_waited_blocks_approval(
        self, ledger: Ledger
    ) -> None:
        """The scale is ground truth and it already counted this print: the reconciliation
        set the balance to the measured 916 g, so charging the 84 g estimate on top would
        deduct the same grams twice. The measurement stands and the review stays open —
        dismissing it is the user's decision, not the system's."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        job = a_job(layer_reached=84, total_layers=200, reported_usage={TRAY_1: Grams.of(200)})
        review_id = await opened(ledger, job)
        ledger.clock.advance(days=1)
        weighing = await ledger.use_cases.reconcile_spool.execute(
            ReconcileSpoolCommand(spool_id=spool_id, measured=Grams.of(916), includes_core=False)
        )
        assert weighing.delta == Grams.of(-84)

        with pytest.raises(SpoolReconciledSinceReviewError):
            await ledger.use_cases.approve_review.execute(ApproveReviewCommand(review_id=review_id))

        assert await estimated_consumption_rows(ledger) == []
        assert (await ledger.use_cases.queries.detail(spool_id)).summary.balance == Grams.of(916)
        assert (await stored_review(ledger, review_id)).state is ReviewState.PENDING

    async def test_a_weighing_from_before_the_review_leaves_the_approval_alone(
        self, ledger: Ledger
    ) -> None:
        """Only a measurement taken after the print can contain it. An older reconciliation
        says nothing about consumption that had not happened yet."""
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        await ledger.use_cases.reconcile_spool.execute(
            ReconcileSpoolCommand(spool_id=spool_id, measured=Grams.of(990), includes_core=False)
        )
        ledger.clock.advance(days=1)
        review_id = await opened(ledger, a_job(reported_usage={TRAY_1: Grams.of(209)}))

        await ledger.use_cases.approve_review.execute(ApproveReviewCommand(review_id=review_id))

        assert len(await estimated_consumption_rows(ledger)) == 1
        assert (await ledger.use_cases.queries.detail(spool_id)).summary.balance == Grams.of(919)

    async def test_an_unknown_review_is_reported_not_invented(self, ledger: Ledger) -> None:
        with pytest.raises(ReviewNotFoundError):
            await ledger.use_cases.approve_review.execute(
                ApproveReviewCommand(review_id=ReviewId("nope"))
            )


class TestFrozenResolution:
    async def test_the_deduction_survives_a_spool_swap(self, ledger: Ledger) -> None:
        """The rationale for freezing at open: a review may sit for days while spools are
        swapped, and approval must charge the spool that was there on Tuesday — not
        whatever happens to be in slot 2 on Friday."""
        tuesday_spool = await a_spool(ledger, label="tuesday")
        await ledger.use_cases.mount_spool.execute(tuesday_spool, TRAY_2)
        review_id = await opened(ledger, a_job(reported_usage={TRAY_2: Grams.of(209)}))

        friday_spool = await a_spool(ledger, label="friday")
        await ledger.use_cases.mount_spool.execute(friday_spool, TRAY_2)
        ledger.clock.advance(days=3)

        await ledger.use_cases.approve_review.execute(ApproveReviewCommand(review_id=review_id))

        [row] = await estimated_consumption_rows(ledger)
        assert row["spool_id"] == tuesday_spool
        friday = await ledger.use_cases.queries.detail(friday_spool)
        assert friday.summary.balance == Grams.of(1000)


class TestConcurrency:
    """These run on `interleaved_ledger`, whose executor yields to the event loop before
    every statement — `run_inline` never yields, so a race could not be observed with it."""

    async def test_approval_and_dismissal_racing_resolve_exactly_once(
        self, interleaved_ledger: Ledger
    ) -> None:
        """A double-decision race: the panel approves while an automation dismisses.

        Unserialised, both would read PENDING, one would deduct and the other would mark
        dismissed — a review claiming both outcomes, or a deduction a dismissal then
        disowns. One decision must win and the other must be refused; which one wins is
        scheduling, so the assertion is the invariant, not the winner."""
        ledger = interleaved_ledger
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        review_id = await opened(ledger, a_job(reported_usage={TRAY_1: Grams.of(209)}))

        outcomes = await asyncio.gather(
            ledger.use_cases.approve_review.execute(ApproveReviewCommand(review_id=review_id)),
            ledger.use_cases.dismiss_review.execute(DismissReviewCommand(review_id=review_id)),
            return_exceptions=True,
        )

        refusals = [o for o in outcomes if isinstance(o, ReviewAlreadyResolvedError)]
        assert len(refusals) == 1
        review = await stored_review(ledger, review_id)
        rows = await estimated_consumption_rows(ledger)
        # The ledger agrees with whichever decision landed: an approval deducted exactly
        # once, a dismissal deducted nothing at all.
        if review.state is ReviewState.APPROVED:
            assert len(rows) == 1
            expected = Grams.of(929)
        else:
            assert review.state is ReviewState.DISMISSED
            assert rows == []
            expected = Grams.of(1000)
        assert (await ledger.use_cases.queries.detail(spool_id)).summary.balance == expected
        assert len(ledger.events.of(ReviewResolved)) == 1

    async def test_two_opens_for_one_job_racing_yield_one_review(
        self, interleaved_ledger: Ledger
    ) -> None:
        """Two `OpenPendingReview` for the same job, racing — the gateway can deliver one
        ending twice. The one-pending-per-job rule must hold under interleaving, and the
        loser must be told in the language of the problem, not with a constraint name."""
        ledger = interleaved_ledger
        job = a_job(reported_usage={TRAY_1: Grams.of(209)})

        outcomes = await asyncio.gather(
            opened(ledger, job),
            opened(ledger, job),
            return_exceptions=True,
        )

        winners = [o for o in outcomes if isinstance(o, str)]
        refusals = [o for o in outcomes if isinstance(o, ReviewAlreadyPendingError)]
        assert len(winners) == 1
        assert len(refusals) == 1
        pending = await SqliteReviewRepository(ledger.database).list_pending()
        assert [review.id for review in pending] == winners
        assert len(ledger.events.of(ReviewOpened)) == 1


class TestDecidingAReviewSettlesItsJob:
    """`_settle_job`: approval and dismissal both close the job's consumption question.

    That write is what stops `TrackPrintJob` re-detecting a decided orphan on every
    later start and re-minting its card each time the user resolves it — the loop
    observed live on 2026-08-31 (dismiss, print, the same ghost again).
    """

    async def test_dismissal_marks_the_job_recorded(self, ledger: Ledger) -> None:
        job = a_job(reported_usage={TRAY_1: Grams.of(50)})
        review_id = await opened(ledger, job)

        await ledger.use_cases.dismiss_review.execute(DismissReviewCommand(review_id=review_id))

        stored = await SqlitePrintJobRepository(ledger.database).get(job.id)
        assert stored is not None
        assert stored.consumption_recorded, "dismissal decides: nothing left to record"

    async def test_approval_marks_the_job_recorded(self, ledger: Ledger) -> None:
        spool_id = await ledger.use_cases.register_spool.execute(
            RegisterSpoolCommand(
                material=Material.of(MaterialKind.PLA),
                colour=Colour.parse("000000"),
                opening_weight=Grams.of(1000),
                core_weight=Grams.of(250),
            )
        )
        job = a_job(reported_usage={TRAY_1: Grams.of(50)})
        review_id = await opened(ledger, job)

        await ledger.use_cases.approve_review.execute(
            ApproveReviewCommand(review_id=review_id, assignments={TRAY_1: spool_id})
        )

        stored = await SqlitePrintJobRepository(ledger.database).get(job.id)
        assert stored is not None
        assert stored.consumption_recorded, "approval decides: the grams are recorded"


class TestDismissingAReview:
    async def test_dismissal_records_the_decision_and_moves_nothing(self, ledger: Ledger) -> None:
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        review_id = await opened(ledger, a_job(reported_usage={TRAY_1: Grams.of(209)}))
        ledger.clock.advance(days=1)

        await ledger.use_cases.dismiss_review.execute(
            DismissReviewCommand(review_id=review_id, note="failed on the first layer")
        )

        review = await stored_review(ledger, review_id)
        assert review.state is ReviewState.DISMISSED
        assert review.resolution_note == "failed on the first layer"
        assert review.resolved_at is not None
        assert await estimated_consumption_rows(ledger) == []
        assert (await ledger.use_cases.queries.detail(spool_id)).summary.balance == Grams.of(1000)
        [resolved] = ledger.events.of(ReviewResolved)
        assert isinstance(resolved, ReviewResolved)
        assert resolved.state is ReviewState.DISMISSED

    async def test_a_dismissed_review_stays_dismissed(self, ledger: Ledger) -> None:
        review_id = await opened(ledger, a_job(reported_usage={TRAY_1: Grams.of(209)}))
        await ledger.use_cases.dismiss_review.execute(DismissReviewCommand(review_id=review_id))

        with pytest.raises(ReviewAlreadyResolvedError):
            await ledger.use_cases.dismiss_review.execute(DismissReviewCommand(review_id=review_id))

    async def test_an_unknown_review_cannot_be_dismissed(self, ledger: Ledger) -> None:
        with pytest.raises(ReviewNotFoundError):
            await ledger.use_cases.dismiss_review.execute(
                DismissReviewCommand(review_id=ReviewId("nope"))
            )


class TestPersistedRoundTrip:
    """The mapping, both directions, on the real schema — the fidelity every use case
    above silently depends on."""

    async def test_a_job_survives_with_every_field_intact(self, ledger: Ledger) -> None:
        repository = SqlitePrintJobRepository(ledger.database)
        job = PrintJob(
            id=PrintJobId("job-full"),
            name="calibration_cube.3mf",
            state=PrintJobState.FAILED,
            started_at=EPOCH,
            ended_at=EPOCH,
            layer_reached=4,
            total_layers=60,
            progress=Percentage.of("6.7"),
            reported_usage={TRAY_1: Grams.of("1.9"), TRAY_2: Grams.zero()},
            raw_gcode_state="FAILED",
            raw_print_error=50348044,
            consumption_recorded=True,
        )

        await repository.save(job)

        assert await repository.get(job.id) == job

    async def test_an_empty_usage_report_survives_distinct_from_no_report(
        self, ledger: Ledger
    ) -> None:
        """`None` and `{}` are different facts — a figure that never materialised versus a
        printer that reported and named no trays — and the nullable column keeps them
        apart through a round trip. Collapsing them would turn a retrieval failure into a
        silent claim that nothing was consumed (docs/04-use-cases.md UC-04)."""
        repository = SqlitePrintJobRepository(ledger.database)
        silent = a_job("job-silent", state=PrintJobState.FINISHED, reported_usage=None)
        empty = a_job("job-empty", state=PrintJobState.FINISHED, reported_usage={})
        await repository.save(silent)
        await repository.save(empty)

        reloaded_silent = await repository.get(silent.id)
        reloaded_empty = await repository.get(empty.id)

        assert reloaded_silent is not None
        assert reloaded_silent.reported_usage is None
        assert reloaded_empty is not None
        assert reloaded_empty.reported_usage == {}

    async def test_saving_again_updates_the_same_row(self, ledger: Ledger) -> None:
        """A job's state evolves as the printer reports; the record follows the claim."""
        repository = SqlitePrintJobRepository(ledger.database)
        running = a_job(state=PrintJobState.RUNNING, reported_usage=None)
        await repository.save(running)

        await repository.save(a_job(state=PrintJobState.CANCELLED, reported_usage=None))

        stored = await repository.get(running.id)
        assert stored is not None
        assert stored.state is PrintJobState.CANCELLED
        assert len(await repository.list_recent(10)) == 1

    async def test_recent_jobs_come_newest_first(self, ledger: Ledger) -> None:
        repository = SqlitePrintJobRepository(ledger.database)
        for name, days in (("old", 0), ("new", 2), ("middle", 1)):
            job = PrintJob(
                id=PrintJobId(name),
                name=name,
                state=PrintJobState.FINISHED,
                started_at=EPOCH.replace(day=EPOCH.day + days),
            )
            await repository.save(job)

        recent = await repository.list_recent(2)

        assert [job.name for job in recent] == ["new", "middle"]

    async def test_a_resolved_review_survives_with_its_decision_intact(
        self, ledger: Ledger
    ) -> None:
        spool_id = await a_spool(ledger)
        await ledger.use_cases.mount_spool.execute(spool_id, TRAY_1)
        review_id = await opened(ledger, a_job(reported_usage={TRAY_1: Grams.of(209)}))
        await ledger.use_cases.approve_review.execute(
            ApproveReviewCommand(
                review_id=review_id, amounts={TRAY_1: Grams.of(31)}, note="weighed the waste"
            )
        )

        review = await stored_review(ledger, review_id)

        assert review.state is ReviewState.APPROVED
        assert review.confirmed_usage == {TRAY_1: Grams.of(31)}
        assert review.estimated_usage == {TRAY_1: Grams.of(71)}
        # The single frozen charge followed the corrected amount: with one charge the sum
        # invariant admits exactly one split, so nothing was decided on anybody's behalf.
        assert review.charges == [(TRAY_1, ReviewCharge(spool_id, Grams.of(31)))]
        assert review.estimator_used is EstimatorKind.LINEAR_PROGRESS
        assert review.reason is ReviewReason.CANCELLED
        assert review.opened_at == EPOCH
