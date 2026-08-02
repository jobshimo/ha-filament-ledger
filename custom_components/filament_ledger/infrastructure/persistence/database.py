"""SQLite access.

Two rules from docs/03-architecture.md §3.7:

1. **Every query runs in an executor.** Blocking the event loop stalls the whole of Home
   Assistant — an unacceptable neighbour effect for an integration.
2. **Access is serialised** by a single lock. SQLite tolerates concurrent readers, but the
   ledger's invariants are easier to reason about when appends and the reads that follow
   them see one consistent sequence.

The connection is opened with ``autocommit=True`` so the standard library performs no
implicit transaction management of its own: a lone statement is its own SQLite transaction,
and the only multi-statement transactions are the ones opened explicitly — by the unit of
work below, or by a migration script's own ``BEGIN``/``COMMIT``. That is what makes the
unit of work honest: nothing can commit behind its back.

The executor is a **port**, injected rather than taken from `hass`, so the whole persistence
layer is testable with a trivial inline runner and no Home Assistant at all.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from functools import partial
from pathlib import Path
from types import TracebackType
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
    """A single SQLite connection, guarded by a lock and driven from an executor.

    Also the infrastructure side of the `UnitOfWork` port: ``async with database:`` holds
    the lock for the whole block and runs every statement issued inside it in one SQLite
    transaction, so a multi-write use case commits entirely or not at all — and no other
    task can read or write between its statements.
    """

    def __init__(self, connection: sqlite3.Connection, executor: Executor) -> None:
        self._connection = connection
        self._executor = executor
        self._lock = asyncio.Lock()
        # The task currently inside the unit of work. `asyncio.Lock` is not re-entrant,
        # so the owner's own statements must bypass the lock — this marker is how
        # `execute` and `fetch_all` recognise them without deadlocking on their own
        # transaction. Any other task queues on the lock as usual.
        self._owner: asyncio.Task[object] | None = None
        # Detached cleanup tasks (see `_settle`). The loop keeps only weak references to
        # tasks; this set is what keeps one alive long enough to release the lock.
        self._drains: set[asyncio.Task[None]] = set()

    @classmethod
    async def open(cls, path: str | Path, executor: Executor) -> Self:
        def connect() -> sqlite3.Connection:
            # check_same_thread=False because the executor hands work to a pool; the lock
            # above is what actually keeps access serialised.
            #
            # autocommit=True hands transaction control to this class. The legacy default
            # opens transactions implicitly and commits them at times the stdlib chooses —
            # which is exactly the behaviour a unit of work cannot coexist with.
            connection = sqlite3.connect(str(path), check_same_thread=False, autocommit=True)
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
        if self._inside_unit_of_work():
            await self._executor(partial(self._execute, sql, params))
            return
        async with self._lock:
            await self._executor(partial(self._execute, sql, params))

    async def fetch_all(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        if self._inside_unit_of_work():
            return await self._executor(partial(self._fetch_all, sql, params))
        async with self._lock:
            return await self._executor(partial(self._fetch_all, sql, params))

    async def fetch_one(self, sql: str, params: Sequence[object] = ()) -> sqlite3.Row | None:
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    def _execute(self, sql: str, params: Sequence[object]) -> None:
        # No transaction management here. Under autocommit=True a lone statement is its
        # own SQLite transaction — the durability the old `with self._connection:` bought —
        # and a statement inside a unit of work joins the transaction the unit opened.
        self._connection.execute(sql, tuple(params))

    def _fetch_all(self, sql: str, params: Sequence[object]) -> list[sqlite3.Row]:
        cursor = self._connection.execute(sql, tuple(params))
        try:
            return cursor.fetchall()
        finally:
            cursor.close()

    # -- unit of work ------------------------------------------------------------------

    def _inside_unit_of_work(self) -> bool:
        return self._owner is not None and asyncio.current_task() is self._owner

    async def __aenter__(self) -> None:
        """Open one atomic unit: the lock for the whole block, then a transaction.

        `BEGIN IMMEDIATE` takes SQLite's write lock up front rather than on the first
        write, so a unit that is going to conflict fails at its border, not halfway
        through its work.
        """
        await self._lock.acquire()
        self._owner = asyncio.current_task()
        await self._settle(partial(self._connection.execute, "BEGIN IMMEDIATE"))

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # A failed COMMIT must not leave its transaction open on a connection that
        # outlives it: the next unit's BEGIN would fail on state it never created.
        # `_settle` owns that cleanup — and the lock — on every failure path.
        if exc_type is None:
            await self._settle(partial(self._connection.execute, "COMMIT"))
        else:
            await self._settle(self._rollback_if_open)
        self._owner = None
        self._lock.release()

    async def _settle(self, statement: Callable[[], object]) -> None:
        """Run one transaction-boundary statement; survive cancellation of the await.

        Cancelling the task that awaits an executor job does not stop a thread already
        running it — `concurrent.futures.Future.cancel` refuses once the job is RUNNING —
        so an abandoned BEGIN or COMMIT keeps executing after the await has raised. Home
        Assistant does exactly this: shutdown gives tracked service calls 0.1 s before
        cancelling them, and unloading an entry cancels whatever is in flight.

        The job is awaited through a shield to tell the two failure modes apart. A plain
        exception is only delivered after the thread has finished, so the connection is
        quiescent: roll back and release the lock inline. A cancellation with the job
        still pending means the statement is in flight and unstoppable: cleanup — and,
        deliberately, the release of the lock — moves to a detached task that waits for
        the thread first. No new unit may BEGIN while a stale statement runs; holding the
        lock until the connection is quiescent again is the point.

        On success the lock is kept: the caller decides what the unit does next.
        """
        job = asyncio.ensure_future(self._executor(statement))
        try:
            await asyncio.shield(job)
        except BaseException:
            if job.done():
                try:
                    await self._executor(self._rollback_if_open)
                finally:
                    self._owner = None
                    self._lock.release()
            else:
                drain = asyncio.get_running_loop().create_task(self._drain(job))
                self._drains.add(drain)
                drain.add_done_callback(self._drains.discard)
            raise

    async def _drain(self, job: asyncio.Future[object]) -> None:
        # Detached from the cancelled caller: wait for the in-flight statement to settle,
        # undo whatever it left open — a no-op if a COMMIT already landed — and only then
        # let the next unit in.
        try:
            with suppress(BaseException):
                await job
            await self._executor(self._rollback_if_open)
        finally:
            self._owner = None
            self._lock.release()

    def _rollback_if_open(self) -> None:
        # Guarded, because SQLite rolls back on its own after certain errors, and a bare
        # ROLLBACK with no transaction active is itself an error.
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")

    # -- migrations --------------------------------------------------------------------

    async def migrate(self) -> int:
        """Apply every migration this database has not seen, each in a transaction.

        Each migration file is self-contained: it opens its own transaction and records
        its own row in `schema_version` before committing, so a crash mid-migration
        leaves nothing behind — no half-created tables, and no committed schema with an
        unrecorded version, which would make every later start fail on "already exists".

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
            try:
                # Under autocommit=True the script's own BEGIN/COMMIT governs: either
                # every statement lands, including the schema_version row, or none do.
                self._connection.executescript(path.read_text(encoding="utf-8"))
            except Exception:
                # executescript stops at the failing statement, leaving the script's
                # BEGIN open on a connection that outlives this attempt. Clean it up so
                # a retry — or anything else on this connection — starts from zero.
                self._rollback_if_open()
                raise
            latest = max(latest, version)
        return latest
