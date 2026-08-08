"""What the printer reports about one AMS tray, translated to domain terms.

A reading describes a *position* at an instant — never a spool. The tag travels with the
reel, so "tray 3 is untagged" stops being true the moment somebody swaps a reel; the
reading therefore carries what was observed and nothing that pretends to be identity.
See docs/12-field-notes.md.

The hint fields — `name`, `material`, `colour`, `weight` — are what the tray sensors
expose beyond the tag (docs/05-ha-integration.md §5.8). They exist so the register form
can be pre-filled with everything the RFID provided, leaving the opening weight for the
user to confirm (docs/06-ui-spec.md). They are hints, not validated facts: `material` is
the printer's own string, because forcing it through `MaterialKind` at the boundary would
either invent a mapping or drop what the printer actually said.

`weight` is the tag's own `tray_weight` — what the reel held **new**, not what is left on
it now. The printer has no scale, and `remain` is useless on this hardware (docs/12), so
nothing here reports a current balance; this figure is only ever an opening weight for a
reel the ledger is meeting for the first time. Absent whenever the tag declined to say.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..error import InvalidValueError
from .colour import Colour
from .grams import Grams
from .identifiers import TagUid, TrayRef


@dataclass(frozen=True, slots=True)
class TrayReading:
    """One tray, as last reported by the printer.

    `tray` names the position in full — printer, AMS unit, tray — because the gateway can
    only report a reading it can say *where* it came from, and a reading that named a bare
    tray number would be a reading whose printer the ledger had to assume.

    `tag` is `None` for an empty tray and for an occupied tray holding a spool with no
    readable tag — the gateway translates the printer's sixteen-zero sentinel to `None`
    before this object exists, and `TagUid` itself refuses the sentinel as the backstop.
    """

    tray: TrayRef
    tag: TagUid | None
    empty: bool
    name: str | None = None
    material: str | None = None
    colour: Colour | None = None
    weight: Grams | None = None

    def __post_init__(self) -> None:
        # An empty tray with a tag is contradictory data, and the contradiction matters:
        # the empty branch unmounts whatever the ledger has in that slot, and doing so
        # while a tag says a spool is present would act on a reading that refutes itself.
        if self.empty and self.tag is not None:
            msg = f"an empty tray cannot carry tag {self.tag}"
            raise InvalidValueError(msg)
        for field_name in ("name", "material"):
            hint = getattr(self, field_name)
            if hint is not None and not hint.strip():
                msg = f"{field_name} hint cannot be blank; use None for absent"
                raise InvalidValueError(msg)
        # Same rule as the blank strings above, in mass: a reel that holds nothing is not
        # a weight the register path could open a balance with, and the domain refuses
        # such an opening weight anyway. Absence travels as `None`, never as zero.
        if self.weight is not None and not self.weight.is_positive:
            msg = f"weight hint must be positive; use None for absent, got {self.weight}"
            raise InvalidValueError(msg)

    def __str__(self) -> str:
        if self.empty:
            return f"tray {self.tray.slot}: empty"
        return f"tray {self.tray.slot}: {self.tag or 'no tag'}"
