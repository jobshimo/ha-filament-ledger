"""Application-level errors.

Distinct from domain errors: these are about *orchestration* — something the caller asked
for does not exist, or cannot be found. The domain's errors are about rules.
"""

from __future__ import annotations

from ..domain.value.identifiers import MovementId, ReviewId, SpoolId


class ApplicationError(Exception):
    """Base for orchestration failures."""


class SpoolNotFoundError(ApplicationError):
    def __init__(self, spool_id: SpoolId) -> None:
        super().__init__(f"no spool with id {spool_id}")
        self.spool_id = spool_id


class MovementNotFoundError(ApplicationError):
    """The corrections of docs/14 all start from a row the user pointed at.

    Orchestration rather than a rule: the id names nothing, which is a different failure
    from the ledger refusing what the id names.
    """

    def __init__(self, movement_id: MovementId) -> None:
        super().__init__(f"no movement with id {movement_id}")
        self.movement_id = movement_id


class ReviewNotFoundError(ApplicationError):
    def __init__(self, review_id: ReviewId) -> None:
        super().__init__(f"no review with id {review_id}")
        self.review_id = review_id
