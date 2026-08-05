"""Give the migrated rows the name discovery found for them.

Migration 0007 has no gateway to ask, so it writes `UNIDENTIFIED` into every mounted
spool's tray reference. Discovery *does* know the serial, and it knows it a few lines later
in the same startup — so the composition root calls this once, after `migrate()` and after
the gateway has run discovery, and the ledger stops carrying a placeholder.

**Why it cannot be skipped.** Without it a slot has two names: the ledger says
`(UNIDENTIFIED, 1, 3)` and the gateway starts reporting `(<serial>, 1, 3)`. The next
reconciliation pass would look up tray 3 under the real serial, find it empty, and mount a
second spool there — two spools in one tray, with the unique index correctly seeing two
different trays and looking away. The whole point of widening the index is lost if the two
halves of the system disagree about what a tray is called.

**Only `spool` is rewritten, and that is deliberate.** A job's `reported_usage` and a
review's frozen lines are *records of what was said at the time*, and this ledger does not
rewrite what it recorded (docs/adr/0001-append-only-ledger.md). Nothing looks a spool up by
those trays — approval charges the spool ids the review froze — so a historical entry
reading `UNIDENTIFIED` is both harmless and true: nobody knew the name when the row was
written. The location is the one place the name is *used* rather than *remembered*.

This is a data repair, not a use case, so it lives here and not behind the repository port —
which deliberately exposes no mutation beyond `save` and is not going to grow one for a
statement that runs once in the life of a database.
"""

from __future__ import annotations

from ...domain.value.identifiers import UNIDENTIFIED_PRINTER, PrinterSerial
from .database import Database


async def adopt_unidentified_trays(database: Database, serial: PrinterSerial | None) -> None:
    """Rewrite every placeholder tray reference to `serial`. Idempotent, and safe to skip.

    A `None` serial — no printer discovered — leaves the placeholder in place, which is the
    honest answer: there is no name to adopt, the rows go on naming one printer, and a
    later start that does find one adopts them then.

    Adopting a serial the rows already carry matches nothing and writes nothing, so this
    runs on every start and does work exactly once.
    """
    if serial is None or serial == UNIDENTIFIED_PRINTER:
        return
    await database.execute(
        "UPDATE spool SET location_printer = ? WHERE location_printer = ?",
        (serial.value, UNIDENTIFIED_PRINTER.value),
    )
