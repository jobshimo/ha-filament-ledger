"""SQLite implementation of `MovementRepository`.

Append and queries. There is no `update` and no `delete` here for the same reason there is
none on the port: the interface makes the invariant unexpressible, and the database triggers
in migration 0001 make it true at the last possible layer as well.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from ...domain.model.movement import Movement
from ...domain.port.repositories import NO_FILTERS, MovementFilter
from ...domain.value.grams import Grams
from ...domain.value.identifiers import MovementId, PrintJobId, ReviewId, SpoolId
from ...domain.value.movement_type import MovementSource, MovementType
from .database import Database

COLUMNS = (
    "id, spool_id, type, amount_mg, source, occurred_at, recorded_at, job_id, review_id, note, "
    # Migration 0003's link columns. Written here at INSERT and nowhere else, ever — which
    # is what keeps the immutability triggers from ever being confronted by a correction
    # (docs/14 §14.7, docs/adr/0007).
    "reassigns_movement_id, reinstates_movement_id"
)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _to_movement(row: sqlite3.Row) -> Movement:
    return Movement(
        id=MovementId(row["id"]),
        spool_id=SpoolId(row["spool_id"]),
        type=MovementType(row["type"]),
        amount=Grams(row["amount_mg"]),
        source=MovementSource(row["source"]),
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        recorded_at=datetime.fromisoformat(row["recorded_at"]),
        note=row["note"],
        job_id=PrintJobId(row["job_id"]) if row["job_id"] else None,
        review_id=ReviewId(row["review_id"]) if row["review_id"] else None,
        reassigns_movement_id=(
            MovementId(row["reassigns_movement_id"]) if row["reassigns_movement_id"] else None
        ),
        reinstates_movement_id=(
            MovementId(row["reinstates_movement_id"]) if row["reinstates_movement_id"] else None
        ),
    )


def _where(criteria: MovementFilter) -> tuple[str, list[object]]:
    """The history filter, as SQL. Empty criteria produce no clause at all.

    Every predicate is pushed into the database rather than applied to a fetched list.
    A ledger grows without bound, and a read that loaded it whole to keep a handful of rows
    is a read that works for a year; the limit has to apply to what matched.

    **None of these predicates is covered by an index**, and that is a deliberate reading
    of the schema rather than an oversight. The global history has always ordered the whole
    table by `occurred_at DESC` with no index on that column alone — migration 0001 indexes
    `(spool_id, occurred_at)` and `job_id`, and 0003 added none to this table — so the read
    was already a scan and a sort, and the filters add tests to rows the query was visiting
    anyway. `LIKE '%…%'` is the one that could never be indexed at all: no B-tree answers
    an unanchored substring. At a household ledger's size — thousands of rows rather than
    millions — that is a cost measured in single-digit milliseconds. Stated here rather
    than left to be discovered: if this read ever becomes slow, the answer is an index on
    `occurred_at`, and free text is the predicate to reach for last.
    """
    clauses: list[str] = []
    params: list[object] = []
    if criteria.since is not None:
        # String comparison, which is exactly why every timestamp in this schema is written
        # through `_iso`: one UTC ISO-8601 layout sorts lexicographically in the order it
        # sorts chronologically. `list_since` has relied on that since the first migration.
        clauses.append("occurred_at >= ?")
        params.append(_iso(criteria.since))
    if criteria.until is not None:
        clauses.append("occurred_at <= ?")
        params.append(_iso(criteria.until))
    if criteria.colours:
        # A movement has no colour; the spool it names has one. Sorted so that the same
        # filter is always the same statement with the same parameter order.
        placeholders = ",".join("?" * len(criteria.colours))
        clauses.append(f"spool_id IN (SELECT id FROM spool WHERE colour IN ({placeholders}))")
        params.extend(sorted(colour.hex8 for colour in criteria.colours))
    if criteria.min_magnitude is not None:
        # **abs, not the stored value.** Amounts are signed — a print consumption is
        # −84 100 mg — and "more than 50 g" is a question about how much filament moved.
        # Comparing the signed column would answer it with every increase in the ledger and
        # no print at all, which is the failure a reader would never think to look for.
        clauses.append("abs(amount_mg) >= ?")
        params.append(criteria.min_magnitude.milligrams)
    if criteria.max_magnitude is not None:
        clauses.append("abs(amount_mg) <= ?")
        params.append(criteria.max_magnitude.milligrams)
    if criteria.search:
        # The note is on the row; the job name is one primary-key hop away. A correlated
        # subquery rather than a join keeps the SELECT list — and therefore the unfiltered
        # statement — exactly as it was, with no column names to disambiguate.
        clauses.append(
            "(COALESCE(note,'') LIKE ? "
            "OR COALESCE((SELECT name FROM print_job WHERE id = job_id),'') LIKE ?)"
        )
        needle = f"%{criteria.search}%"
        params.extend([needle, needle])
    return (f" WHERE {' AND '.join(clauses)}" if clauses else "", params)


@dataclass(frozen=True, slots=True)
class SqliteMovementRepository:
    database: Database

    async def append(self, movement: Movement) -> None:
        await self.database.execute(
            f"INSERT INTO movement ({COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                movement.id,
                movement.spool_id,
                movement.type.value,
                movement.amount.milligrams,
                movement.source.value,
                _iso(movement.occurred_at),
                _iso(movement.recorded_at),
                movement.job_id,
                movement.review_id,
                movement.note,
                movement.reassigns_movement_id,
                movement.reinstates_movement_id,
            ),
        )

    async def get(self, movement_id: MovementId) -> Movement | None:
        row = await self.database.fetch_one(
            f"SELECT {COLUMNS} FROM movement WHERE id = ?", (movement_id,)
        )
        return _to_movement(row) if row else None

    async def list_for_spool(self, spool_id: SpoolId) -> list[Movement]:
        """Oldest first — the order the running balance is derived in."""
        rows = await self.database.fetch_all(
            f"SELECT {COLUMNS} FROM movement WHERE spool_id = ? ORDER BY occurred_at, rowid",
            (spool_id,),
        )
        return [_to_movement(row) for row in rows]

    async def list_since(self, spool_id: SpoolId, moment: datetime) -> list[Movement]:
        rows = await self.database.fetch_all(
            f"SELECT {COLUMNS} FROM movement "
            f"WHERE spool_id = ? AND occurred_at > ? ORDER BY occurred_at, rowid",
            (spool_id, _iso(moment)),
        )
        return [_to_movement(row) for row in rows]

    async def list_recent(
        self, limit: int, criteria: MovementFilter = NO_FILTERS
    ) -> list[Movement]:
        """Newest first, across every spool — the global history view's read.

        `rowid DESC` breaks timestamp ties by insertion order reversed, so two entries
        written in one transaction render in the order they happened, not arbitrarily.

        The criteria narrow the slice **before** the limit takes the newest of it, which is
        the whole point of pushing them into SQL: a filtered history must show the newest
        hundred entries *that match*, not whatever matches within the newest hundred.
        """
        where, params = _where(criteria)
        rows = await self.database.fetch_all(
            f"SELECT {COLUMNS} FROM movement{where} ORDER BY occurred_at DESC, rowid DESC LIMIT ?",
            [*params, limit],
        )
        return [_to_movement(row) for row in rows]

    async def list_in_period(self, since: datetime | None) -> list[Movement]:
        """Oldest first, bounded by time rather than by a row count.

        The comparison is a string comparison, which is exactly why every timestamp in
        this schema is written through `_iso`: the same UTC ISO-8601 layout sorts
        lexicographically in the order it sorts chronologically. `list_since` above has
        relied on that since the first migration; this read is the cross-spool form.
        """
        if since is None:
            rows = await self.database.fetch_all(
                f"SELECT {COLUMNS} FROM movement ORDER BY occurred_at, rowid"
            )
        else:
            rows = await self.database.fetch_all(
                f"SELECT {COLUMNS} FROM movement "
                f"WHERE occurred_at >= ? ORDER BY occurred_at, rowid",
                (_iso(since),),
            )
        return [_to_movement(row) for row in rows]

    async def count_for_spool(self, spool_id: SpoolId) -> int:
        row = await self.database.fetch_one(
            "SELECT COUNT(*) AS n FROM movement WHERE spool_id = ?", (spool_id,)
        )
        return int(row["n"]) if row else 0
