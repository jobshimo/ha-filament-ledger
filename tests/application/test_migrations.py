"""Migrations are atomic, or they are a trap.

A migration that half-applies and commits leaves tables behind with no schema_version row
to say so — and every subsequent start then fails on "table already exists", forever.
These tests pin both halves of the defence: every migration file is one self-contained
transaction that records its own version, and a failure leaves the schema exactly as it
found it, retryable.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from custom_components.filament_ledger.domain.model.pending_review import PendingReview
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import ReviewId, SlotIndex, SpoolId
from custom_components.filament_ledger.infrastructure.persistence import (
    database as database_module,
)
from custom_components.filament_ledger.infrastructure.persistence.database import (
    MIGRATIONS,
    Database,
    run_inline,
)
from custom_components.filament_ledger.infrastructure.persistence.review_repository import (
    SqliteReviewRepository,
)

BROKEN_MIGRATION = """
BEGIN;
CREATE TABLE demo (x INTEGER);
INSERT INTO absent_table VALUES (1);
COMMIT;
"""

GOOD_MIGRATION = """
BEGIN;
CREATE TABLE demo (x INTEGER);
INSERT INTO schema_version (version, applied_at) VALUES (1, datetime('now'));
COMMIT;
"""


def _statements(path: Path) -> list[str]:
    """The file's SQL split into statements, comments stripped.

    Coarse on purpose — it splits on every semicolon, including the ones inside trigger
    bodies — but the first and last statement are all the transactional claim needs."""
    without_comments = "\n".join(
        line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
    )
    return [chunk.strip() for chunk in without_comments.split(";") if chunk.strip()]


class TestEveryMigrationIsSelfContained:
    """The static half: the shape `Database._migrate` relies on, checked at the source.

    Under ``autocommit=True`` nothing wraps a migration from the outside, so a file that
    forgets its own BEGIN/COMMIT — or its own schema_version row — reintroduces the
    half-applied-forever failure without any code change to warn about it."""

    @pytest.mark.parametrize("path", sorted(MIGRATIONS.glob("*.sql")), ids=lambda p: p.name)
    def test_it_wraps_itself_in_one_transaction(self, path: Path) -> None:
        statements = _statements(path)
        assert statements[0].upper() == "BEGIN", f"{path.name} does not open a transaction"
        assert statements[-1].upper() == "COMMIT", f"{path.name} does not commit itself"

    @pytest.mark.parametrize("path", sorted(MIGRATIONS.glob("*.sql")), ids=lambda p: p.name)
    def test_it_records_its_own_version_inside_the_transaction(self, path: Path) -> None:
        version = int(path.name.split("_", 1)[0])
        match = re.search(
            r"INSERT INTO schema_version\s*\(version, applied_at\)\s*VALUES\s*\((\d+)",
            path.read_text(encoding="utf-8"),
        )
        assert match is not None, f"{path.name} never records itself in schema_version"
        assert int(match.group(1)) == version, (
            f"{path.name} records version {match.group(1)}, not the {version} its name claims"
        )


class TestAFailedMigrationLeavesNoTrace:
    async def test_failure_mid_script_commits_nothing_and_can_be_retried(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The crash-mid-first-migration scenario, in miniature.

        The broken script creates a table and then fails. Nothing may survive — not the
        table, not a schema_version row — and the connection must come out clean enough
        for the corrected file to apply on the next attempt."""
        migrations = tmp_path / "migrations"
        migrations.mkdir()
        monkeypatch.setattr(database_module, "MIGRATIONS", migrations)
        (migrations / "0001_demo.sql").write_text(BROKEN_MIGRATION, encoding="utf-8")

        database = await Database.open(tmp_path / "ledger.db", run_inline)
        try:
            with pytest.raises(sqlite3.OperationalError, match="no such table"):
                await database.migrate()

            tables = await database.fetch_all(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'demo'"
            )
            assert tables == [], "the failed migration committed a table"
            assert await database.fetch_all("SELECT version FROM schema_version") == []

            (migrations / "0001_demo.sql").write_text(GOOD_MIGRATION, encoding="utf-8")
            assert await database.migrate() == 1
            versions = await database.fetch_all("SELECT version FROM schema_version")
            assert [row["version"] for row in versions] == [1]
        finally:
            await database.close()


LEGACY_SPOOL = (
    "INSERT INTO spool (id, material, colour, opening_weight_mg, core_weight_mg, "
    "location_kind, tag_uid, registered_at, updated_at) "
    "VALUES (?, 'PLA', '000000FF', 1000000, 250000, 'STORAGE', ?, "
    "datetime('now'), datetime('now'))"
)


