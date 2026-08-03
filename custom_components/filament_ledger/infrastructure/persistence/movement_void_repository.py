"""SQLite implementation of `MovementVoidRepository`.

The one table in this design that is written after insert — and deliberately not the
ledger. `record_reinstatement` updates two columns on a *status record*; the movements it
points at are never touched, so migration 0001's triggers are never confronted
(docs/adr/0007, docs/14 §14.4.1).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from ...domain.model.movement_void import MovementVoid
from ...domain.value.identifiers import MovementId
from .database import Database

COLUMNS = (
    "movement_id, voided_at, reason, reversal_movement_id, reinstated_at, reinstatement_movement_id"
)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _to_void(row: sqlite3.Row) -> MovementVoid:
    voided_at = _parse(row["voided_at"])
    if voided_at is None:  # pragma: no cover - NOT NULL in the schema
        msg = f"void of movement {row['movement_id']} has no voided_at"
        raise ValueError(msg)
    return MovementVoid(
        movement_id=MovementId(row["movement_id"]),
        voided_at=voided_at,
        reason=row["reason"],
        reversal_movement_id=(
            MovementId(row["reversal_movement_id"]) if row["reversal_movement_id"] else None
        ),
        reinstated_at=_parse(row["reinstated_at"]),
        reinstatement_movement_id=(
            MovementId(row["reinstatement_movement_id"])
            if row["reinstatement_movement_id"]
            else None
        ),
    )


@dataclass(frozen=True, slots=True)
class SqliteMovementVoidRepository:
    database: Database

    async def append(self, void: MovementVoid) -> None:
        """A plain INSERT, never an upsert.

        The primary key is the rule — a movement is voided at most once, ever — so a
        second void must fail loudly rather than overwrite the first chapter. The use case
        checks first so the user reads a sentence instead of a constraint name; this is
        the layer that makes the sentence true.
        """
        await self.database.execute(
            f"INSERT INTO movement_void ({COLUMNS}) VALUES (?,?,?,?,?,?)",
            (
                void.movement_id,
                _iso(void.voided_at),
                void.reason,
                void.reversal_movement_id,
                _iso(void.reinstated_at) if void.reinstated_at else None,
                void.reinstatement_movement_id,
            ),
        )

    async def get(self, movement_id: MovementId) -> MovementVoid | None:
        row = await self.database.fetch_one(
            f"SELECT {COLUMNS} FROM movement_void WHERE movement_id = ?", (movement_id,)
        )
        return _to_void(row) if row else None

    async def list_open(self) -> list[MovementVoid]:
        """Every chapter still out, newest first — the Trash's order and the hide set.

        `idx_void_open` is the partial index this read exists for; the ordering is by when
        the deletion happened, because the Trash is a list of recent regrets.
        """
        rows = await self.database.fetch_all(
            f"SELECT {COLUMNS} FROM movement_void "
            f"WHERE reinstatement_movement_id IS NULL ORDER BY voided_at DESC, rowid DESC"
        )
        return [_to_void(row) for row in rows]

    async def record_reinstatement(
        self, movement_id: MovementId, reinstatement_id: MovementId, at: datetime
    ) -> None:
        """Close the chapter, and only one that is open.

        The `reinstatement_movement_id IS NULL` predicate makes closing idempotent-safe
        under concurrency: a second closer updates nothing rather than rewriting the first
        one's link. The use case has already refused the double-restore in language the
        user can read; this is the same rule at the last possible layer.
        """
        await self.database.execute(
            "UPDATE movement_void SET reinstated_at = ?, reinstatement_movement_id = ? "
            "WHERE movement_id = ? AND reinstatement_movement_id IS NULL",
            (_iso(at), reinstatement_id, movement_id),
        )
