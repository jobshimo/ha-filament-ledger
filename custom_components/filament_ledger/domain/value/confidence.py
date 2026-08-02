"""How much a balance can be trusted.

Derived, never set by hand. A number presented without its reliability invites false trust,
so every surface that shows a balance shows this alongside it.
"""

from __future__ import annotations

from enum import StrEnum


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @property
    def needs_weighing(self) -> bool:
        """LOW is not "this number is bad". It is "weigh this when you get a chance"."""
        return self is Confidence.LOW
