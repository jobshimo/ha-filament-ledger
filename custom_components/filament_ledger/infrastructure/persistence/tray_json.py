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

**Nor the second holder (v2.9).** The same trick one level down: `holder` is written only
when it is not the first, so every entry already in the column goes on meaning the holder it
has always meant, and a reader needs no version to know which.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...domain.value.identifiers import (
    FIRST_HOLDER,
    MIN_EXTERNAL_HOLDER,
    AmsIndex,
    ExternalFeed,
    Feed,
    HolderIndex,
    PrinterSerial,
    SlotIndex,
    TrayRef,
)

#: The key that marks an entry as the direct feed's. Its presence is the whole test, so a
#: tray entry — which never carried it — needs no rewrite to keep reading as a tray.
EXTERNAL_KEY = "external"

#: Which of the machine's holders the entry names (v2.9). Absent reads as the first, which
#: is what lets every entry written before the second holder existed keep its meaning
#: without a migration — the same statement 0010 makes about the rows in the `spool` table.
HOLDER_KEY = "holder"


def tray_fields(feed: Feed) -> dict[str, str | int | bool]:
    """The keys that name a position, ready to be merged into an entry.

    **The first holder writes no key**, which is the same restraint that let the direct feed
    arrive without a migration: a key nothing wrote before must mean, by its absence, what
    every document already written means. Writing `holder: 1` everywhere would say nothing
    new and would put two spellings of one fact in one column for ever, so only the second
    holder — the one an older reader could not have meant — is spelled out.
    """
    if isinstance(feed, TrayRef):
        return {"printer": feed.printer.value, "ams": feed.ams.value, "slot": feed.slot.value}
    fields: dict[str, str | int | bool] = {"printer": feed.printer.value, EXTERNAL_KEY: True}
    if feed.holder != FIRST_HOLDER:
        fields[HOLDER_KEY] = feed.holder.value
    return fields


def tray_from(entry: Mapping[str, object]) -> Feed:
    """Read those keys back. Every stored tray entry carries all three — 0007 saw to it.

    Each value goes through `str` before it is parsed. This layer refuses an explicit `Any`
    (`disallow_any_explicit`), and a JSON integer and its decimal spelling read back as the
    same number — which is the only tolerance a document this side of the boundary needs.

    The holder is the one key with a default, and the default is the fact rather than a
    convenience: an entry that names none was written while a machine could hold exactly one
    reel directly, so the holder it means is the first.
    """
    printer = PrinterSerial(str(entry["printer"]))
    if entry.get(EXTERNAL_KEY):
        holder = entry.get(HOLDER_KEY)
        return ExternalFeed(
            printer,
            HolderIndex(int(str(holder)) if holder is not None else MIN_EXTERNAL_HOLDER),
        )
    return TrayRef(
        printer=printer,
        ams=AmsIndex(int(str(entry["ams"]))),
        slot=SlotIndex(int(str(entry["slot"]))),
    )
