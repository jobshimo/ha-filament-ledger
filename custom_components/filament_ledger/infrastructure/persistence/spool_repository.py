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
    MIN_AMS_INDEX,
    UNIDENTIFIED_PRINTER,
    AmsIndex,
    PrinterSerial,
    ReelUid,
    SlotIndex,
    SpoolId,
    TagSource,
    TagUid,
    TrayRef,
)
from ...domain.value.location import AmsSlot, ExternalSpool, Location, Storage
from ...domain.value.material import Material, MaterialKind
from .database import Database

COLUMNS = (
    "id, material, material_other, colour, vendor, label, opening_weight_mg, "
    "core_weight_mg, location_kind, location_printer, location_ams, location_slot, "
    "tag_uid, tag_source, reel_uid, registered_at, discarded_at, deleted_at, deleted_reason"
)

# Both retirements, in one predicate. Every read that means "in inventory" uses this
# string rather than spelling the clause out again: docs/14 §14.4.5's visibility table
# gives discarded and deleted spools the same answer in every row except the global
# history, and one place to state that is one place to get it wrong.
IN_INVENTORY = "discarded_at IS NULL AND deleted_at IS NULL"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _location_columns(location: Location) -> tuple[str, str | None, int | None, int | None]:
    match location:
        case AmsSlot(tray):
            return "AMS_SLOT", tray.printer.value, tray.ams.value, tray.slot.value
        case ExternalSpool(printer):
            # `location_printer` carries the machine for both mounted kinds since 0008, which
            # is what lets one partial unique index per kind state *one spool per position*
            # without either of them naming a printer column of its own.
            return "EXTERNAL_SPOOL", printer.value, None, None
        case Storage():
            return "STORAGE", None, None, None


def _location_from(kind: str, printer: str | None, ams: int | None, slot: int | None) -> Location:
    """Rebuild the location, tolerating a mounted row that names no printer.

    Migrations 0007 and 0008 backfill every mounted row with a printer — and a tray row with
    an AMS index besides — so a row missing either should not exist. Should one turn up
    anyway — a backup restored from between the ALTER and the UPDATE, a row inserted by
    hand — it hydrates exactly as the migration would have written it, for the reason
    `_tag_from` tolerates the sentinel and `_tag_source_from` defaults to MANUAL: one odd row
    must not fail every list and get, and with them the coordinator and the whole entry.
    """
    if kind == "AMS_SLOT" and slot is not None:
        return AmsSlot(
            TrayRef(
                printer=_printer_from(printer),
                ams=AmsIndex(ams if ams is not None else MIN_AMS_INDEX),
                slot=SlotIndex(slot),
            )
        )
    if kind == "EXTERNAL_SPOOL":
        return ExternalSpool(printer=_printer_from(printer))
    return Storage()


def _printer_from(value: str | None) -> PrinterSerial:
    return PrinterSerial(value) if value else UNIDENTIFIED_PRINTER


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


def _reel_from(value: str | None) -> ReelUid | None:
    """The reel id, hydrated — tolerating the sentinel exactly as `_tag_from` does.

    Thirty-two zeros is what the printer reports for a reel it could not identify. The
    gateway turns it into `None` on the way in and `ReelUid` refuses it, so a stored one
    should not exist; a row that has one anyway — hand-inserted, or restored from a backup
    written by a build that predates the refusal — hydrates as an unidentified reel rather
    than failing every list and get, and with them the coordinator and the whole entry.
    """
    if not value or set(value) == {"0"}:
        return None
    return ReelUid(value)


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
        location=_location_from(
            row["location_kind"],
            row["location_printer"],
            row["location_ams"],
            row["location_slot"],
        ),
        registered_at=registered,
        vendor=row["vendor"],
        label=row["label"],
        tag_uid=tag,
        tag_source=_tag_source_from(row["tag_source"], tag),
        reel_uid=_reel_from(row["reel_uid"]),
        discarded_at=_parse(row["discarded_at"]),
        deleted_at=_parse(row["deleted_at"]),
        deleted_reason=row["deleted_reason"],
    )


