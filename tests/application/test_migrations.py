"""Migrations are atomic, or they are a trap.

A migration that half-applies and commits leaves tables behind with no schema_version row
to say so — and every subsequent start then fails on "table already exists", forever.
These tests pin both halves of the defence: every migration file is one self-contained
transaction that records its own version, and a failure leaves the schema exactly as it
found it, retryable.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from custom_components.filament_ledger.infrastructure.persistence import (
    database as database_module,
)
from custom_components.filament_ledger.infrastructure.persistence.database import (
    MIGRATIONS,
    Database,
    run_inline,
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
            assert await database.migrate() == 3

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
