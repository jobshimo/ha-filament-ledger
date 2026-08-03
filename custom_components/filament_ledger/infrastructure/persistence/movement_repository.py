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
from ...domain.value.grams import Grams
from ...domain.value.identifiers import MovementId, PrintJobId, ReviewId, SpoolId
from ...domain.value.movement_type import MovementSource, MovementType
from .database import Database

COLUMNS = "id, spool_id, type, amount_mg, source, occurred_at, recorded_at, job_id, review_id, note"


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
    )


@dataclass(frozen=True, slots=True)
class SqliteMovementRepository:
    database: Database

    async def append(self, movement: Movement) -> None:
        await self.database.execute(
            f"INSERT INTO movement ({COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?)",
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
            ),
        )

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

    async def list_recent(self, limit: int) -> list[Movement]:
        """Newest first, across every spool — the global history view's read.

        `rowid DESC` breaks timestamp ties by insertion order reversed, so two entries
        written in one transaction render in the order they happened, not arbitrarily.
        """
        rows = await self.database.fetch_all(
            f"SELECT {COLUMNS} FROM movement ORDER BY occurred_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        return [_to_movement(row) for row in rows]

    async def count_for_spool(self, spool_id: SpoolId) -> int:
        row = await self.database.fetch_one(
            "SELECT COUNT(*) AS n FROM movement WHERE spool_id = ?", (spool_id,)
        )
        return int(row["n"]) if row else 0
