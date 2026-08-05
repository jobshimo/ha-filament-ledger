"""How a `TrayRef` is written into a stored JSON document, stated once.

Four JSON columns carry per-tray figures — a job's `reported_usage`, and a review's
`estimated_usage`, `confirmed_usage` and `slot_resolution` — and all four name the tray the
same way: `printer`, `ams` and `slot` beside whatever the entry is about. One place to say
it is one place to get it wrong, and migration 0007 rewrites all four together for exactly
that reason.

**A list of objects rather than a map keyed by a composite string.** A map would need a
separator that can never appear in a printer serial, and nobody can promise that about
somebody else's hardware; the entries also stay readable in a database browser, which is
where a stored document is actually inspected. Migration 0004 already made
`slot_resolution` a list for its own reason, so this is one shape rather than two.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...domain.value.identifiers import AmsIndex, PrinterSerial, SlotIndex, TrayRef


def tray_fields(tray: TrayRef) -> dict[str, str | int]:
    """The three keys that name a tray, ready to be merged into an entry."""
    return {"printer": tray.printer.value, "ams": tray.ams.value, "slot": tray.slot.value}


def tray_from(entry: Mapping[str, object]) -> TrayRef:
    """Read those three keys back. Every stored entry carries all three — 0007 saw to it.

    Each value goes through `str` before it is parsed. This layer refuses an explicit `Any`
    (`disallow_any_explicit`), and a JSON integer and its decimal spelling read back as the
    same number — which is the only tolerance a document this side of the boundary needs.
    """
    return TrayRef(
        printer=PrinterSerial(str(entry["printer"])),
        ams=AmsIndex(int(str(entry["ams"]))),
        slot=SlotIndex(int(str(entry["slot"]))),
    )
