"""How a consumption position is written into a stored JSON document, stated once.

Four JSON columns carry per-position figures — a job's `reported_usage`, and a review's
`estimated_usage`, `confirmed_usage` and `slot_resolution` — and all four name the position
the same way. A tray is `printer`, `ams` and `slot` beside whatever the entry is about; the
printer's direct feed is `printer` and `"external": true`, with no tray half at all. One
place to say it is one place to get it wrong, and migration 0007 rewrites all four together
for exactly that reason.

**A list of objects rather than a map keyed by a composite string.** A map would need a
separator that can never appear in a printer serial, and nobody can promise that about
somebody else's hardware; the entries also stay readable in a database browser, which is
where a stored document is actually inspected. Migration 0004 already made
`slot_resolution` a list for its own reason, so this is one shape rather than two.

**No migration accompanies the direct feed (v2.8).** Every entry written before it names a
tray, and still reads as one: the external shape is recognised by a key no tray entry ever
carried, so the two coexist in one column without a rewrite.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...domain.value.identifiers import (
    AmsIndex,
    ExternalFeed,
    Feed,
    PrinterSerial,
    SlotIndex,
    TrayRef,
)

#: The key that marks an entry as the direct feed's. Its presence is the whole test, so a
#: tray entry — which never carried it — needs no rewrite to keep reading as a tray.
EXTERNAL_KEY = "external"


def tray_fields(feed: Feed) -> dict[str, str | int | bool]:
    """The keys that name a position, ready to be merged into an entry."""
    if isinstance(feed, TrayRef):
        return {"printer": feed.printer.value, "ams": feed.ams.value, "slot": feed.slot.value}
    return {"printer": feed.printer.value, EXTERNAL_KEY: True}


def tray_from(entry: Mapping[str, object]) -> Feed:
    """Read those keys back. Every stored tray entry carries all three — 0007 saw to it.

    Each value goes through `str` before it is parsed. This layer refuses an explicit `Any`
    (`disallow_any_explicit`), and a JSON integer and its decimal spelling read back as the
    same number — which is the only tolerance a document this side of the boundary needs.
    """
    printer = PrinterSerial(str(entry["printer"]))
    if entry.get(EXTERNAL_KEY):
        return ExternalFeed(printer)
    return TrayRef(
        printer=printer,
        ams=AmsIndex(int(str(entry["ams"]))),
        slot=SlotIndex(int(str(entry["slot"]))),
    )
