"""The job reconciliation pass — every machine whose lifecycle turned while nobody heard.

The printer does not replay what happened while Home Assistant was off (the port's own
contract), and both edges of a print are worth money. Upstream fires neither reliably: it
guards `event_print_started` and `event_print_finished` alike with
`previous_gcode_state != "unknown"` (`pybambu/models.py`), and a reconnection resets that
to `unknown`. A machine whose connection drops across either edge announces nothing at all.

The two silences cost differently, and both cost:

- **A missed ending** leaves a RUNNING row for a print that stopped hours ago, and nothing
  in the system will ever close it.
- **A missed start** leaves no row at all — so the ending, whenever it arrives, finds
  nothing to close and is discarded as an inference about a print already recorded. The
  whole job vanishes. Measured on the reference instance: a 291.42 g print ran to 68 % with
  no row in the ledger after a 12.3 s dropout (docs/12-field-notes.md, 2026-08-11), and the
  248.41 g `MANUAL_ADJUSTMENT` of 2026-08-08 is the same hole, paid for by hand.

So this pass asks the opposite question from `TraySync`, and asks it of the *level* rather
than of an event, in both directions: **what is this machine doing right now, and does the
ledger agree?** For every followed printer it reads the levels as they stand and hands both
inferences to `TrackPrintJob`, which closes a running row, opens a missing one, or does
nothing at all.

**It can neither invent a print nor duplicate one, and that is the whole safety argument.**
An idle machine reads `finish` for as long as it sits there and a printing one reads
`printing` for hours, so a pass that trusted either level would mint a phantom job on every
startup. It does not, because every event it produces is `derived`: `PrintEnded.derived`
bounds an inferred ending to a row the ledger already holds open, `PrintStarted.derived`
bounds an inferred start to a print the ledger holds no row for, and `TrackPrintJob`
enforces both. Everything this pass reports, it reports because the ledger and the machine
disagreed.

Living in `infrastructure/ha` for the reason `TraySync` does: the pass needs the gateway,
and the application layer may not import Home Assistant.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ...application.track_print_job import TrackPrintJob
from ...domain.value.identifiers import PrinterSerial, PrintJobId
from ...domain.value.print_event import PrintEnded, PrintEvent
from .bambu_gateway import BambuLabGateway

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobSync:
    """Make the ledger's open rows agree with what every followed machine is doing now.

    Constructed once in the composition root, same as `TraySync`, so a later on-demand
    caller runs exactly the wiring startup ran rather than a re-creation of it.
    """

    gateway: BambuLabGateway
    track_print_job: TrackPrintJob

    async def execute(self) -> list[PrintJobId]:
        """The jobs this pass reconciled, in the order it touched them. Usually empty.

        Both inferences per followed machine, handed over unconditionally: whether a row is
        open is `TrackPrintJob`'s question, and asking it here as well would put the
        correlation rules in two places for them to drift apart in. A machine with nothing
        to reconcile costs two bounded reads.

        **The ending is offered first**, though on a healthy machine the two are mutually
        exclusive — a printer sitting on `finish` is not printing, and one that is printing
        has not reached a terminal level. The order is what settles the case where they
        overlap because a level moved between the two reads: closing what stopped before
        opening what started keeps the new row the newest, which is the ordering
        `_is_newest` depends on to protect the old one from a later inference.

        **Nothing here raises.** Startup runs this pass unguarded, and a printer whose
        lifecycle cannot be recorded must not take the integration down with it — the ledger
        that loads with one job still open is strictly better than the one that does not
        load. The same argument `TraySync` makes about a single failing tray.
        """
        reconciled: list[PrintJobId] = []
        for printer in self.gateway.printers:
            inferences = (
                self.gateway.derived_ending(printer),
                self.gateway.derived_start(printer),
            )
            for event in inferences:
                if event is None:
                    continue
                job_id = await self._apply(printer, event)
                if job_id is not None:
                    reconciled.append(job_id)
        return reconciled

    async def _apply(self, printer: PrinterSerial, event: PrintEvent) -> PrintJobId | None:
        """Hand one inference over, and say out loud what it changed. Never raises.

        `None` covers both *nothing to do* — the overwhelmingly common answer, and the whole
        reason the pass is safe to run on every startup — and *it failed*, which is logged
        with its traceback and then dropped so the remaining machines still get their turn.
        """
        try:
            job_id = await self.track_print_job.execute(event)
        except Exception:
            LOGGER.exception(
                "reconciling the lifecycle of printer %s failed; the remaining machines still run",
                printer,
            )
            return None
        if job_id is None:
            return None
        if isinstance(event, PrintEnded):
            LOGGER.info(
                "printer %s reports %s while the ledger held job %s open; closed it and "
                "recorded what the machine reported",
                printer,
                event.outcome.value,
                job_id,
            )
        else:
            LOGGER.info(
                "printer %s is printing %r and the ledger held no row for it; opened job "
                "%s so its consumption is not lost",
                printer,
                event.name,
                job_id,
            )
        return job_id
