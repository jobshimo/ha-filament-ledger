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
