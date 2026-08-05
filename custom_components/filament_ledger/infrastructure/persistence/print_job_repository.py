"""SQLite implementation of `PrintJobRepository`.

Upsert on every save: a job's state and counters evolve as the printer reports, and this
table records the latest claim. The per-slot maps travel as JSON milligram integers —
`{"1": 40000}` — because the milligram is the ledger's unit and a float in a stored
document would reintroduce exactly the drift `Grams` exists to prevent.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from ...domain.model.print_job import PrintJob
from ...domain.value.grams import Grams
from ...domain.value.identifiers import PrintJobId, SlotIndex
from ...domain.value.percentage import Percentage
from ...domain.value.print_job_state import PrintJobState
from .database import Database

COLUMNS = (
    "id, name, state, started_at, ended_at, layer_reached, total_layers, "
    "progress_pct, reported_usage, raw_gcode_state, raw_print_error, "
    "printer_started_at, printer_ended_at, consumption_recorded"
)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def usage_to_json(usage: dict[SlotIndex, Grams] | None) -> str | None:
    """`None` stays NULL. A missing per-tray figure and an empty report are different
    facts, and collapsing them here would undo the distinction the nullable column and
    the entity both keep (docs/04-use-cases.md UC-04)."""
    if usage is None:
        return None
    return json.dumps({str(slot.value): grams.milligrams for slot, grams in sorted(usage.items())})


def usage_from_json(text: str | None) -> dict[SlotIndex, Grams] | None:
    if text is None:
        return None
    return {SlotIndex(int(slot)): Grams(int(mg)) for slot, mg in json.loads(text).items()}


def _to_job(row: sqlite3.Row) -> PrintJob:
    started = _parse(row["started_at"])
    if started is None:  # pragma: no cover - NOT NULL in the schema
        msg = f"print job {row['id']} has no started_at"
        raise ValueError(msg)
    progress = row["progress_pct"]
    return PrintJob(
        id=PrintJobId(row["id"]),
        name=row["name"],
        state=PrintJobState(row["state"]),
        started_at=started,
        ended_at=_parse(row["ended_at"]),
        layer_reached=row["layer_reached"],
        total_layers=row["total_layers"],
        progress=Percentage.of(progress) if progress is not None else None,
        reported_usage=usage_from_json(row["reported_usage"]),
        raw_gcode_state=row["raw_gcode_state"],
        raw_print_error=row["raw_print_error"],
        printer_started_at=_parse(row["printer_started_at"]),
        printer_ended_at=_parse(row["printer_ended_at"]),
        consumption_recorded=bool(row["consumption_recorded"]),
    )


@dataclass(frozen=True, slots=True)
class SqlitePrintJobRepository:
    database: Database

    async def get(self, job_id: PrintJobId) -> PrintJob | None:
        row = await self.database.fetch_one(
            f"SELECT {COLUMNS} FROM print_job WHERE id = ?", (job_id,)
        )
        return _to_job(row) if row else None

    async def save(self, job: PrintJob) -> None:
        await self.database.execute(
            f"""
            INSERT INTO print_job ({COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                state = excluded.state,
                started_at = excluded.started_at,
                ended_at = excluded.ended_at,
                layer_reached = excluded.layer_reached,
                total_layers = excluded.total_layers,
                progress_pct = excluded.progress_pct,
                reported_usage = excluded.reported_usage,
                raw_gcode_state = excluded.raw_gcode_state,
                raw_print_error = excluded.raw_print_error,
                printer_started_at = excluded.printer_started_at,
                printer_ended_at = excluded.printer_ended_at,
                consumption_recorded = excluded.consumption_recorded
            """,
            (
                job.id,
                job.name,
                job.state.value,
                _iso(job.started_at),
                _iso(job.ended_at) if job.ended_at else None,
                job.layer_reached,
                job.total_layers,
                float(job.progress.value) if job.progress is not None else None,
                usage_to_json(job.reported_usage),
                job.raw_gcode_state,
                job.raw_print_error,
                # The printer's own pair, normalised to UTC like every other instant here.
                # `_iso` shifts the offset and never the moment, so the subtraction that
                # reads them back is the machine's own elapsed time to the second.
                _iso(job.printer_started_at) if job.printer_started_at else None,
                _iso(job.printer_ended_at) if job.printer_ended_at else None,
                int(job.consumption_recorded),
            ),
        )

    async def list_recent(self, limit: int) -> list[PrintJob]:
        """Newest first — the order a history panel reads. Ties (a burst of jobs sharing a
        timestamp) break on insertion order so the listing is stable across calls."""
        rows = await self.database.fetch_all(
            f"SELECT {COLUMNS} FROM print_job ORDER BY started_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        return [_to_job(row) for row in rows]

    async def list_in_period(self, since: datetime | None) -> list[PrintJob]:
        """Oldest first, by start. The ISO layout `_iso` writes sorts chronologically as
        a string, which is what makes the bound a plain comparison."""
        if since is None:
            rows = await self.database.fetch_all(
                f"SELECT {COLUMNS} FROM print_job ORDER BY started_at, rowid"
            )
        else:
            rows = await self.database.fetch_all(
                f"SELECT {COLUMNS} FROM print_job WHERE started_at >= ? ORDER BY started_at, rowid",
                (_iso(since),),
            )
        return [_to_job(row) for row in rows]
