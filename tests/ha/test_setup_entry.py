"""The composition root's setup path, when it fails partway.

Home Assistant answers a failed `async_setup_entry` by running the entry's `async_on_unload`
callbacks — and never calls `async_unload_entry`, because nothing was loaded. Whatever setup
opened before the failure must therefore be registered there, or it outlives the attempt;
and every retry opens it again.
"""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from custom_components.filament_ledger import async_setup_entry
from custom_components.filament_ledger.infrastructure.ha.runtime import LedgerConfigEntry
from custom_components.filament_ledger.infrastructure.persistence import printer_adoption
from custom_components.filament_ledger.infrastructure.persistence.database import (
    Database,
    run_inline,
)

from .conftest import FakeConfigEntry, FakeHass, as_hass
from .test_bambu_gateway import plant_registry


async def _process_on_unload(entry: FakeConfigEntry) -> None:
    """What `ConfigEntry._async_process_on_unload` does: last registered runs first, and a
    callback that returns a coroutine has it awaited."""
    while entry.unload_callbacks:
        # `object`, not the fake's `None`: the real registry takes coroutine functions too,
        # and that is exactly the case this runner exists to exercise.
        result: object = entry.unload_callbacks.pop()()
        if inspect.isawaitable(result):
            await result


class TestASetupThatFails:
    async def test_closes_the_database_it_opened(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hass = FakeHass()
        # The two pieces of `hass` setup reads that the shared fake has no use for elsewhere.
        config = SimpleNamespace(path=lambda name: str(tmp_path / name), language="en")
        monkeypatch.setattr(hass, "config", config, raising=False)
        monkeypatch.setattr(hass, "async_add_executor_job", run_inline, raising=False)
        plant_registry(hass, [])

        opened: list[Database] = []
        original_open = Database.open.__func__  # type: ignore[attr-defined]

        async def recording_open(cls: type[Database], *args: object) -> Database:
            database = cast(Database, await original_open(cls, *args))
            opened.append(database)
            return database

        monkeypatch.setattr(Database, "open", classmethod(recording_open))

        async def failing_adoption(*_args: object) -> None:
            msg = "the registry went away mid-setup"
            raise RuntimeError(msg)

        monkeypatch.setattr(printer_adoption, "adopt_unidentified_trays", failing_adoption)

        entry = FakeConfigEntry()
        with pytest.raises(RuntimeError):
            await async_setup_entry(as_hass(hass), cast(LedgerConfigEntry, entry))
        await _process_on_unload(entry)

        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            await opened[0].fetch_all("SELECT 1")
