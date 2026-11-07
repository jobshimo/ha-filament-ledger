"""Read-only registration validation; deliberately owns no repository or publisher."""

from dataclasses import dataclass

from ..domain.service.registration_quantity import registration_quantity
from ..domain.value.grams import Grams


@dataclass(frozen=True, slots=True)
class PreviewRegistration:
    def execute(
        self, basis: str, net: Grams | None, gross: Grams | None = None, tare: Grams | None = None
    ) -> Grams:
        return registration_quantity(basis, net, gross, tare)
