"""PendingReview invariants and transitions.

The queue's whole promise is made here: a pending review has moved nothing, an approval
refuses the unresolvable, and a resolved review can never resolve again.
"""

from __future__ import annotations

import dataclasses

import pytest

from custom_components.filament_ledger.domain.error import (
    InvalidValueError,
    ReviewAlreadyResolvedError,
    UnresolvedSlotError,
)
from custom_components.filament_ledger.domain.model.pending_review import (
    PendingReview,
    ReviewCharge,
    ReviewLine,
)
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import SpoolId
from custom_components.filament_ledger.domain.value.review import ReviewState

from .conftest import A_SPOOL_ID, a_charge, a_line, a_pending_review, a_tray, at

ANOTHER_SPOOL = SpoolId("the-spool-assigned-at-approval")
REPLACEMENT = SpoolId("the-spool-that-replaced-it-mid-print")


class TestOpening:
    def test_a_new_review_is_pending_and_has_confirmed_nothing(self) -> None:
        review = a_pending_review()
        assert review.state is ReviewState.PENDING
        assert not review.is_resolved
        assert review.confirmed_usage is None
        assert review.confirmed_charges == []

    def test_identities_are_unique(self) -> None:
        assert a_pending_review().id != a_pending_review().id

    def test_lines_are_kept_sorted_by_slot(self) -> None:
        review = a_pending_review(a_line(3, 12.1), a_line(1, 28.4))
        assert [line.tray for line in review.lines] == [a_tray(1), a_tray(3)]

    def test_the_same_slot_cannot_appear_twice(self) -> None:
        with pytest.raises(InvalidValueError):
            a_pending_review(a_line(2, 1.0), a_line(2, 2.0))

    def test_a_negative_estimate_cannot_exist(self) -> None:
        with pytest.raises(InvalidValueError):
            a_line(1, -28.4)

    def test_an_unresolved_slot_is_a_recordable_fact(self) -> None:
        """No spool mounted is the case the queue exists for — never an error at open."""
        review = a_pending_review(a_line(3, 12.1, spool_id=None))
        assert review.charges == []

    def test_a_mounted_spool_freezes_as_one_charge_for_the_whole_estimate(self) -> None:
        review = a_pending_review(a_line(1, 28.4))
        assert review.charges == [(a_tray(1), ReviewCharge(A_SPOOL_ID, Grams.of(28.4)))]

    def test_a_tray_cannot_charge_the_same_spool_twice(self) -> None:
        """One figure per spool per tray. The same spool twice is one answer written as
        two, and every reader that maps the list back by spool keeps only the last."""
        with pytest.raises(InvalidValueError):
            ReviewLine(
                tray=a_tray(1),
                estimated=Grams.of(300),
                charges=(a_charge(10), a_charge(290)),
            )

    def test_a_negative_charge_cannot_exist(self) -> None:
        with pytest.raises(InvalidValueError):
            ReviewCharge(spool_id=A_SPOOL_ID, amount=Grams.of(-1))

    def test_a_zero_charge_is_legal(self) -> None:
        """The no-data card's row: the spool is known, the figure never arrived. Refusing
        it would make that row unrepresentable (docs/06 §6.3)."""
        line = ReviewLine(tray=a_tray(1), estimated=Grams.zero(), charges=(a_charge(0),))
        assert line.attributed == Grams.zero()


class TestContradictions:
    def test_a_pending_review_cannot_carry_a_resolution_timestamp(self) -> None:
        with pytest.raises(InvalidValueError):
            dataclasses.replace(a_pending_review(), resolved_at=at(days=1))

    def test_a_resolved_review_cannot_lack_one(self) -> None:
        with pytest.raises(InvalidValueError):
            dataclasses.replace(a_pending_review(), state=ReviewState.DISMISSED)


