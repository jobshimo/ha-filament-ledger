"""SQLite implementation of `SpoolRepository`."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from ...domain.model.spool import Spool
from ...domain.port.repositories import SpoolFilter
from ...domain.value.colour import Colour
from ...domain.value.grams import Grams
from ...domain.value.identifiers import (
    ABSENT_TAG_SENTINEL,
    SlotIndex,
    SpoolId,
    TagSource,
    TagUid,
)
from ...domain.value.location import AmsSlot, ExternalSpool, Location, Storage
from ...domain.value.material import Material, MaterialKind
from .database import Database

COLUMNS = (
    "id, material, material_other, colour, vendor, label, opening_weight_mg, "
    "core_weight_mg, location_kind, location_slot, tag_uid, tag_source, registered_at, "
    "discarded_at"
)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _location_columns(location: Location) -> tuple[str, int | None]:
    match location:
        case AmsSlot(slot):
            return "AMS_SLOT", slot.value
        case ExternalSpool():
            return "EXTERNAL_SPOOL", None
        case Storage():
            return "STORAGE", None


def _location_from(kind: str, slot: int | None) -> Location:
    if kind == "AMS_SLOT" and slot is not None:
        return AmsSlot(SlotIndex(slot))
    if kind == "EXTERNAL_SPOOL":
        return ExternalSpool()
    return Storage()


def _tag_from(value: str | None) -> TagUid | None:
    """The sentinel is tolerated on the way out, not only refused on the way in.

    Rows saved before `TagUid` refused sixteen zeros — or restored from a backup that
    predates migration 0002's scrub — must hydrate as untagged spools. Raising here would
    fail every list and get, and with them the coordinator and the whole entry: one legacy
    row must not take the ledger down.
    """
    if not value or value == ABSENT_TAG_SENTINEL:
        return None
    return TagUid(value)


def _tag_source_from(value: str | None, tag: TagUid | None) -> TagSource | None:
    """The pairing, restored on the way out — the domain refuses a half-set pair.

    Migration 0003 backfills every tagged row as MANUAL, so a tagged row with no
    provenance should not exist. Should one turn up anyway — a restored backup written
    between the ALTER and the UPDATE, a row inserted by hand — it hydrates as MANUAL for
    the same reason the migration chose MANUAL, and for the reason `_tag_from` tolerates
    the sentinel: one odd row must not fail every list and get, and with them the
    coordinator and the whole entry. A source stored against no tag is dropped: the tag is
    the fact, the provenance only describes it.
    """
    if tag is None:
        return None
    return TagSource(value) if value else TagSource.MANUAL


def _to_spool(row: sqlite3.Row) -> Spool:
    kind = MaterialKind(row["material"])
    registered = _parse(row["registered_at"])
    if registered is None:  # pragma: no cover - NOT NULL in the schema
        msg = f"spool {row['id']} has no registered_at"
        raise ValueError(msg)
    tag = _tag_from(row["tag_uid"])
    return Spool(
        id=SpoolId(row["id"]),
        material=Material(kind, row["material_other"]),
        colour=Colour.parse(row["colour"]),
        opening_weight=Grams(row["opening_weight_mg"]),
        core_weight=Grams(row["core_weight_mg"]),
        location=_location_from(row["location_kind"], row["location_slot"]),
        registered_at=registered,
        vendor=row["vendor"],
        label=row["label"],
        tag_uid=tag,
        tag_source=_tag_source_from(row["tag_source"], tag),
        discarded_at=_parse(row["discarded_at"]),
    )


@dataclass(frozen=True, slots=True)
class SqliteSpoolRepository:
    database: Database

    async def get(self, spool_id: SpoolId) -> Spool | None:
        row = await self.database.fetch_one(
            f"SELECT {COLUMNS} FROM spool WHERE id = ?", (spool_id,)
        )
        return _to_spool(row) if row else None

    async def find_by_tag(self, tag: TagUid) -> list[Spool]:
        """Every non-discarded spool carrying this tag — plural, because duplicates are
        legal and the caller has to be told when the answer is ambiguous."""
        rows = await self.database.fetch_all(
            f"SELECT {COLUMNS} FROM spool WHERE tag_uid = ? AND discarded_at IS NULL",
            (tag.value,),
        )
        return [_to_spool(row) for row in rows]

    async def find_by_location(self, location: Location) -> Spool | None:
        kind, slot = _location_columns(location)
        if kind == "STORAGE":
            # Storage is not a unique position; "which spool is in storage" has no answer.
            return None
        row = await self.database.fetch_one(
            f"SELECT {COLUMNS} FROM spool "
            f"WHERE location_kind = ? AND (location_slot IS ? OR location_slot = ?) "
            f"AND discarded_at IS NULL",
            (kind, slot, slot),
        )
        return _to_spool(row) if row else None

    async def list(self, criteria: SpoolFilter) -> list[Spool]:
        clauses: list[str] = []
        params: list[object] = []
        if not criteria.include_discarded:
            clauses.append("discarded_at IS NULL")
        if criteria.mounted_only:
            clauses.append("location_kind != 'STORAGE'")
        if criteria.search:
            clauses.append("(COALESCE(label,'') LIKE ? OR COALESCE(vendor,'') LIKE ?)")
            needle = f"%{criteria.search}%"
            params.extend([needle, needle])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self.database.fetch_all(
            f"SELECT {COLUMNS} FROM spool{where} ORDER BY registered_at", params
        )
        return [_to_spool(row) for row in rows]

    async def save(self, spool: Spool) -> None:
        kind, slot = _location_columns(spool.location)
        await self.database.execute(
            """
            INSERT INTO spool (
                id, material, material_other, colour, vendor, label,
                opening_weight_mg, core_weight_mg, location_kind, location_slot,
                tag_uid, tag_source, registered_at, discarded_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                material = excluded.material,
                material_other = excluded.material_other,
                colour = excluded.colour,
                vendor = excluded.vendor,
                label = excluded.label,
                core_weight_mg = excluded.core_weight_mg,
                location_kind = excluded.location_kind,
                location_slot = excluded.location_slot,
                tag_uid = excluded.tag_uid,
                tag_source = excluded.tag_source,
                discarded_at = excluded.discarded_at,
                updated_at = datetime('now')
            """,
            (
                spool.id,
                spool.material.kind.value,
                spool.material.other_name,
                spool.colour.hex8,
                spool.vendor,
                spool.label,
                spool.opening_weight.milligrams,
                spool.core_weight.milligrams,
                kind,
                slot,
                spool.tag_uid.value if spool.tag_uid else None,
                spool.tag_source.value if spool.tag_source else None,
                _iso(spool.registered_at),
                _iso(spool.discarded_at) if spool.discarded_at else None,
            ),
        )
