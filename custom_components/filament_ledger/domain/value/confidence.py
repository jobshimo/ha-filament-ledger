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
        """LOW is not "this number is bad". It is "weigh this when you get a chance".

        Both routes to LOW ask for the same thing, which is why the prompt turns on the
        level rather than on the rule that produced it: an approved estimate and a reel
        drawn past the point where the drift would raise an anomaly are answered by the
        same thirty seconds with a kitchen scale.
        """
        return self is Confidence.LOW