class TestApproval:
    def test_untouched_estimates_become_the_confirmed_amounts(self) -> None:
        review = a_pending_review(a_line(1, 28.4))

        approved = review.approved(at=at(days=1), note="looks right")

        assert approved.state is ReviewState.APPROVED
        assert approved.resolved_at == at(days=1)
        assert approved.resolution_note == "looks right"
        assert approved.confirmed_usage == {a_tray(1): Grams.of(28.4)}

    def test_the_users_number_always_wins(self) -> None:
        """The estimate is a starting value, never a fixed one (docs/06-ui-spec.md §6.3)."""
        review = a_pending_review(a_line(1, 28.4))

        approved = review.approved(at=at(days=1), amounts={a_tray(1): Grams.of(31.0)})

        assert approved.confirmed_usage == {a_tray(1): Grams.of(31.0)}
        # The proposal survives per line; only the decision changed.
        assert approved.estimated_usage == {a_tray(1): Grams.of(28.4)}

    def test_an_assignment_resolves_a_frozen_unresolved_slot(self) -> None:
        """The one-spool shorthand: the named spool takes the tray whole, so the caller
        supplies a spool rather than restating arithmetic."""
        review = a_pending_review(a_line(3, 12.1, spool_id=None))

        approved = review.approved(at=at(days=1), assignments={a_tray(3): ANOTHER_SPOOL})

        assert approved.charges == [(a_tray(3), ReviewCharge(ANOTHER_SPOOL, Grams.of(12.1)))]
        assert approved.confirmed_charges == [(a_tray(3), Grams.of(12.1), ANOTHER_SPOOL)]

    def test_a_nonzero_amount_without_a_spool_blocks_the_whole_approval(self) -> None:
        review = a_pending_review(a_line(1, 28.4), a_line(3, 12.1, spool_id=None))

        with pytest.raises(UnresolvedSlotError):
            review.approved(at=at(days=1))

    def test_zeroing_the_amount_unblocks_an_unresolved_slot(self) -> None:
        """The user's third option beside assigning and dismissing: declare the slot moot."""
        review = a_pending_review(a_line(3, 12.1, spool_id=None))

        approved = review.approved(at=at(days=1), amounts={a_tray(3): Grams.zero()})

        assert approved.confirmed_charges == []

    def test_a_zero_estimate_on_an_unresolved_slot_needs_no_decision(self) -> None:
        """The no-data placeholder: zero amount, no spool, and approval passes it by."""
        review = a_pending_review(a_line(2, 0, spool_id=None))
        assert review.approved(at=at(days=1)).confirmed_charges == []

    def test_a_negative_correction_is_refused(self) -> None:
        review = a_pending_review(a_line(1, 28.4))
        with pytest.raises(InvalidValueError):
            review.approved(at=at(days=1), amounts={a_tray(1): Grams.of(-1)})

    def test_an_amount_for_a_slot_the_review_never_covered_is_a_caller_bug(self) -> None:
        review = a_pending_review(a_line(1, 28.4))
        with pytest.raises(InvalidValueError):
            review.approved(at=at(days=1), amounts={a_tray(4): Grams.of(5)})

    def test_an_assignment_for_an_uncovered_slot_is_equally_refused(self) -> None:
        review = a_pending_review(a_line(1, 28.4))
        with pytest.raises(InvalidValueError):
            review.approved(at=at(days=1), assignments={a_tray(4): ANOTHER_SPOOL})

    def test_charges_skip_zero_amounts_and_keep_slot_order(self) -> None:
        review = a_pending_review(
            a_line(1, 28.4),
            a_line(2, 0),
            a_line(3, 6.1, spool_id=ANOTHER_SPOOL),
        )

        approved = review.approved(at=at(days=1))

        assert approved.confirmed_charges == [
            (a_tray(1), Grams.of(28.4), A_SPOOL_ID),
            (a_tray(3), Grams.of(6.1), ANOTHER_SPOOL),
        ]