@dataclass(frozen=True, slots=True)
class SqliteSpoolRepository:
    database: Database

    async def get(self, spool_id: SpoolId) -> Spool | None:
        """Unfiltered on purpose — the Trash reaches a deleted spool's detail through here."""
        row = await self.database.fetch_one(
            f"SELECT {COLUMNS} FROM spool WHERE id = ?", (spool_id,)
        )
        return _to_spool(row) if row else None

    async def find_by_tag(self, tag: TagUid) -> list[Spool]:
        """Every **in-inventory** spool carrying this tag — plural, because duplicates are
        legal and the caller has to be told when the answer is ambiguous.

        Deleted spools drop out alongside discarded ones (docs/14 §14.4). A spool
        retracted as never-registered that went on matching its tag would demand a
        duplicate-confirmation about a spool the user cannot see anywhere, and would keep
        answering the printer's RFID reads with a reel that is out of the ledger.

        Ordered, because when the answer is ambiguous this list *is* the choice the AMS view
        offers (`DetectSpool` hands it straight to `AmbiguousTagDetected`). An unordered read
        would let the same two spools swap places between one detection and the next, which
        is a menu that moves under the hand that is reaching for it.
        """
        rows = await self.database.fetch_all(
            f"SELECT {COLUMNS} FROM spool "
            f" WHERE ( tag_uid = ? "
            f"      OR EXISTS (SELECT 1 FROM spool_tag "
            f"                  WHERE spool_tag.spool_id = spool.id "
            f"                    AND spool_tag.tag_uid = ?) ) "
            f"   AND {IN_INVENTORY} "
            f" ORDER BY registered_at, rowid",
            (tag.value, tag.value),
        )
        return [_to_spool(row) for row in rows]

    async def find_by_reel(self, reel: ReelUid) -> list[Spool]:
        """Every **in-inventory** spool that is this physical reel.

        The lookup automatic recognition leads with, and the one that ends the defect
        `spool_tag` exists to survive: a reel answers with a different chip UID in an odd
        tray than in an even one, but it reports one `tray_uuid` in every tray, so this
        question has a stable answer where `find_by_tag`'s did not.

        **Plural, and it should almost always return one.** Two rows naming one reel is not
        a state this release can create — `DetectSpool` resolves by reel before it considers
        registering — but it is a state a ledger *arrives* with: every pair minted under the
        old rule becomes visible here the moment both halves learn their reel. Returning a
        list rather than the first row is what lets the panel offer to merge them instead of
        the ledger silently picking a winner and charging prints to it.

        Ordered like `find_by_tag`, and for its reason: this list is a choice offered to a
        user, and a menu that reorders itself between one detection and the next is a menu
        that moves under the hand reaching for it.
        """
        rows = await self.database.fetch_all(
            f"SELECT {COLUMNS} FROM spool WHERE reel_uid = ? AND {IN_INVENTORY} "
            f"ORDER BY registered_at, rowid",
            (reel.value,),
        )
        return [_to_spool(row) for row in rows]

    async def find_by_location(self, location: Location) -> Spool | None:
        """Who is in this position — and a retired spool is in none.

        Deletion clears the location in the same transaction that sets `deleted_at`, so
        this predicate is belt and braces; it is here because the partial unique indexes
        stopped watching deleted rows in migration 0003, and a read that still saw one
        would report an occupant no constraint is defending.
        """
        kind, printer, ams, slot = _location_columns(location)
        if kind == "STORAGE":
            # Storage is not a unique position; "which spool is in storage" has no answer.
            return None
        # `IS` rather than `=` on all three tray columns: it is SQLite's null-safe
        # equality, so the one predicate answers both the tray question — where every
        # column is set — and the external-feed question, where all three are NULL.
        row = await self.database.fetch_one(
            f"SELECT {COLUMNS} FROM spool "
            f"WHERE location_kind = ? AND location_printer IS ? AND location_ams IS ? "
            f"AND location_slot IS ? AND {IN_INVENTORY}",
            (kind, printer, ams, slot),
        )
        return _to_spool(row) if row else None

    async def list(self, criteria: SpoolFilter) -> list[Spool]:
        clauses: list[str] = []
        params: list[object] = []
        if criteria.deleted_only:
            # The Trash's query. It inverts rather than widens, so the include flags have
            # nothing left to say and are deliberately not consulted.
            clauses.append("deleted_at IS NOT NULL")
        else:
            if not criteria.include_discarded:
                clauses.append("discarded_at IS NULL")
            if not criteria.include_deleted:
                clauses.append("deleted_at IS NULL")
        if criteria.mounted_only:
            clauses.append("location_kind != 'STORAGE'")
        if criteria.search:
            clauses.append("(COALESCE(label,'') LIKE ? OR COALESCE(vendor,'') LIKE ?)")
            needle = f"%{criteria.search}%"
            params.extend([needle, needle])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self.database.fetch_all(
            f"SELECT {COLUMNS} FROM spool{where} ORDER BY registered_at, rowid", params
        )
        return [_to_spool(row) for row in rows]

    async def save(self, spool: Spool) -> None:
        kind, printer, ams, slot = _location_columns(spool.location)
        await self.database.execute(
            """
            INSERT INTO spool (
                id, material, material_other, colour, vendor, label,
                opening_weight_mg, core_weight_mg, location_kind, location_printer,
                location_ams, location_slot,
                tag_uid, tag_source, reel_uid,
                registered_at, discarded_at, deleted_at, deleted_reason, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                material = excluded.material,
                material_other = excluded.material_other,
                colour = excluded.colour,
                vendor = excluded.vendor,
                label = excluded.label,
                core_weight_mg = excluded.core_weight_mg,
                location_kind = excluded.location_kind,
                location_printer = excluded.location_printer,
                location_ams = excluded.location_ams,
                location_slot = excluded.location_slot,
                tag_uid = excluded.tag_uid,
                tag_source = excluded.tag_source,
                reel_uid = excluded.reel_uid,
                discarded_at = excluded.discarded_at,
                deleted_at = excluded.deleted_at,
                deleted_reason = excluded.deleted_reason,
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
                printer,
                ams,
                slot,
                spool.tag_uid.value if spool.tag_uid else None,
                spool.tag_source.value if spool.tag_source else None,
                spool.reel_uid.value if spool.reel_uid else None,
                _iso(spool.registered_at),
                _iso(spool.discarded_at) if spool.discarded_at else None,
                _iso(spool.deleted_at) if spool.deleted_at else None,
                spool.deleted_reason,
            ),
        )
        # The chip index follows the entity, in the same unit of work, so the two can never
        # disagree about whether a spool owns a chip. Done here rather than left to callers
        # because `find_by_tag` reads the index: a caller that forgot would produce a spool
        # with a tag the printer can no longer resolve, which is a defect with no symptom
        # until the reel is next put in a tray.
        #
        # OR IGNORE, not upsert: re-saving a spool re-asserts a chip it already owns on
        # every mount, unmount and adjustment, and `first_seen_at` means *first*.
        if spool.tag_uid is not None:
            await self.database.execute(
                """
                INSERT OR IGNORE INTO spool_tag (spool_id, tag_uid, first_seen_at)
                VALUES (?, ?, datetime('now'))
                """,
                (spool.id, spool.tag_uid.value),
            )

    async def claim_tag(self, spool_id: SpoolId, tag: TagUid) -> None:
        """Record that this reel also answers to this chip UID.

        The second side of a reel, learned the first time the AMS reads it from a tray of
        the opposite parity. Separate from `save` because it is not a property of the
        entity: `Spool.tag_uid` remains the chip the spool was *registered* with, which is
        what the panel shows and what `tag_source` qualifies, while this index is every
        chip the reel is known to answer to.

        Idempotent, for the reason `save`'s insert is: the detection path re-observes the
        same chip on every republish.
        """
        await self.database.execute(
            """
            INSERT OR IGNORE INTO spool_tag (spool_id, tag_uid, first_seen_at)
            VALUES (?, ?, datetime('now'))
            """,
            (spool_id, tag.value),
        )
