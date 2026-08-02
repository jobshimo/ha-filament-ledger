"""What the printer reports about one AMS tray, translated to domain terms.

A reading describes a *position* at an instant — never a spool. The tag travels with the
reel, so "tray 3 is untagged" stops being true the moment somebody swaps a reel; the
reading therefore carries what was observed and nothing that pretends to be identity.
See docs/12-field-notes.md.

The hint fields — `name`, `material`, `colour` — are what the tray sensors expose beyond
the tag (docs/05-ha-integration.md §5.8). They exist so the register form can be
pre-filled with everything the RFID provided, leaving only the opening weight for the
user to confirm (docs/06-ui-spec.md). They are hints, not validated facts: `material` is
the printer's own string, because forcing it through `MaterialKind` at the boundary would
either invent a mapping or drop what the printer actually said.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..error import InvalidValueError
from .colour import Colour
from .identifiers import SlotIndex, TagUid


@dataclass(frozen=True, slots=True)
class TrayReading:
    """One tray, as last reported by the printer.

    `tag` is `None` for an empty tray and for an occupied tray holding a spool with no
    readable tag — the gateway translates the printer's sixteen-zero sentinel to `None`
    before this object exists, and `TagUid` itself refuses the sentinel as the backstop.
    """

    slot: SlotIndex
    tag: TagUid | None
    empty: bool
    name: str | None = None
    material: str | None = None
    colour: Colour | None = None

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

    def __str__(self) -> str:
        if self.empty:
            return f"tray {self.slot}: empty"
        return f"tray {self.slot}: {self.tag or 'no tag'}"