class TestATrayThatFedFromTwoSpools:
    """The situation the entity exists to be able to hold: a spool empties mid-print and
    is replaced in the same tray. The printer reports one figure for that tray, and it
    belongs to two spools (docs/02 §2.3)."""

    def test_charges_split_one_trays_amount_across_two_spools(self) -> None:
        review = a_pending_review(a_line(1, 300))

        approved = review.approved(
            at=at(days=1),
            charges={a_tray(1): (a_charge(10), a_charge(290, REPLACEMENT))},
        )

        assert approved.confirmed_usage == {a_tray(1): Grams.of(300)}
        assert approved.confirmed_charges == [
            (a_tray(1), Grams.of(10), A_SPOOL_ID),
            (a_tray(1), Grams.of(290), REPLACEMENT),
        ]

    def test_a_split_that_does_not_add_up_is_refused_and_says_by_how_much(self) -> None:
        """*Load the rest* is a subtraction precisely because this is refused: the other
        290 g came off something, and accepting the shortfall would lose them silently."""
        review = a_pending_review(a_line(1, 300))

        with pytest.raises(UnresolvedSlotError, match=r"confirms 300\.0 g and charges 10\.0 g"):
            review.approved(at=at(days=1), charges={a_tray(1): (a_charge(10),)})

    def test_a_split_that_overshoots_is_refused_the_same_way(self) -> None:
        review = a_pending_review(a_line(1, 300))

        with pytest.raises(UnresolvedSlotError):
            review.approved(
                at=at(days=1),
                charges={a_tray(1): (a_charge(200), a_charge(200, REPLACEMENT))},
            )

    def test_the_split_follows_a_corrected_amount(self) -> None:
        """The user weighed the waste and split what they weighed, in one decision."""
        review = a_pending_review(a_line(1, 300))

        approved = review.approved(
            at=at(days=1),
            amounts={a_tray(1): Grams.of(250)},
            charges={a_tray(1): (a_charge(60), a_charge(190, REPLACEMENT))},
        )

        assert approved.confirmed_usage == {a_tray(1): Grams.of(250)}

    def test_a_tray_cannot_carry_an_assignment_and_a_split_at_once(self) -> None:
        """Two answers to one question. Letting one win silently is how a user's second
        thought gets discarded."""
        review = a_pending_review(a_line(1, 300))

        with pytest.raises(InvalidValueError, match="two answers to one question"):
            review.approved(
                at=at(days=1),
                assignments={a_tray(1): REPLACEMENT},
                charges={a_tray(1): (a_charge(300),)},
            )

    def test_a_charge_list_for_an_uncovered_slot_is_a_caller_bug(self) -> None:
        review = a_pending_review(a_line(1, 28.4))
        with pytest.raises(InvalidValueError):
            review.approved(at=at(days=1), charges={a_tray(4): (a_charge(5),)})

    def test_an_amount_alone_cannot_rescale_a_split_somebody_else_decided(self) -> None:
        """With one charge the invariant admits exactly one split, so the charge follows.
        With two it admits many, and choosing one would be the system deciding."""
        split = ReviewLine(
            tray=a_tray(1),
            estimated=Grams.of(300),
            charges=(a_charge(10), a_charge(290, REPLACEMENT)),
        )
        review = a_pending_review(split)

        with pytest.raises(UnresolvedSlotError):
            review.approved(at=at(days=1), amounts={a_tray(1): Grams.of(250)})


class TestDismissal:
    def test_dismissal_is_a_recorded_decision_not_a_deletion(self) -> None:
        review = a_pending_review()

        dismissed = review.dismissed(at=at(days=2), note="failed on the first layer")

        assert dismissed.state is ReviewState.DISMISSED
        assert dismissed.resolved_at == at(days=2)
        assert dismissed.resolution_note == "failed on the first layer"
        # Nothing was confirmed, so nothing pretends to have been.
        assert dismissed.confirmed_usage is None
        assert dismissed.confirmed_charges == []


class TestResolutionIsIdempotent:
    """A double-click deducts twice, and a duplicate ledger entry is indistinguishable
    from a real one after the fact."""

    def test_an_approved_review_refuses_every_further_resolution(self) -> None:
        approved = a_pending_review().approved(at=at(days=1))
        with pytest.raises(ReviewAlreadyResolvedError):
            approved.approved(at=at(days=2))
        with pytest.raises(ReviewAlreadyResolvedError):
            approved.dismissed(at=at(days=2))

    def test_a_dismissed_review_refuses_them_too(self) -> None:
        dismissed = a_pending_review().dismissed(at=at(days=1))
        with pytest.raises(ReviewAlreadyResolvedError):
            dismissed.approved(at=at(days=2))
        with pytest.raises(ReviewAlreadyResolvedError):
            dismissed.dismissed(at=at(days=2))


class TestImmutability:
    def test_a_review_cannot_be_modified_in_place(self) -> None:
        review = a_pending_review()
        with pytest.raises(dataclasses.FrozenInstanceError):
            review.state = ReviewState.APPROVED  # type: ignore[misc]

    def test_resolution_returns_a_new_review(self) -> None:
        review = a_pending_review()
        approved = review.approved(at=at(days=1))
        assert review.state is ReviewState.PENDING
        assert isinstance(approved, PendingReview)
