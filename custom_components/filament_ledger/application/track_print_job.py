"""The print lifecycle, recorded as the printer reports it.

One use case receives every `PrintEvent` the gateway translates. A start becomes a
`RUNNING` row; an ending updates that row with the moment's figures and — for a cancelled
or failed job — opens a review through UC-05. The classification is never made here: the
event arrives with its outcome already read off the upstream event type (Q1, closed).

A `FINISHED` job goes to UC-04 (Q4, closed — docs/12-field-notes.md): the ending's row,
the automatic deduction and the `consumption_recorded` flag land in one unit inside
`RecordPrintConsumption`, which degrades to a review with reason `UNMAPPED_USAGE`
wherever a figure cannot be attributed (docs/04-use-cases.md UC-04).

**Correlation is per printer, and that is the whole of what v2.0 changed here.** An ending
carries no job id, so it is matched to the newest `RUNNING` row — and with two machines
printing at once "the newest RUNNING row" is the other machine's job about half the time.
The consequence is not a cosmetic mislabelling: the ending's per-tray figures are written
onto that row, so UC-04 would deduct one printer's grams from the spools in the other
printer's trays, and a cancellation would open a review whose lines name trays the job
never touched. Both printers are then wrong, and the ledger says so about neither. See
`_running_job`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING

from ..domain.error import ReviewAlreadyPendingError
from ..domain.model.print_job import PrintJob
from ..domain.port.clock import Clock
from ..domain.port.repositories import PrintJobRepository
from ..domain.port.unit_of_work import UnitOfWork
from ..domain.value.identifiers import PrinterSerial, PrintJobId, new_print_job_id
from ..domain.value.print_event import PrintEnded, PrintEvent, PrintStarted
from ..domain.value.print_job_state import PrintJobState
from ..domain.value.review import ReviewReason
from .record_print_consumption import RecordPrintConsumption
from .review_queue import OpenPendingReview, OpenPendingReviewCommand

if TYPE_CHECKING:
    from datetime import datetime

LOGGER = logging.getLogger(__name__)

# How far down **one machine's** recent-jobs listing the running job is looked for. The job
# an ending belongs to is expected at the head — anything RUNNING deeper than this is a
# stale row from an ending that never arrived, not the job that just stopped.
#
# Counted per printer since v2.0, which is what keeps the number meaning what it says: a
# machine printing one long job while another runs fifteen short ones would push the long
# job out of a shared window of ten and lose the correlation to a limit that was never
# about it.
CORRELATION_WINDOW = 10

# How recently one machine's newest job must have stopped for a second ending to be read as
# *the same ending, arriving twice* rather than as a new print nobody watched begin.
#
# Two independent signals now report an ending — the bus event and the status sensor
# (docs/05 §5.8) — and on a healthy print they land in the same second. Whichever loses the
# race arrives at a ledger with no RUNNING row left, which is the exact shape of the restart
# case below; without this window that signal would open a second row for a print already
# recorded and charge its grams twice. `consumption_recorded` does not cover it, because
# that flag guards a *row*, and the duplicate is a different row.
#
# Five minutes is far longer than either signal's delay and far shorter than a print, and
# the name has to match as well — so the branch that repairs a swallowed start still fires
# for a genuinely new job, which is the whole reason it exists.
DUPLICATE_ENDING_WINDOW = timedelta(minutes=5)

# How far apart two readings of the machine's own `start_time` may be and still describe
# **the same print** — the identity an inferred start correlates by.
#
# Exact equality does not work, and the reason is measured rather than defensive: upstream
# publishes the figure truncated to the minute and corrects it seconds later. On the
# reference instance (docs/12-field-notes.md, 2026-08-10) one job read `23:04:00` at
# 01:04:34 local and `23:06:37` at 01:06:41 — the same print, two values, 2 m 37 s apart.
# A comparison by equality would call the correction a new job and open a second row for a
# print already being tracked.
#
# Five minutes is far wider than any correction observed and far narrower than the gap
# between two prints of the same plate, which is the collision this has to stay clear of.
JOB_IDENTITY_TOLERANCE = timedelta(minutes=5)

# How recently this machine's open row must have been created for an **announced** start
# that carries no printer timestamp to be read as *the same print, arriving twice* rather
# than as a new job.
#
# The two starts race by seconds on a healthy print: the status sensor reaches `running` and
# the bus fires `event_print_started` within the same moment, so the inference opens the row
# and the announcement finds it already there. Without this window that announcement would
# open a second row for the print already being tracked and UC-04 would deduct its grams
# twice.
#
# Measured against the case it must **not** swallow: upstream announced two starts for one
# job eight minutes apart on 2026-08-08, and the ledger has always answered that with two
# rows. Five minutes keeps that answer, and keeps it by the same margin the ending path uses
# for the same shape of question.
SIMULTANEOUS_START_WINDOW = timedelta(minutes=5)

# Why a review opens, keyed by how the job ended. `FINISHED` is deliberately absent:
# a completed job needs no decision (docs/adr/0004-approval-queue-for-estimates.md).
_REVIEW_REASONS = {
    PrintJobState.CANCELLED: ReviewReason.CANCELLED,
    PrintJobState.FAILED: ReviewReason.FAILED,
}


@dataclass(frozen=True, slots=True)
class TrackPrintJob:
    jobs: PrintJobRepository
    open_pending_review: OpenPendingReview
    record_print_consumption: RecordPrintConsumption
    clock: Clock
    uow: UnitOfWork

    async def execute(self, event: PrintEvent) -> PrintJobId | None:
        """The job this event landed on, or `None` when it deliberately landed on nothing.

        Both moments can answer `None`, and every shape that does is a non-event by design
        rather than a failure — so none of them raises, and none has an id to give, because
        no row was written:

        - *An inferred ending with no running job to close*, and *a second delivery of an
          ending already recorded* — the two shapes `_ended` refuses.
        - *An inferred start for a job already being tracked* — the level saying `printing`
          about the very print whose row is already open. `_started` refuses it.
        """
        match event:
            case PrintStarted():
                return await self._started(event)
            case PrintEnded():
                return await self._ended(event)

    async def _started(self, event: PrintStarted) -> PrintJobId | None:
        """A new job, a new identity. The upstream event carries no job id, so the row
        created here is what a later ending correlates back to (see `_running_job`).

        `started_at` is **this** clock's, not the printer's, and the printer's own answer
        lands beside it rather than over it. Everything the ledger orders itself by is
        stamped from one clock; the machine's report is a second fact about the same
        moment, and `PrintJob` says why the two must not be merged.

        **No start opens a second row for a print already being tracked**, and the guard
        covers the announced start as much as the inferred one. That symmetry is not
        tidiness, it is the whole correctness of adding inference at all: on a healthy print
        the two signals arrive within the same second — the status sensor reaches `running`
        and the bus fires `event_print_started` — so a guard that exempted the announced one
        would open a row for the inference and a second row for the announcement, every
        single print. UC-04 would then deduct the same grams twice, which is the one failure
        this module exists to prevent.

        The identity is the machine's own `start_time` — not the level, not the name — and
        `_same_print` says what happens when it is missing, which is where the announced and
        the inferred start finally part company.

        **A stale row is left standing rather than closed here.** When the printer is
        plainly running a print the open row does not describe, that row belongs to a job
        whose ending never arrived, and inventing an outcome for it would be a guess with a
        number on it. Opening the new row is enough: the stale one stops being the newest,
        and `_is_newest` already refuses to let any inference close it — the same mechanism
        that protects the double-start case, reused rather than duplicated.
        """
        now = self.clock.now()
        open_job = await self._running_job(event.printer)
        if open_job is not None and _same_print(open_job, event, now):
            LOGGER.debug(
                "printer %s reports a print that job %s (%s) already tracks; %s start ignored",
                event.printer,
                open_job.id,
                open_job.name,
                "inferred" if event.derived else "announced",
            )
            return None
        if open_job is not None:
            LOGGER.warning(
                "printer %s is running a print that job %s (%s) does not describe; that "
                "row's ending never arrived and it is left open for review, while the "
                "print now running gets a row of its own",
                event.printer,
                open_job.id,
                open_job.name,
            )
        job = PrintJob(
            id=new_print_job_id(),
            name=event.name,
            state=PrintJobState.RUNNING,
            started_at=now,
            printer=event.printer,
            reported_usage=event.plan,
            printer_started_at=event.printer_started_at,
        )
        async with self.uow:
            await self.jobs.save(job)
        return job.id

    async def _ended(self, event: PrintEnded) -> PrintJobId | None:
        """Close the running job with the moment's figures — creating the row first when
        a restart swallowed the start, because a review must never be lost to a restart.

        `ended_at` stays `now`. UC-04 stamps every movement's `occurred_at` from it, and
        the whole ledger orders itself by `occurred_at` — so a printer's clock reaching it
        would sort a print against reconciliations it never happened near. The machine's
        own pair is recorded in its own two columns instead, where nothing compares it to
        anything (`PrintJob` states the rule and the consequence).

        **With no running row, the repair is not unconditional.** Two signals report an
        ending now, so *no row left to close* has three causes, and only one of them wants
        a new row:

        - *An inferred ending.* It is read off a level that rests in `finish` between
          prints, so with nothing running it is describing a print already closed —
          usually the one the other signal just closed. `PrintEnded.derived` says why this
          can never open a job.
        - *The same ending, twice.* The other signal won the race moments ago and this one
          arrived to find its own work done. `_already_ended` recognises it.
        - *A start nobody saw.* The integration restarted mid-print. This is the case the
          row-creating branch was written for, and it keeps it.
        """
        now = self.clock.now()
        job = await self._running_job(event.printer)
        if job is not None and event.derived and not await self._is_newest(event.printer, job):
            # A stale running row, and an inference has no business closing one. See
            # `_is_newest` for the double charge this prevents.
            LOGGER.debug(
                "job %s (%s) on printer %s is a stale running row; a later job already "
                "concluded there, so this inferred %s does not describe it",
                job.id,
                job.name,
                event.printer,
                event.outcome.value,
            )
            return None
        if job is None:
            if event.derived:
                LOGGER.debug(
                    "%s inferred on printer %s with no running job; nothing to close",
                    event.outcome.value,
                    event.printer,
                )
                return None
            duplicate = await self._already_ended(event, now)
            if duplicate is not None:
                LOGGER.debug(
                    "job %s (%s) already ended at %s; duplicate %s ending ignored",
                    duplicate.id,
                    duplicate.name,
                    duplicate.ended_at,
                    event.outcome.value,
                )
                return None
            # The integration restarted mid-print: no RUNNING row exists for this ending on
            # this machine. The start time is gone; `now` is the honest lower bound for both
            # timestamps.
            job = PrintJob(
                id=new_print_job_id(),
                name=event.name,
                state=PrintJobState.RUNNING,
                started_at=now,
                printer=event.printer,
            )
        ended = replace(
            job,
            state=event.outcome,
            ended_at=now,
            layer_reached=event.layer_reached,
            total_layers=event.total_layers,
            progress=event.progress,
            # The ending's figures win when present; otherwise whatever the start knew
            # survives — for an interrupted job those totals are exactly what the
            # estimator scales by progress (docs/07-consumption-estimation.md §7.3).
            #
            # **The Bambu gateway now knows nothing at the start**, deliberately: its
            # weight sensor is republished after the start event, so a plan captured
            # there would be the previous job's. That makes this fallback inert for the
            # only adapter shipped today — kept because the port permits a gateway that
            # genuinely knows the plan up front, and because a `None` ending must not
            # erase a figure such a gateway supplied.
            reported_usage=(
                event.reported_usage if event.reported_usage is not None else job.reported_usage
            ),
            raw_gcode_state=event.raw_gcode_state,
            raw_print_error=event.raw_print_error,
            # The same rule the plan follows, for the same reason: the ending's reading
            # wins when the sensor gave one, and the start's capture survives when it did
            # not. A machine that reset `start_time` before the finish arrived keeps the
            # start this job was actually opened with.
            printer_started_at=(
                event.printer_started_at
                if event.printer_started_at is not None
                else job.printer_started_at
            ),
            printer_ended_at=event.printer_ended_at,
        )

        reason = _REVIEW_REASONS.get(event.outcome)
        if reason is None:
            # FINISHED. UC-04 owns the whole write: the ending's row, the deduction and
            # the idempotency flag commit in one unit — saving the row here first would
            # put a second delivery's stale claims outside the guard.
            await self.record_print_consumption.execute(ended)
            return ended.id

        try:
            # UC-05 saves the job and the review in one unit, so the ending and the
            # decision it demands land together or not at all.
            await self.open_pending_review.execute(
                OpenPendingReviewCommand(job=ended, reason=reason)
            )
        except ReviewAlreadyPendingError:
            # Two deliveries of one ending can race: both correlate to the same RUNNING
            # job before either writes, the first opens the review, and the second finds
            # it already open. The decision item exists — a second card would split one
            # decision across two — so this is a line in the log, never a crash.
            LOGGER.warning(
                "job %s (%s) already has a pending review; duplicate %s event ignored",
                ended.id,
                ended.name,
                event.outcome.value,
            )
        return ended.id

    async def _is_newest(self, printer: PrinterSerial, job: PrintJob) -> bool:
        """Whether this running row is the last thing that happened on the machine.

        **A running row is not always the print a machine is running.** Upstream announces a
        start per attempt, and it announced two for one job on the reference machine — 22:07:51
        and 22:15:38 UTC on 2026-08-08, same file, no ending between them. The older row is
        then a leftover: `_running_job` closes the newest, and the stale one is left standing,
        which is what this ledger has always done with an ending that never arrived.

        Left standing is harmless until something reads a *level* and offers to close it. The
        startup pass does exactly that, on every restart, for as long as the row exists — so
        without this check the first restart after that print finished would close the stale
        row too and charge the machine's current plan a second time. One physical print, two
        deductions, and no way to tell the duplicate from a real entry afterwards.

        Announced endings are deliberately not subject to this. One of those is a discrete
        event that just fired, so the newest running row *is* what it describes; an inference
        is read off a level that says nothing about which of two rows it belongs to, and when
        a later job has already concluded on that machine the level is describing the concluded
        one.
        """
        recent = await self.jobs.list_recent(1, printer=printer)
        return bool(recent) and recent[0].id == job.id

    async def _already_ended(self, event: PrintEnded, now: datetime) -> PrintJob | None:
        """This machine's newest job when it is the print this ending is already recorded
        for — otherwise `None`, and the caller opens the row a swallowed start needs.

        Three conditions, and each one is load-bearing:

        - **Newest, and terminal.** A job still running is not what this asks about; the
          caller looked for that first and found nothing.
        - **Ended within `DUPLICATE_ENDING_WINDOW`.** The two signals race by seconds. A
          job that stopped an hour ago is history, and a fresh ending naming it would be a
          new print of the same file — which is a row, not a duplicate.
        - **The same name.** Two prints can finish minutes apart, and the window alone
          would swallow the second one's ending whole. The name is the only thing an
          ending carries that distinguishes them.

        `ended_at` is the ledger's own clock, the one `now` comes from, so the comparison
        stays inside one clock — the printer's pair is deliberately not consulted here for
        the reason `PrintJob` gives about foreign clocks.
        """
        recent = await self.jobs.list_recent(1, printer=event.printer)
        if not recent:
            return None
        newest = recent[0]
        if not newest.state.is_terminal or newest.ended_at is None:
            return None
        if newest.name != event.name:
            return None
        if now - newest.ended_at > DUPLICATE_ENDING_WINDOW:
            return None
        return newest

    async def _running_job(self, printer: PrinterSerial) -> PrintJob | None:
        """The newest RUNNING job **on this machine** — the one its terminal event belongs to.

        Correlation by state rather than by an in-memory id, deliberately: memory does
        not survive a restart, and the row does. If several RUNNING rows exist for one
        machine — endings that never arrived — the newest is the one that just stopped;
        the stale ones stay verbatim, reclassifiable later.

        **By state *and by machine*, because state alone stopped being an identity.** With
        two printers the newest RUNNING row is as likely to be the other one's job as this
        one's, and closing it would write this ending's per-tray figures onto that job — so
        UC-04 deducts these grams from the spools in *that* machine's trays, and the ledger
        reports both printers wrongly while flagging neither.

        A row that names no printer matches nothing here, which is the one behaviour a
        migrated ledger notices: the single print that spanned the upgrade to 0008 has a
        nameless RUNNING row, its ending opens a fresh one, and the stale row is left as
        every uncorrelated ending already leaves one. That costs a duration and a plan for
        one job, once. Letting it match would buy them back by guessing which machine a row
        belongs to that explicitly does not say — and guessing is the entire failure this
        method exists to end. `PrintJob.printer` states the same choice from the other side.
        """
        for job in await self.jobs.list_recent(CORRELATION_WINDOW, printer=printer):
            if job.state is PrintJobState.RUNNING:
                return job
        return None


def _same_print(job: PrintJob, event: PrintStarted, now: datetime) -> bool:
    """Whether the row already open describes the print this start is announcing.

    **The machine's own `start_time` decides it whenever both sides carry one.** That figure
    is the only job identity upstream publishes: it does not move for the length of a print,
    and on the reference instance it held `2026-08-11T16:38:23+00:00` across both a
    twelve-second dropout and a Home Assistant restart while the status sensor, the name
    sensors and the bus events all reset (docs/12-field-notes.md, 2026-08-11).

    **The name is deliberately not consulted**, on either path. It moves during a job —
    `UNKNOWN_JOB_NAME` while every naming sensor is unavailable, the file name once one
    speaks — so a row opened before its name resolved would fail an equality test against
    its own print and be duplicated.

    **With no timestamp to compare, the answer depends on who is speaking**, because the two
    starts are wrong about different things:

    - *Inferred.* The level says `printing` for the whole job and is re-read on every
      transition, so an inference arriving at a machine that already has a row is
      overwhelmingly that row's own print — ten `offline → running` bounces inside one
      thirty-six-minute job on the reference machine, every one of them a fresh arrival at
      `running`. It answers `True` and does nothing. The cost of being wrong is that the
      ledger stays blind to one job, which is the state it was already in and which the next
      announced event repairs. The cost of the opposite mistake is a duplicate row per
      bounce, each charged in full.
    - *Announced.* This is a discrete event that fired because the machine said a print
      began, so it may open a row — unless it is racing the inference that just opened one
      for the very same print, which on a healthy start it always is.
      `SIMULTANEOUS_START_WINDOW` is that race, and the eight minutes between upstream's two
      announcements for one job on 2026-08-08 is what it must stay clear of.

    Under-claiming is the standing policy at this boundary; double-charging is the one
    failure this module exists to prevent.
    """
    if job.printer_started_at is not None and event.printer_started_at is not None:
        return abs(job.printer_started_at - event.printer_started_at) <= JOB_IDENTITY_TOLERANCE
    if event.derived:
        return True
    return now - job.started_at <= SIMULTANEOUS_START_WINDOW
