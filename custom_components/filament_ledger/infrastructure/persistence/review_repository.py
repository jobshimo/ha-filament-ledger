"""SQLite implementation of `ReviewRepository`.

The entity's per-slot lines split across two JSON columns — `estimated_usage` and
`slot_resolution` — exactly as migration 0001 lays them out. Both are written from the
same lines, so their key sets are identical by construction and hydration re-joins them
by slot without a reconciliation step.

The upsert deliberately updates only what resolution changes. `job_id`, `reason`,
`estimated_usage`, `estimator_used` and `opened_at` are facts about the moment the review
opened; no entity transition touches them, and leaving them out of the UPDATE keeps this
adapter unable to express a rewrite of history even by accident. (`slot_resolution` *is*
updated: UC-06 records the resolutions actually used, which is a decision, not a rewrite.) The one-pending-per-job rule is the partial
unique index's to enforce at this layer — UC-05 says it first, in the language of the
problem.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from ...domain.model.pending_review import PendingReview, ReviewLine
from ...domain.value.grams import Grams
from ...domain.value.identifiers import PrintJobId, ReviewId, SlotIndex, SpoolId
from ...domain.value.review import EstimatorKind, ReviewReason, ReviewState
from .database import Database

COLUMNS = (
    "id, job_id, reason, estimated_usage, confirmed_usage, slot_resolution, "
    "estimator_used, state, opened_at, resolved_at, resolution_note"
)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _lines_to_columns(lines: tuple[ReviewLine, ...]) -> tuple[str, str]:
    estimated = {str(line.slot.value): line.estimated.milligrams for line in lines}
    resolution = {str(line.slot.value): line.spool_id for line in lines}
    return json.dumps(estimated), json.dumps(resolution)


def _lines_from_columns(estimated_json: str, resolution_json: str) -> tuple[ReviewLine, ...]:
    estimated = json.loads(estimated_json)
    resolution = json.loads(resolution_json)
    return tuple(
        ReviewLine(
            slot=SlotIndex(int(slot)),
            estimated=Grams(int(mg)),
            spool_id=SpoolId(resolution[slot]) if resolution.get(slot) is not None else None,
        )
        for slot, mg in sorted(estimated.items(), key=lambda item: int(item[0]))
    )


def _confirmed_to_json(confirmed: dict[SlotIndex, Grams] | None) -> str | None:
    if confirmed is None:
        return None
    return json.dumps(
        {str(slot.value): grams.milligrams for slot, grams in sorted(confirmed.items())}
    )


def _confirmed_from_json(text: str | None) -> dict[SlotIndex, Grams] | None:
    if text is None:
        return None
    return {SlotIndex(int(slot)): Grams(int(mg)) for slot, mg in json.loads(text).items()}


def _to_review(row: sqlite3.Row) -> PendingReview:
    opened = _parse(row["opened_at"])
    if opened is None:  # pragma: no cover - NOT NULL in the schema
        msg = f"review {row['id']} has no opened_at"
        raise ValueError(msg)
    return PendingReview(
        id=ReviewId(row["id"]),
        job_id=PrintJobId(row["job_id"]),
        reason=ReviewReason(row["reason"]),
        lines=_lines_from_columns(row["estimated_usage"], row["slot_resolution"]),
        estimator_used=EstimatorKind(row["estimator_used"]),
        state=ReviewState(row["state"]),
        opened_at=opened,
        confirmed_usage=_confirmed_from_json(row["confirmed_usage"]),
        resolved_at=_parse(row["resolved_at"]),
        resolution_note=row["resolution_note"],
    )


@dataclass(frozen=True, slots=True)
class SqliteReviewRepository:
    database: Database

    async def get(self, review_id: ReviewId) -> PendingReview | None:
        row = await self.database.fetch_one(
            f"SELECT {COLUMNS} FROM pending_review WHERE id = ?", (review_id,)
        )
        return _to_review(row) if row else None

    async def list_pending(self) -> list[PendingReview]:
        """Oldest first: the queue asks for decisions in the order the doubts arose."""
        rows = await self.database.fetch_all(
            f"SELECT {COLUMNS} FROM pending_review WHERE state = ? ORDER BY opened_at, rowid",
            (ReviewState.PENDING.value,),
        )
        return [_to_review(row) for row in rows]

    async def list_resolved(self, since: datetime | None) -> list[PendingReview]:
        """Oldest resolution first. The state filter and the timestamp filter say the same
        thing twice on purpose: the entity refuses to hold one without the other, and a row
        that somehow held only one would be a record contradicting itself rather than a
        review this count should include."""
        sql = f"SELECT {COLUMNS} FROM pending_review WHERE state != ? AND resolved_at IS NOT NULL"
        params: list[object] = [ReviewState.PENDING.value]
        if since is not None:
            sql += " AND resolved_at >= ?"
            params.append(_iso(since))
        rows = await self.database.fetch_all(f"{sql} ORDER BY resolved_at, rowid", params)
        return [_to_review(row) for row in rows]

    async def save(self, review: PendingReview) -> None:
        estimated, resolution = _lines_to_columns(review.lines)
        await self.database.execute(
            f"""
            INSERT INTO pending_review ({COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                confirmed_usage = excluded.confirmed_usage,
                slot_resolution = excluded.slot_resolution,
                state = excluded.state,
                resolved_at = excluded.resolved_at,
                resolution_note = excluded.resolution_note
            """,
            (
                review.id,
                review.job_id,
                review.reason.value,
                estimated,
                _confirmed_to_json(review.confirmed_usage),
                resolution,
                review.estimator_used.value,
                review.state.value,
                _iso(review.opened_at),
                _iso(review.resolved_at) if review.resolved_at else None,
                review.resolution_note,
            ),
        )
