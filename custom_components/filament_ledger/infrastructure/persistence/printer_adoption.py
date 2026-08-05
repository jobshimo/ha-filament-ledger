"""Give the migrated rows the name discovery found for them.

Migrations 0007 and 0008 have no gateway to ask, so they write `UNIDENTIFIED` into every
mounted spool's location — a tray reference or a direct feed. Discovery *does* know the
serial, and it knows it a few lines later in the same startup — so the composition root
calls this once, after `migrate()` and after the gateway has run discovery, and the ledger
stops carrying a placeholder.

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

import logging

from ...domain.value.identifiers import UNIDENTIFIED_PRINTER, PrinterSerial
from .database import Database

LOGGER = logging.getLogger(__name__)


async def adopt_unidentified_trays(database: Database, printers: tuple[PrinterSerial, ...]) -> None:
    """Rewrite every placeholder location to the discovered machine. Idempotent.

    **One printer adopts; several do not, and there is no third option that is honest.**

    The placeholder means *the one machine this ledger has always talked to*. With exactly
    one machine discovered, that machine is it — there is nothing else the rows could be
    describing, and adopting is what stops the ledger and the gateway calling one tray by
    two names.

    With several, the ledger has no record of which of them it has always talked to. It is
    not recoverable either: the config entry stores no serial, the spool rows store only the
    placeholder, and a device id is a random identifier rather than a name a machine answers
    to. Every rule that would pick one — the lowest device id, the first by serial, the one
    with the most trays — is a coin toss dressed as a heuristic, and losing it puts somebody's
    spools on a printer they are not in. So the rows keep the placeholder, the AMS view shows
    them as their own section under a heading that says the machine was never named, and the
    owner moves each spool onto the right machine with the mount control they already have.

    That case is narrower than it sounds. Adoption has run on every start since v1.4, so a
    ledger that ever saw one nameable printer already carries its serial and passes through
    here doing nothing. What is left is the ledger that never resolved a printer at all until
    the day two appeared at once — and for that ledger the honest answer really is *I do not
    know which*, said where it can be read rather than guessed at where it cannot.

    A `None`-equivalent — no printer discovered — leaves the placeholder in place too, which
    is the same honesty for the simpler reason: there is no name to adopt, the rows go on
    naming one printer, and a later start that does find one adopts them then.

    Adopting a serial the rows already carry matches nothing and writes nothing, so this runs
    on every start and does work exactly once.
    """
    if len(printers) != 1:
        if len(printers) > 1:
            LOGGER.debug(
                "several printers discovered (%s); any location still naming %s is left as it "
                "is, because which machine it means was never recorded",
                [serial.value for serial in printers],
                UNIDENTIFIED_PRINTER,
            )
        return
    serial = printers[0]
    if serial == UNIDENTIFIED_PRINTER:
        return
    await database.execute(
        "UPDATE spool SET location_printer = ? WHERE location_printer = ?",
        (serial.value, UNIDENTIFIED_PRINTER.value),
    )
