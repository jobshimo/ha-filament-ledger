"""SQLite implementation of `ReviewRepository`.

The entity's lines split across two JSON columns, and the split follows the two facts a
line holds. `estimated_usage` is the per-tray figure, because the printer reports one
figure per tray. `slot_resolution` is the attribution, a list carrying a `spool_id` since
migration 0004, because one tray's figure may belong to more than one spool. Both name
their tray in full since migration 0007 (`tray_json`). Hydration re-joins them by tray; a
tray with no entry in the list is a tray that froze without a spool, which is the fact the
queue exists to ask about.

The upsert deliberately updates only what a decision changes. `job_id`, `reason`,
`estimated_usage`, `estimator_used` and `opened_at` are facts about the moment the review
opened; no entity transition touches them, and leaving them out of the UPDATE keeps this
adapter unable to express a rewrite of history even by accident. (`slot_resolution` *is*
updated: UC-06 records the attribution actually used, which is a decision, not a rewrite.)
The one-pending-per-job rule is the partial unique index's to enforce at this layer —
UC-05 says it first, in the language of the problem.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from ...domain.model.pending_review import PendingReview, ReviewCharge, ReviewLine
from ...domain.value.grams import Grams
from ...domain.value.identifiers import PrintJobId, ReviewId, SpoolId, TrayRef
from ...domain.value.review import EstimatorKind, ReviewReason, ReviewState
from .database import Database
from .tray_json import tray_fields, tray_from

COLUMNS = (
    "id, job_id, reason, estimated_usage, confirmed_usage, slot_resolution, "
    "estimator_used, state, opened_at, resolved_at, resolution_note"
)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _lines_to_columns(lines: tuple[ReviewLine, ...]) -> tuple[str, str]:
    estimated = [{**tray_fields(line.tray), "mg": line.estimated.milligrams} for line in lines]
    charges = [
        {**tray_fields(line.tray), "spool_id": charge.spool_id, "mg": charge.amount.milligrams}
        for line in lines
        for charge in line.charges
    ]
    return json.dumps(estimated), json.dumps(charges)


def _lines_from_columns(estimated_json: str, charges_json: str) -> tuple[ReviewLine, ...]:
    """Re-join the two columns by tray.

    The estimate list is what decides which lines exist: a charge names a tray the estimate
    already covers, and the entity refuses a line for a tray nobody estimated. So a stray
    entry pointing at an unknown tray is dropped rather than resurrected as a line with no
    figure behind it.
    """
    charges: dict[TrayRef, list[ReviewCharge]] = {}
    for entry in json.loads(charges_json):
        charges.setdefault(tray_from(entry), []).append(
            ReviewCharge(spool_id=SpoolId(entry["spool_id"]), amount=Grams(int(entry["mg"])))
        )
    estimated = {tray_from(entry): Grams(int(entry["mg"])) for entry in json.loads(estimated_json)}
    return tuple(
        ReviewLine(tray=tray, estimated=amount, charges=tuple(charges.get(tray, ())))
        for tray, amount in sorted(estimated.items())
    )


def _confirmed_to_json(confirmed: dict[TrayRef, Grams] | None) -> str | None:
    if confirmed is None:
        return None
    return json.dumps(
        [{**tray_fields(tray), "mg": grams.milligrams} for tray, grams in sorted(confirmed.items())]
    )


def _confirmed_from_json(text: str | None) -> dict[TrayRef, Grams] | None:
    if text is None:
        return None
    return {tray_from(entry): Grams(int(entry["mg"])) for entry in json.loads(text)}


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
        estimated, charges = _lines_to_columns(review.lines)
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
                charges,
                review.estimator_used.value,
                review.state.value,
                _iso(review.opened_at),
                _iso(review.resolved_at) if review.resolved_at else None,
                review.resolution_note,
            ),
        )
