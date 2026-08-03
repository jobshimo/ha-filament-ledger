"""The void chapter — a status record about a movement, never a movement.

Every rule here also exists as a `CHECK` in migration 0003, and the duplication is
deliberate: a constraint name is not an answer a user can act on, and the entity is what
the use cases reason about. Both layers holding the same line is the same argument
docs/adr/0007 makes about the immutability triggers.
"""

from __future__ import annotations

import pytest

from custom_components.filament_ledger.domain.error import InvalidValueError
from custom_components.filament_ledger.domain.model.movement_void import MovementVoid
from custom_components.filament_ledger.domain.value.identifiers import MovementId

from .conftest import EPOCH, at

ORIGINAL = MovementId("mv-original")
REVERSAL = MovementId("mv-reversal")
REINSTATEMENT = MovementId("mv-reinstatement")


def a_chapter(**overrides: object) -> MovementVoid:
    settings: dict[str, object] = {
        "movement_id": ORIGINAL,
        "voided_at": EPOCH,
        "reason": "wrong spool was loaded",
        "reversal_movement_id": REVERSAL,
    } | overrides
    return MovementVoid(**settings)  # type: ignore[arg-type]


class TestAnOpenChapter:
    def test_a_void_with_restitution_is_open_until_it_is_reinstated(self) -> None:
        chapter = a_chapter()

        assert chapter.is_open
        assert chapter.had_restitution

    def test_reinstating_closes_it_with_both_facts_together(self) -> None:
        closed = a_chapter().reinstated(REINSTATEMENT, at(days=1))

        assert not closed.is_open
        assert closed.reinstated_at == at(days=1)
        assert closed.reinstatement_movement_id == REINSTATEMENT

    def test_a_closed_chapter_cannot_be_closed_again(self) -> None:
        """Restoring twice would deduct the grams twice, and in a ledger a duplicate entry
        is indistinguishable from a real one after the fact."""
        closed = a_chapter().reinstated(REINSTATEMENT, at(days=1))

        with pytest.raises(InvalidValueError, match="already reinstated"):
            closed.reinstated(MovementId("mv-again"), at(days=2))


class TestAVoidWithoutRestitution:
    def test_it_needs_a_reason(self) -> None:
        """A null reversal with no explanation reads as a bug six months later."""
        with pytest.raises(InvalidValueError, match="needs a reason"):
            a_chapter(reversal_movement_id=None, reason=None)
        with pytest.raises(InvalidValueError, match="needs a reason"):
            a_chapter(reversal_movement_id=None, reason="   ")

    def test_it_is_open_but_never_restorable(self) -> None:
        chapter = a_chapter(reversal_movement_id=None, reason="the spool was never here")

        assert chapter.is_open
        assert not chapter.had_restitution

    def test_it_can_never_be_reinstated(self) -> None:
        """Nothing came back, so "deduct it again" would charge the same grams twice."""
        with pytest.raises(InvalidValueError, match="never be reinstated"):
            a_chapter(
                reversal_movement_id=None,
                reason="the spool was never here",
                reinstated_at=at(days=1),
                reinstatement_movement_id=REINSTATEMENT,
            )


class TestTheReinstatementPairIsAllOrNothing:
    def test_a_timestamp_with_no_movement_is_refused(self) -> None:
        with pytest.raises(InvalidValueError, match="both reinstatement facts"):
            a_chapter(reinstated_at=at(days=1))

    def test_a_movement_with_no_timestamp_is_refused(self) -> None:
        with pytest.raises(InvalidValueError, match="both reinstatement facts"):
            a_chapter(reinstatement_movement_id=REINSTATEMENT)


class TestAReasonIsOptionalWhenGramsCameBack:
    def test_a_restitution_void_needs_no_explanation(self) -> None:
        """The reversal *is* the explanation: the grams went back and a row says so. Only
        the branch where nothing came back has to justify itself in prose."""
        chapter = a_chapter(reason=None)

        assert chapter.reason is None
        assert chapter.had_restitution
