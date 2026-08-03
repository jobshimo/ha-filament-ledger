"""Sidebar panel lifecycle: register once, survive reloads, remove quietly.

Home Assistant has no unregister API for static paths, so the route must outlive the
panel — and the guards in `panel.py` are what keep an unload/reload cycle from stacking
identical routes or warning about panels that were never there. The real `frontend` and
`panel_custom` modules run here; only `hass` is faked.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import cast

import pytest
from homeassistant.components import frontend

from custom_components.filament_ledger.infrastructure.ha.panel import (
    PANEL_FILE,
    async_register_panel,
    async_remove_panel,
)

from .conftest import FakeHass, as_hass


def manifest_version() -> str:
    """Read independently of the panel module, so the test asserts the *manifest's* version
    rather than agreeing with whatever `panel.py` happened to compute."""
    manifest = Path("custom_components/filament_ledger/manifest.json")
    return cast("str", json.loads(manifest.read_text(encoding="utf-8"))["version"])


@pytest.fixture
def hass() -> FakeHass:
    return FakeHass()


def panels(hass: FakeHass) -> dict[str, frontend.Panel]:
    return cast("dict[str, frontend.Panel]", hass.data.get(frontend.DATA_PANELS) or {})


class TestRegister:
    async def test_the_panel_and_its_static_files_are_registered(self, hass: FakeHass) -> None:
        await async_register_panel(as_hass(hass))

        panel = panels(hass)["filament-ledger"]
        assert panel.component_name == "custom"
        assert panel.sidebar_title == "Filament"
        assert panel.sidebar_icon == "mdi:printer-3d-nozzle"
        # Weighing a spool is not an administrative act.
        assert panel.require_admin is False
        config = panel.config or {}
        assert config["domain"] == "filament_ledger"
        custom = cast("dict[str, object]", config["_panel_custom"])
        assert custom["module_url"] == f"/filament_ledger_static/{manifest_version()}/{PANEL_FILE}"

        (static,) = hass.http.static_paths
        assert static.url_path == f"/filament_ledger_static/{manifest_version()}"
        assert Path(static.path).name == "www"
        assert (Path(static.path) / PANEL_FILE).is_file()

    async def test_the_url_carries_the_version_and_the_files_are_cached(
        self, hass: FakeHass
    ) -> None:
        """Caching and busting are one decision, and neither is safe without the other.

        `cache_headers=True` is what makes Home Assistant send a month of `max-age`, which
        the panel wants: it ships 125 KB of typefaces on top of its own weight. A cached
        panel that survives an upgrade is a user running last month's code, so the version
        rides in the **path** — a query string would bust only the file it is attached to,
        while `panel.js` imports `./i18n.js`, which resolves without the query and would
        stay stale for the whole month.
        """
        await async_register_panel(as_hass(hass))
        (static,) = hass.http.static_paths

        assert static.cache_headers is True
        # Not "some version": the one in the file the release workflow checks the tag against.
        assert static.url_path.endswith(f"/{manifest_version()}")
        assert manifest_version() != "dev"

        custom = cast(
            "dict[str, object]", (panels(hass)["filament-ledger"].config or {})["_panel_custom"]
        )
        module_url = cast("str", custom["module_url"])
        assert module_url.startswith(static.url_path + "/")
        # Every relative import and every font resolves under the same versioned prefix,
        # which is the whole reason the segment is in the path rather than the query.
        assert "?" not in module_url

    async def test_every_face_the_panel_declares_is_a_file_that_ships(self, hass: FakeHass) -> None:
        """The panel names eight woff2 files and the static route is what serves them.

        There is no JS harness, so this is the only automatic check that a typeface named in
        the module is a typeface present in the package. A rename, a missed `git add` or a
        packaging filter that drops binaries would otherwise reach a user as text silently
        rendered in the fallback face — the failure mode 16 §16.9 warns about, and one nobody
        gets an error for.
        """
        await async_register_panel(as_hass(hass))
        (static,) = hass.http.static_paths
        fonts = Path(static.path) / "fonts"

        panel_source = (Path(static.path) / "filament-ledger-panel.js").read_text("utf-8")
        declared = set(re.findall(r'file: "([^"]+\.woff2)"', panel_source))
        assert len(declared) == 8

        for name in declared:
            face = fonts / name
            assert face.is_file(), f"{name} is declared by the panel but absent from www/fonts"
            assert face.stat().st_size > 0

        # The licence travels with the fonts: both families are SIL OFL 1.1, which obliges the
        # notice to accompany the font software wherever it is redistributed.
        assert (fonts / "OFL-Space-Grotesk.txt").is_file()
        assert (fonts / "OFL-IBM-Plex.txt").is_file()

    async def test_registering_twice_neither_stacks_routes_nor_raises(self, hass: FakeHass) -> None:
        """Without the guard, `frontend` raises "Overwriting panel" and a second static
        route lands on the router. Setup runs on every reload, so twice is normal."""
        await async_register_panel(as_hass(hass))
        await async_register_panel(as_hass(hass))

        assert list(panels(hass)) == ["filament-ledger"]
        assert len(hass.http.static_paths) == 1


class TestRemove:
    async def test_remove_then_register_comes_back_without_a_second_route(
        self, hass: FakeHass
    ) -> None:
        await async_register_panel(as_hass(hass))
        async_remove_panel(as_hass(hass))
        assert "filament-ledger" not in panels(hass)

        await async_register_panel(as_hass(hass))

        assert "filament-ledger" in panels(hass)
        # The static route deliberately outlives the panel: registered once per run.
        assert len(hass.http.static_paths) == 1

    def test_removing_when_absent_neither_warns_nor_crashes(
        self, hass: FakeHass, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`frontend.async_remove_panel` logs "Removing unknown panel" when asked to remove
        what is not there; the guard means unload never asks."""
        with caplog.at_level(logging.WARNING):
            async_remove_panel(as_hass(hass))
        assert caplog.records == []
