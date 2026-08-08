"""The job reconciliation pass — every machine that stopped while nobody was listening.

The printer does not replay what happened while Home Assistant was off (the port's own
contract), and an ending is the one moment in a print's life that is worth money. If the
machine reached `finish` during a restart, a reload, or a dropout, its bus event fired in
front of nobody — or, more often, was never fired at all: upstream suppresses it when the
connection reset across the ending (`_DERIVED_OUTCOMES` in `bambu_gateway`). Either way the
ledger holds a RUNNING row for a print that stopped hours ago, and nothing in the system
will ever close it.

This pass asks the opposite question from `TraySync`, and asks it of the *level* rather
than of an event: **the printer is sitting on a finished print — is the ledger still
holding it open?** For every followed machine it reads the status sensor as it stands now
and hands the resulting ending to `TrackPrintJob`, which closes a running row or does
nothing at all.

**It cannot invent a print, and that is the whole safety argument.** An idle machine reads
`finish` for as long as it sits there, so a pass that trusted the level would mint a
phantom job on every startup and charge the last plan again. It does not, because the
endings it produces are `derived` — `PrintEnded.derived` states the rule and
`TrackPrintJob._ended` enforces it. Everything this pass reports, it reports because the
ledger independently believed that machine was printing.

Living in `infrastructure/ha` for the reason `TraySync` does: the pass needs the gateway,
and the application layer may not import Home Assistant.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ...application.track_print_job import TrackPrintJob
from ...domain.value.identifiers import PrintJobId
from .bambu_gateway import BambuLabGateway

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobSync:
    """Close every job the ledger still holds open for a machine that has already stopped.

    Constructed once in the composition root, same as `TraySync`, so a later on-demand
    caller runs exactly the wiring startup ran rather than a re-creation of it.
    """

    gateway: BambuLabGateway
    track_print_job: TrackPrintJob

    async def execute(self) -> list[PrintJobId]:
        """The jobs this pass closed, in the order it closed them. Usually empty.

        One derived ending per followed machine, handed over unconditionally: whether a
        row is open is `TrackPrintJob`'s question, and asking it here as well would put the
        correlation rules in two places for them to drift apart in. A machine with nothing
        open costs one bounded read.

        **Nothing here raises.** Startup runs this pass unguarded, and a printer whose
        ending cannot be recorded must not take the integration down with it — the ledger
        that loads with one job still open is strictly better than the one that does not
        load. The same argument `TraySync` makes about a single failing tray.
        """
        closed: list[PrintJobId] = []
        for printer in self.gateway.printers:
            ending = self.gateway.derived_ending(printer)
            if ending is None:
                continue
            try:
                job_id = await self.track_print_job.execute(ending)
            except Exception:
                LOGGER.exception(
                    "closing the open job on printer %s failed; the remaining machines still run",
                    printer,
                )
                continue
            if job_id is None:
                continue
            LOGGER.info(
                "printer %s reports %s while the ledger held job %s open; closed it and "
                "recorded what the machine reported",
                printer,
                ending.outcome.value,
                job_id,
            )
            closed.append(job_id)
        return closed
