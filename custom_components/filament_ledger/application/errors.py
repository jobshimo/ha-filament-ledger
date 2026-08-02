"""Application-level errors.

Distinct from domain errors: these are about *orchestration* — something the caller asked
for does not exist, or cannot be found. The domain's errors are about rules.
"""

from __future__ import annotations

from ..domain.value.identifiers import SpoolId


class ApplicationError(Exception):
    """Base for orchestration failures."""


class SpoolNotFoundError(ApplicationError):
    def __init__(self, spool_id: SpoolId) -> None:
        super().__init__(f"no spool with id {spool_id}")
        self.spool_id = spool_id
