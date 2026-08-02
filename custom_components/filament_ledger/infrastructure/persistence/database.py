"""SQLite access.

Two rules from docs/03-architecture.md §3.7:

1. **Every query runs in an executor.** Blocking the event loop stalls the whole of Home
   Assistant — an unacceptable neighbour effect for an integration.
2. **Access is serialised** by a single lock. SQLite tolerates concurrent readers, but the
   ledger's invariants are easier to reason about when appends and the reads that follow
   them see one consistent sequence.

The executor is a **port**, injected rather than taken from `hass`, so the whole persistence
layer is testable with a trivial inline runner and no Home Assistant at all.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Protocol, Self

MIGRATIONS = Path(__file__).parent / "migrations"


class Executor(Protocol):
    """Runs a blocking callable off the event loop.

    `hass.async_add_executor_job` satisfies this structurally. So does `run_inline` below,
    which is what lets the repositories be tested without an event loop policy or a thread
    pool.
    """

    def __call__[T](self, target: Callable[[], T]) -> Awaitable[T]: ...


async def run_inline[T](target: Callable[[], T]) -> T:
    """An executor for tests: runs on the calling thread, immediately."""
    return target()


SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


class Database:
    """A single SQLite connection, guarded by a lock and driven from an executor."""

    def __init__(self, connection: sqlite3.Connection, executor: Executor) -> None:
        self._connection = connection
        self._executor = executor
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: str | Path, executor: Executor) -> Self:
        def connect() -> sqlite3.Connection:
            # check_same_thread=False because the executor hands work to a pool; the lock
            # above is what actually keeps access serialised.
            connection = sqlite3.connect(str(path), check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            return connection

        return cls(await executor(connect), executor)

    async def close(self) -> None:
        async with self._lock:
            await self._executor(self._connection.close)

    # -- queries -----------------------------------------------------------------------

    async def execute(self, sql: str, params: Sequence[object] = ()) -> None:
        async with self._lock:
            await self._executor(partial(self._execute, sql, params))

    async def fetch_all(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        async with self._lock:
            return await self._executor(partial(self._fetch_all, sql, params))

    async def fetch_one(self, sql: str, params: Sequence[object] = ()) -> sqlite3.Row | None:
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    def _execute(self, sql: str, params: Sequence[object]) -> None:
        with self._connection:
            self._connection.execute(sql, tuple(params))

    def _fetch_all(self, sql: str, params: Sequence[object]) -> list[sqlite3.Row]:
        cursor = self._connection.execute(sql, tuple(params))
        try:
            return cursor.fetchall()
        finally:
            cursor.close()

    # -- migrations --------------------------------------------------------------------

    async def migrate(self) -> int:
        """Apply every migration this database has not seen, each in a transaction.

        Forward-only. A restored backup is the rollback path, because a half-applied
        reversal is worse than no reversal.
        """
        async with self._lock:
            return await self._executor(self._migrate)

    def _migrate(self) -> int:
        self._connection.executescript(SCHEMA_VERSION_TABLE)
        applied = {
            int(row["version"])
            for row in self._connection.execute("SELECT version FROM schema_version")
        }

        latest = max(applied, default=0)
        for path in sorted(MIGRATIONS.glob("*.sql")):
            version = int(path.name.split("_", 1)[0])
            if version in applied:
                continue
            with self._connection:
                self._connection.executescript(path.read_text(encoding="utf-8"))
                self._connection.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, datetime('now'))",
                    (version,),
                )
            latest = max(latest, version)
        return latest