class TestMigration0002ScrubsTheAbsentTagSentinel:
    async def test_the_sentinel_becomes_null_and_real_tags_survive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exact upgrade every pre-0002 install performs: a database stopped at
        version 1, holding the sentinel an earlier version legally saved, brought to
        current. The row must come out untagged — `TagUid` refuses sixteen zeros now, so
        a row still carrying them would fail hydration on every start — and a real tag
        must come through untouched."""
        staged = tmp_path / "migrations"
        staged.mkdir()
        monkeypatch.setattr(database_module, "MIGRATIONS", staged)
        initial = MIGRATIONS / "0001_initial.sql"
        (staged / initial.name).write_text(initial.read_text(encoding="utf-8"), encoding="utf-8")

        database = await Database.open(tmp_path / "ledger.db", run_inline)
        try:
            assert await database.migrate() == 1
            await database.execute(LEGACY_SPOOL, ("untagged", "0000000000000000"))
            await database.execute(LEGACY_SPOOL, ("tagged", "3C45C3DB00000100"))

            scrub = MIGRATIONS / "0002_scrub_absent_tag_sentinel.sql"
            (staged / scrub.name).write_text(scrub.read_text(encoding="utf-8"), encoding="utf-8")
            assert await database.migrate() == 2

            rows = await database.fetch_all("SELECT id, tag_uid FROM spool ORDER BY id")
            assert [(row["id"], row["tag_uid"]) for row in rows] == [
                ("tagged", "3C45C3DB00000100"),
                ("untagged", None),
            ]
        finally:
            await database.close()


LEGACY_MOVEMENT = (
    "INSERT INTO movement (id, spool_id, type, amount_mg, source, occurred_at, recorded_at) "
    "VALUES (?, ?, 'OPENING_BALANCE', 1000000, 'USER_CONFIRMED', "
    "datetime('now'), datetime('now'))"
)


async def _staged_at_version_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Database, Path]:
    """A populated database stopped at version 2 — the exact state every live install is
    in before this release. Returns it alongside the staging directory, so the test can
    drop 0003 in and run the real runner over it."""
    staged = tmp_path / "migrations"
    staged.mkdir()
    monkeypatch.setattr(database_module, "MIGRATIONS", staged)
    for name in ("0001_initial.sql", "0002_scrub_absent_tag_sentinel.sql"):
        source = MIGRATIONS / name
        (staged / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    database = await Database.open(tmp_path / "ledger.db", run_inline)
    assert await database.migrate() == 2
    return database, staged


def _stage_0003(staged: Path) -> None:
    source = next(MIGRATIONS.glob("0003_*.sql"))
    (staged / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


class TestMigration0003AddsCorrectionsProvenanceAndTheTrash:
    """docs/14 §14.7 — one release, one migration, additive throughout.

    The movement columns are nullable and written only at INSERT, so `ALTER TABLE ADD
    COLUMN` never rewrites a row and the immutability triggers are never confronted. That
    claim is the one worth testing, because it is the claim the whole release stands on.
    """

    async def test_it_applies_cleanly_to_an_empty_database(self, tmp_path: Path) -> None:
        database = await Database.open(tmp_path / "ledger.db", run_inline)
        try:
            # The runner reports the newest version it applied, which later migrations
            # move; what this test is about is the objects 0003 leaves behind.
            assert await database.migrate() >= 3

            objects = await database.fetch_all(
                "SELECT name FROM sqlite_master WHERE name IN "
                "('movement_void', 'idx_void_open', 'idx_spool_slot', 'idx_spool_external')"
            )
            assert sorted(row["name"] for row in objects) == [
                "idx_spool_external",
                "idx_spool_slot",
                "idx_void_open",
                "movement_void",
            ]

            spool_columns = {
                row["name"] for row in await database.fetch_all("PRAGMA table_info(spool)")
            }
            assert {"tag_source", "deleted_at"} <= spool_columns

            movement_columns = {
                row["name"] for row in await database.fetch_all("PRAGMA table_info(movement)")
            }
            assert {"reassigns_movement_id", "reinstates_movement_id"} <= movement_columns
        finally:
            await database.close()

    async def test_it_backfills_every_existing_tag_as_manual(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Criterion 8. Provenance was never recorded before this migration, and claiming
        DETECTED for a tag whose origin nobody knows would be invented history. MANUAL is
        the honest floor: it over-grants edit rights once rather than storing a lie."""
        database, staged = await _staged_at_version_two(tmp_path, monkeypatch)
        try:
            await database.execute(LEGACY_SPOOL, ("tagged", "3C45C3DB00000100"))
            await database.execute(LEGACY_SPOOL, ("also-tagged", "4289A97100000100"))
            await database.execute(LEGACY_SPOOL, ("untagged", None))

            _stage_0003(staged)
            assert await database.migrate() == 3

            rows = await database.fetch_all("SELECT id, tag_source FROM spool ORDER BY id")
            assert [(row["id"], row["tag_source"]) for row in rows] == [
                ("also-tagged", "MANUAL"),
                ("tagged", "MANUAL"),
                # No tag, so nothing to describe: the pair stays null together.
                ("untagged", None),
            ]
        finally:
            await database.close()

    async def test_it_leaves_existing_movements_byte_identical(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The additive claim, checked rather than asserted: the new columns arrive NULL on
        every existing row and nothing else about them moves."""
        database, staged = await _staged_at_version_two(tmp_path, monkeypatch)
        try:
            await database.execute(LEGACY_SPOOL, ("spool", None))
            await database.execute(LEGACY_MOVEMENT, ("movement", "spool"))
            columns = "id, spool_id, type, amount_mg, source, occurred_at, recorded_at, note"
            before = await database.fetch_all(f"SELECT {columns} FROM movement")

            _stage_0003(staged)
            assert await database.migrate() == 3

            after = await database.fetch_all(f"SELECT {columns} FROM movement")
            assert [tuple(row) for row in after] == [tuple(row) for row in before]

            links = await database.fetch_all(
                "SELECT reassigns_movement_id, reinstates_movement_id FROM movement"
            )
            assert [tuple(row) for row in links] == [(None, None)]
        finally:
            await database.close()

    async def test_the_immutability_triggers_survive_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """0003 does not touch the triggers, and the point of never confronting them is
        that they still refuse everything afterwards (docs/adr/0007)."""
        database, staged = await _staged_at_version_two(tmp_path, monkeypatch)
        try:
            await database.execute(LEGACY_SPOOL, ("spool", None))
            await database.execute(LEGACY_MOVEMENT, ("movement", "spool"))
            _stage_0003(staged)
            assert await database.migrate() == 3

            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                await database.execute(
                    "UPDATE movement SET note = 'edited' WHERE id = ?", ("movement",)
                )
            with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
                await database.execute("DELETE FROM movement WHERE id = ?", ("movement",))
        finally:
            await database.close()

    async def test_the_new_link_columns_are_writable_at_insert(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The claim the whole release stands on, stated the other way round.

        The immutability triggers fire `BEFORE UPDATE` and `BEFORE DELETE` — an INSERT is
        free, whatever columns it fills. So a correction can name the entry it corrects on
        the row itself without the triggers ever being confronted, let alone modified
        (docs/14 §14.7). Checked at the SQL layer, because that is where a future
        `UPDATE`-based shortcut would be tempting.
        """
        database, staged = await _staged_at_version_two(tmp_path, monkeypatch)
        try:
            await database.execute(LEGACY_SPOOL, ("spool", None))
            await database.execute(LEGACY_MOVEMENT, ("original", "spool"))
            _stage_0003(staged)
            assert await database.migrate() == 3

            await database.execute(
                "INSERT INTO movement (id, spool_id, type, amount_mg, source, occurred_at, "
                "recorded_at, reassigns_movement_id, reinstates_movement_id) "
                "VALUES ('correction', 'spool', 'REASSIGNMENT', -1000, 'USER_CONFIRMED', "
                "datetime('now'), datetime('now'), 'original', 'original')"
            )

            row = await database.fetch_one(
                "SELECT reassigns_movement_id, reinstates_movement_id FROM movement "
                "WHERE id = 'correction'"
            )
            assert row is not None
            assert tuple(row) == ("original", "original")
            # And the entry it points at is untouched, which is the point of doing it
            # this way rather than with a flag.
            untouched = await database.fetch_one(
                "SELECT reassigns_movement_id FROM movement WHERE id = 'original'"
            )
            assert untouched is not None
            assert untouched["reassigns_movement_id"] is None
        finally:
            await database.close()

    async def test_the_void_table_accepts_a_chapter_and_enforces_its_checks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`movement_void` is the one table written after insert, so its two CHECK clauses
        are the last line between a status record and a second source of truth."""
        database, staged = await _staged_at_version_two(tmp_path, monkeypatch)
        try:
            await database.execute(LEGACY_SPOOL, ("spool", None))
            await database.execute(LEGACY_MOVEMENT, ("original", "spool"))
            _stage_0003(staged)
            assert await database.migrate() == 3

            await database.execute(
                "INSERT INTO movement_void (movement_id, voided_at, reason) "
                "VALUES ('original', datetime('now'), 'nothing came back')"
            )
            # A chapter is closed by both facts together or neither.
            with pytest.raises(sqlite3.IntegrityError):
                await database.execute(
                    "UPDATE movement_void SET reinstated_at = datetime('now') "
                    "WHERE movement_id = 'original'"
                )
            # And a void that returned nothing can never be reinstated at all.
            with pytest.raises(sqlite3.IntegrityError):
                await database.execute(
                    "UPDATE movement_void SET reinstated_at = datetime('now'), "
                    "reinstatement_movement_id = 'original' WHERE movement_id = 'original'"
                )
        finally:
            await database.close()

    async def test_running_the_runner_again_changes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Idempotence of the runner, not of the SQL: `DROP INDEX` would fail on a second
        pass, which is exactly why the version row is what guards it."""
        database, staged = await _staged_at_version_two(tmp_path, monkeypatch)
        try:
            await database.execute(LEGACY_SPOOL, ("tagged", "3C45C3DB00000100"))
            _stage_0003(staged)
            assert await database.migrate() == 3
            assert await database.migrate() == 3
            assert await database.migrate() == 3

            versions = await database.fetch_all(
                "SELECT version FROM schema_version ORDER BY version"
            )
            assert [row["version"] for row in versions] == [1, 2, 3]
            row = await database.fetch_one("SELECT tag_source FROM spool WHERE id = 'tagged'")
            assert row is not None
            assert row["tag_source"] == "MANUAL"
        finally:
            await database.close()

    async def test_the_slot_invariant_now_ignores_deleted_spools(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The recreated partial indexes, checked through behaviour: one spool per slot
        still holds, and a deleted spool no longer occupies one (§14.4.3)."""
        database, staged = await _staged_at_version_two(tmp_path, monkeypatch)
        try:
            _stage_0003(staged)
            assert await database.migrate() == 3

            mounted = (
                "INSERT INTO spool (id, material, colour, opening_weight_mg, core_weight_mg, "
                "location_kind, location_slot, registered_at, updated_at, deleted_at) "
                "VALUES (?, 'PLA', '000000FF', 1000000, 250000, 'AMS_SLOT', 1, "
                "datetime('now'), datetime('now'), ?)"
            )
            await database.execute(mounted, ("gone", "2026-08-03T00:00:00+00:00"))
            # The slot the deleted spool used to hold is free for another spool.
            await database.execute(mounted, ("live", None))

            with pytest.raises(sqlite3.IntegrityError):
                await database.execute(mounted, ("clash", None))
        finally:
            await database.close()


LEGACY_JOB = (
    "INSERT INTO print_job (id, name, state, started_at, consumption_recorded) "
    "VALUES (?, 'bracket_v3.gcode.3mf', 'CANCELLED', '2026-08-02T12:00:00+00:00', 0)"
)

LEGACY_REVIEW = (
    "INSERT INTO pending_review (id, job_id, reason, estimated_usage, confirmed_usage, "
    "slot_resolution, estimator_used, state, opened_at, resolved_at, resolution_note) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?)"
)

#: Every shape a pre-0004 `slot_resolution` could hold, written the way the old repository
#: wrote it — `{slot: spool_id | null}` beside a `{slot: mg}` estimate. Nothing here is
#: produced by the current code: these are literal columns, so the fixture cannot drift
#: into agreeing with the mapper it exists to check.
OLD_SHAPE_REVIEWS: tuple[tuple[object, ...], ...] = (
    # A pending review whose tray had a spool mounted: the ordinary case.
    (
        "pending-resolved",
        "job-1",
        "CANCELLED",
        '{"1": 28400}',
        None,
        '{"1": "spool-a"}',
        "LINEAR_PROGRESS",
        "PENDING",
        "2026-08-02T12:00:00+00:00",
        None,
        None,
    ),
    # A pending review whose tray had none: the case the queue exists for.
    (
        "pending-unresolved",
        "job-2",
        "UNMAPPED_USAGE",
        '{"3": 12100}',
        None,
        '{"3": null}',
        "NONE",
        "PENDING",
        "2026-08-02T13:00:00+00:00",
        None,
        None,
    ),
    # Three trays at once, including a zero estimate on a tray that did have a spool —
    # the no-data card's row, where the spool is a fact and the amount is not.
    (
        "pending-mixed",
        "job-3",
        "FAILED",
        '{"1": 28400, "2": 0, "3": 12100}',
        None,
        '{"1": "spool-a", "2": "spool-b", "3": null}',
        "LINEAR_PROGRESS",
        "PENDING",
        "2026-08-02T14:00:00+00:00",
        None,
        None,
    ),
    # Approved with the user's number overriding the estimate: 71 g proposed, 31 g
    # confirmed. The charge that landed is 31 g, and the migrated row must say so.
    (
        "approved-corrected",
        "job-4",
        "CANCELLED",
        '{"1": 71000}',
        '{"1": 31000}',
        '{"1": "spool-a"}',
        "LINEAR_PROGRESS",
        "APPROVED",
        "2026-08-02T15:00:00+00:00",
        "2026-08-03T09:00:00+00:00",
        "weighed the waste",
    ),
    # Approved after an assignment filled a tray the review froze without a spool, and
    # with a second tray zeroed rather than attributed — the user's third option.
    (
        "approved-assigned",
        "job-5",
        "UNMAPPED_USAGE",
        '{"2": 20000, "3": 12100}',
        '{"2": 0, "3": 12100}',
        '{"2": null, "3": "spool-c"}',
        "NONE",
        "APPROVED",
        "2026-08-02T16:00:00+00:00",
        "2026-08-03T10:00:00+00:00",
        None,
    ),
    # Dismissed: a decision, and nothing confirmed. The frozen attribution survives.
    (
        "dismissed",
        "job-6",
        "FAILED",
        '{"1": 71000}',
        None,
        '{"1": "spool-a"}',
        "LINEAR_PROGRESS",
        "DISMISSED",
        "2026-08-02T17:00:00+00:00",
        "2026-08-03T11:00:00+00:00",
        "failed on the first layer",
    ),
    # Estimation unavailable and not even the slots known: the review that documents a
    # loss whose size nobody can name.
    (
        "empty",
        "job-7",
        "UNCLASSIFIED",
        "{}",
        None,
        "{}",
        "NONE",
        "PENDING",
        "2026-08-02T18:00:00+00:00",
        None,
        None,
    ),
)

Semantics = tuple[
    dict[int, int],
    dict[int, int] | None,
    dict[int, str | None],
    list[tuple[int, int, SpoolId]],
]


def _old_semantics(row: sqlite3.Row) -> Semantics:
    """What the pre-0004 mapper read out of these columns.

    A deliberate second implementation, written from the shape rather than imported: the
    code that used to do this is gone, and a comparison against the mapper under test
    would prove only that it agrees with itself. Four facts, because four is everything a
    review's amounts and attribution could answer — the estimate per tray, the confirmed
    amount per tray, the one spool each tray resolved to, and the charges an approval
    turned into movements.
    """
    estimated = {int(slot): int(mg) for slot, mg in json.loads(row["estimated_usage"]).items()}
    confirmed = (
        {int(slot): int(mg) for slot, mg in json.loads(row["confirmed_usage"]).items()}
        if row["confirmed_usage"] is not None
        else None
    )
    resolution = {int(slot): spool for slot, spool in json.loads(row["slot_resolution"]).items()}
    charges = (
        [
            (slot, mg, resolution[slot])
            for slot, mg in sorted(confirmed.items())
            if mg != 0 and resolution.get(slot) is not None
        ]
        if confirmed is not None
        else []
    )
    return estimated, confirmed, resolution, charges


def _new_semantics(review: PendingReview) -> Semantics:
    """The same four facts, read off the migrated entity.

    The third one is the interesting derivation: *the one spool this tray resolves to* is
    still answerable for every migrated review, because 0004 gives each old entry exactly
    one charge. That it stays answerable is the losslessness claim; that it need not be
    answerable for reviews written afterwards is the whole point of the change.
    """
    estimated = {slot.value: amount.milligrams for slot, amount in review.estimated_usage.items()}
    confirmed = (
        {slot.value: amount.milligrams for slot, amount in review.confirmed_usage.items()}
        if review.confirmed_usage is not None
        else None
    )
    resolution: dict[int, str | None] = {}
    for line in review.lines:
        assert len(line.charges) <= 1, f"0004 invented a split on slot {line.slot}"
        resolution[line.slot.value] = line.charges[0].spool_id if line.charges else None
    charges = [
        (slot.value, amount.milligrams, spool_id)
        for slot, amount, spool_id in review.confirmed_charges
    ]
    return estimated, confirmed, resolution, charges


async def _staged_at_version_three_for_0004(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Database, Path]:
    """A database stopped at version 3 — where every 1.2.0 install sits.

    0001 to 0003 land in one run, so this is a fresh 1.2.0 install; 0005's fixture stages
    them a release at a time, and the two are kept apart because the paths differ.
    """
    staged = tmp_path / "migrations"
    staged.mkdir()
    monkeypatch.setattr(database_module, "MIGRATIONS", staged)
    for name in (
        "0001_initial.sql",
        "0002_scrub_absent_tag_sentinel.sql",
        "0003_corrections_provenance_and_trash.sql",
    ):
        source = MIGRATIONS / name
        (staged / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    database = await Database.open(tmp_path / "ledger.db", run_inline)
    assert await database.migrate() == 3
    return database, staged


def _stage_0004(staged: Path) -> None:
    source = next(MIGRATIONS.glob("0004_*.sql"))
    (staged / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


async def _populate_in_the_old_shape(database: Database) -> None:
    for review in OLD_SHAPE_REVIEWS:
        await database.execute(LEGACY_JOB, (review[1],))
        await database.execute(LEGACY_REVIEW, review)


class TestMigration0004TurnsTheResolutionIntoAListOfCharges:
    """docs/08 §8.2 — `slot_resolution` stops being one spool per tray and becomes a list
    of `{slot, spool_id, mg}`, so a tray whose spool emptied mid-print can name both.

    The claim the release stands on is that the rewrite loses nothing, and it is checked
    the only way that means anything: a database populated in the old shape, migrated by
    the real runner, read back through the real mapper, and compared fact for fact against
    what the old shape said.
    """

    async def test_every_review_comes_back_semantically_identical(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        database, staged = await _staged_at_version_three_for_0004(tmp_path, monkeypatch)
        try:
            await _populate_in_the_old_shape(database)
            rows = await database.fetch_all("SELECT * FROM pending_review ORDER BY id")
            before = {row["id"]: _old_semantics(row) for row in rows}
            assert len(before) == len(OLD_SHAPE_REVIEWS)

            _stage_0004(staged)
            assert await database.migrate() == 4

            repository = SqliteReviewRepository(database)
            after = {}
            for review_id in before:
                review = await repository.get(ReviewId(review_id))
                assert review is not None, f"{review_id} did not survive the migration"
                after[review_id] = _new_semantics(review)

            assert after == before
        finally:
            await database.close()

    async def test_a_resolved_slot_becomes_one_charge_carrying_what_it_was_charged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stated at the column, where the rewrite happens. The confirmed figure wins over
        the estimate for a review that was already approved: 71 g was proposed and 31 g
        deducted, and a charge carrying 71 g would misreport a movement already written."""
        database, staged = await _staged_at_version_three_for_0004(tmp_path, monkeypatch)
        try:
            await _populate_in_the_old_shape(database)
            _stage_0004(staged)
            assert await database.migrate() == 4

            stored = {
                row["id"]: json.loads(row["slot_resolution"])
                for row in await database.fetch_all(
                    "SELECT id, slot_resolution FROM pending_review"
                )
            }

            assert stored["pending-resolved"] == [{"slot": 1, "spool_id": "spool-a", "mg": 28400}]
            assert stored["approved-corrected"] == [{"slot": 1, "spool_id": "spool-a", "mg": 31000}]
            # A zero estimate keeps its charge: the spool is a fact, the amount is not.
            assert stored["pending-mixed"] == [
                {"slot": 1, "spool_id": "spool-a", "mg": 28400},
                {"slot": 2, "spool_id": "spool-b", "mg": 0},
            ]
            # A null resolution becomes no charge, which is already what it meant.
            assert stored["pending-unresolved"] == []
            assert stored["empty"] == []
        finally:
            await database.close()

    async def test_a_migrated_review_still_approves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The sum invariant is new, so a review frozen before it existed has to satisfy
        it. It does by construction — every migrated charge carries its own tray's whole
        amount — and this is that claim, not asserted but exercised through the entity."""
        database, staged = await _staged_at_version_three_for_0004(tmp_path, monkeypatch)
        try:
            await _populate_in_the_old_shape(database)
            _stage_0004(staged)
            assert await database.migrate() == 4

            repository = SqliteReviewRepository(database)
            review = await repository.get(ReviewId("pending-resolved"))
            assert review is not None

            approved = review.approved(at=review.opened_at)

            assert approved.confirmed_charges == [
                (SlotIndex(1), Grams.of("28.4"), SpoolId("spool-a"))
            ]
        finally:
            await database.close()

    async def test_running_the_runner_again_changes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The version row is what guards it: a second pass over the same rows would read
        the new array as an object and write nonsense, which is exactly why re-running a
        migration must never reach its SQL."""
        database, staged = await _staged_at_version_three_for_0004(tmp_path, monkeypatch)
        try:
            await _populate_in_the_old_shape(database)
            _stage_0004(staged)
            assert await database.migrate() == 4
            once = await database.fetch_all(
                "SELECT slot_resolution FROM pending_review ORDER BY id"
            )

            assert await database.migrate() == 4
            assert await database.migrate() == 4

            twice = await database.fetch_all(
                "SELECT slot_resolution FROM pending_review ORDER BY id"
            )
            assert [tuple(row) for row in twice] == [tuple(row) for row in once]
            versions = await database.fetch_all(
                "SELECT version FROM schema_version ORDER BY version"
            )
            assert [row["version"] for row in versions] == [1, 2, 3, 4]
        finally:
            await database.close()

    async def test_it_applies_cleanly_to_an_empty_database(self, tmp_path: Path) -> None:
        database = await Database.open(tmp_path / "ledger.db", run_inline)
        try:
            # The runner reports the newest version it applied, which later migrations
            # move; what this test is about is 0004 surviving a run over nothing.
            assert await database.migrate() >= 4
        finally:
            await database.close()


LEGACY_TYPED_MOVEMENT = (
    "INSERT INTO movement (id, spool_id, type, amount_mg, source, occurred_at, recorded_at) "
    "VALUES (?, ?, ?, ?, 'USER_CONFIRMED', datetime('now'), datetime('now'))"
)

LEGACY_VOID = (
    "INSERT INTO movement_void (movement_id, voided_at, reason, reversal_movement_id) "
    "VALUES (?, datetime('now'), ?, ?)"
)


async def _staged_at_version_three_for_0005(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Database, Path]:
    """A populated database stopped at version 3 — the shape of every install that has
    ever deleted a history entry, and the one 0005 has to arrive into.

    Each release is staged and run separately, so the trash arrives as its own upgrade
    rather than with the schema; 0004's fixture applies 0001 to 0003 in one run instead.
    """
    database, staged = await _staged_at_version_two(tmp_path, monkeypatch)
    _stage_0003(staged)
    assert await database.migrate() == 3
    return database, staged


def _stage_0005(staged: Path) -> None:
    source = next(MIGRATIONS.glob("0005_*.sql"))
    (staged / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


class TestMigration0005TeachesTheVoidRowAboutTheUnDiscard:
    """docs/14 §14.4.1-2 — the fact that stopped being derivable, given a column.

    Additive in the strictest sense available to `ALTER TABLE`: one flag on a status
    record, defaulted, and not a statement in the file touches `movement`.
    """

    async def test_it_applies_to_a_database_populated_in_the_old_shape(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The upgrade every install with a trash performs, with both kinds of chapter
        already in the table: one that returned the grams, one that returned nothing."""
        database, staged = await _staged_at_version_three_for_0005(tmp_path, monkeypatch)
        try:
            await database.execute(LEGACY_SPOOL, ("spool", None))
            await database.execute(LEGACY_TYPED_MOVEMENT, ("charge", "spool", "DISCARD", -84_100))
            await database.execute(
                LEGACY_TYPED_MOVEMENT, ("reversal", "spool", "VOID_REVERSAL", 84_100)
            )
            await database.execute(LEGACY_TYPED_MOVEMENT, ("orphan", "spool", "DISCARD", -40_000))
            await database.execute(LEGACY_VOID, ("charge", "wrong spool", "reversal"))
            await database.execute(LEGACY_VOID, ("orphan", "nothing came back", None))

            _stage_0005(staged)
            assert await database.migrate() == 5

            columns = {
                row["name"] for row in await database.fetch_all("PRAGMA table_info(movement_void)")
            }
            assert "undiscarded_spool" in columns
            movement_columns = {
                row["name"] for row in await database.fetch_all("PRAGMA table_info(movement)")
            }
            assert "undiscarded_spool" not in movement_columns
        finally:
            await database.close()

    async def test_it_backfills_every_existing_chapter_as_no_un_discard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The deliberate half, and the case that argues it: `undiscarded` below *is* a
        chapter that un-discarded its spool — a whole-spool `DISCARD`, voided, the spool
        back in inventory with `discarded_at` cleared — and it still backfills to no.

        Nothing recorded the fact, and it cannot be recovered: a partial `DISCARD` voided
        on a spool that was never retired leaves those same traces, which is exactly what
        `partial` is here to show. The two mistakes are not symmetric. A wrong `0` makes an
        open chapter restore the way every restore behaved before this migration — the
        behaviour that install already has, and at most a handful of chapters are open at
        an upgrade. A wrong `1` would throw a spool away that nobody threw away, inventing
        a real-world event that counts as waste. Under-claim.
        """
        database, staged = await _staged_at_version_three_for_0005(tmp_path, monkeypatch)
        try:
            await database.execute(LEGACY_SPOOL, ("spool", None))
            for movement, kind, amount in (
                ("undiscarded", "DISCARD", -1_000_000),
                ("undiscarded-reversal", "VOID_REVERSAL", 1_000_000),
                ("partial", "DISCARD", -40_000),
                ("partial-reversal", "VOID_REVERSAL", 40_000),
            ):
                await database.execute(LEGACY_TYPED_MOVEMENT, (movement, "spool", kind, amount))
            await database.execute(LEGACY_VOID, ("undiscarded", None, "undiscarded-reversal"))
            await database.execute(LEGACY_VOID, ("partial", None, "partial-reversal"))

            _stage_0005(staged)
            assert await database.migrate() == 5

            rows = await database.fetch_all(
                "SELECT movement_id, undiscarded_spool FROM movement_void ORDER BY movement_id"
            )
            assert [(row["movement_id"], row["undiscarded_spool"]) for row in rows] == [
                ("partial", 0),
                ("undiscarded", 0),
            ]
        finally:
            await database.close()

    async def test_it_leaves_the_rest_of_every_chapter_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The additive claim, checked rather than asserted."""
        database, staged = await _staged_at_version_three_for_0005(tmp_path, monkeypatch)
        try:
            await database.execute(LEGACY_SPOOL, ("spool", None))
            await database.execute(LEGACY_TYPED_MOVEMENT, ("charge", "spool", "DISCARD", -84_100))
            await database.execute(
                LEGACY_TYPED_MOVEMENT, ("reversal", "spool", "VOID_REVERSAL", 84_100)
            )
            await database.execute(LEGACY_VOID, ("charge", "wrong spool", "reversal"))
            columns = (
                "movement_id, voided_at, reason, reversal_movement_id, reinstated_at, "
                "reinstatement_movement_id"
            )
            before = await database.fetch_all(f"SELECT {columns} FROM movement_void")

            _stage_0005(staged)
            assert await database.migrate() == 5

            after = await database.fetch_all(f"SELECT {columns} FROM movement_void")
            assert [tuple(row) for row in after] == [tuple(row) for row in before]
        finally:
            await database.close()

    async def test_a_chapter_cannot_claim_an_un_discard_it_could_not_have_made(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The new invariant at the last layer. A void without restitution returned no
        balance, so nothing was stranded outside inventory and there was nothing to bring a
        spool back for — the row would describe something that never happened.

        SQLite validates a new column's CHECK against the rows already in the table, so
        this clause is also what proves the backfill above consistent: the migration would
        have refused to apply if any existing row had contradicted it.
        """
        database, staged = await _staged_at_version_three_for_0005(tmp_path, monkeypatch)
        try:
            await database.execute(LEGACY_SPOOL, ("spool", None))
            await database.execute(LEGACY_TYPED_MOVEMENT, ("orphan", "spool", "DISCARD", -40_000))
            _stage_0005(staged)
            assert await database.migrate() == 5

            with pytest.raises(sqlite3.IntegrityError):
                await database.execute(
                    "INSERT INTO movement_void (movement_id, voided_at, reason, "
                    "reversal_movement_id, undiscarded_spool) "
                    "VALUES ('orphan', datetime('now'), 'nothing came back', NULL, 1)"
                )
        finally:
            await database.close()

    async def test_running_the_runner_again_changes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        database, staged = await _staged_at_version_three_for_0005(tmp_path, monkeypatch)
        try:
            await database.execute(LEGACY_SPOOL, ("spool", None))
            await database.execute(LEGACY_TYPED_MOVEMENT, ("charge", "spool", "DISCARD", -84_100))
            await database.execute(LEGACY_VOID, ("charge", "nothing came back", None))

            _stage_0005(staged)
            assert await database.migrate() == 5
            assert await database.migrate() == 5
            assert await database.migrate() == 5

            versions = await database.fetch_all(
                "SELECT version FROM schema_version ORDER BY version"
            )
            assert [row["version"] for row in versions] == [1, 2, 3, 5]
            row = await database.fetch_one(
                "SELECT undiscarded_spool FROM movement_void WHERE movement_id = 'charge'"
            )
            assert row is not None
            assert row["undiscarded_spool"] == 0
        finally:
            await database.close()


def _stage_0006(staged: Path) -> None:
    source = next(MIGRATIONS.glob("0006_*.sql"))
    (staged / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


class TestMigration0006GivesThePrintersClockItsOwnColumns:
    """docs/05 §5.8 — the machine's own start and end, beside the ledger's rather than over.

    Additive in the strictest sense: two nullable columns on `print_job`, no backfill, and
    not one statement touches `movement`. A job recorded before this migration ran has
    nothing to say about the printer's clock, and NULL is exactly that.
    """

    async def test_it_applies_to_a_database_populated_in_the_old_shape(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        database, staged = await _staged_at_version_three_for_0005(tmp_path, monkeypatch)
        try:
            _stage_0005(staged)
            assert await database.migrate() == 5
            await database.execute(LEGACY_JOB, ("job-before-the-upgrade",))

            _stage_0006(staged)
            assert await database.migrate() == 6

            columns = {
                row["name"] for row in await database.fetch_all("PRAGMA table_info(print_job)")
            }
            assert {"printer_started_at", "printer_ended_at"} <= columns
            movement_columns = {
                row["name"] for row in await database.fetch_all("PRAGMA table_info(movement)")
            }
            assert "printer_started_at" not in movement_columns
        finally:
            await database.close()

    async def test_an_existing_job_says_nothing_about_the_machines_clock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No backfill, and the absence of one is the decision. The ledger's own pair is
        *not* copied across: two columns holding Home Assistant's timestamps under the
        printer's name would claim a measurement nobody made, and the duration falls back
        to that pair anyway — which is what those prints have always been measured by."""
        database, staged = await _staged_at_version_three_for_0005(tmp_path, monkeypatch)
        try:
            _stage_0005(staged)
            assert await database.migrate() == 5
            await database.execute(LEGACY_JOB, ("job-before-the-upgrade",))

            _stage_0006(staged)
            assert await database.migrate() == 6

            row = await database.fetch_one(
                "SELECT started_at, printer_started_at, printer_ended_at FROM print_job"
            )
            assert row is not None
            assert row["started_at"] == "2026-08-02T12:00:00+00:00"
            assert row["printer_started_at"] is None
            assert row["printer_ended_at"] is None
        finally:
            await database.close()
