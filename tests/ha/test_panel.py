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

PANEL_SOURCE = Path("custom_components/filament_ledger/www") / PANEL_FILE


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


class TestPanelSource:
    """Two rules about the panel's own source that a reader would otherwise have to hold.

    [ADR-0006](docs/adr/0006-vanilla-panel.md) accepted having no JavaScript harness, and
    [CONTRIBUTING](CONTRIBUTING.md) answers that with a hand-verification checklist. These
    two are the part of it a machine can hold: both defend a failure that is *silent* on
    screen, which is exactly the kind a checklist executed by a tired person misses.
    """

    def source(self) -> str:
        return PANEL_SOURCE.read_text(encoding="utf-8")

    def test_finishing_a_spool_reconciles_to_a_net_zero_not_a_gross_one(self) -> None:
        """Zero net is not zero gross, and the difference is a whole reel.

        Marking a spool finished sends a measured **0 g** to `spools/reconcile`. With
        `includes_core` true the use case would read that as a scale reading and subtract
        the empty reel from it (`Spool.net_from_gross`), reconciling the spool to minus its
        own core — a 250 g error, written as a correction, on the one operation whose
        entire purpose is to make the ledger honest. Nothing on screen would say so.
        """
        block = re.search(r'case "finish":(.*?)\bbreak;', self.source(), re.DOTALL)
        assert block is not None, "the finish dispatch has moved or been renamed"

        body = block.group(1)
        assert '"spools/reconcile"' in body, "finishing must go through the reconcile path"
        assert "measured_g: 0," in body
        assert "includes_core: false," in body

    def test_every_action_the_panel_renders_is_one_its_dispatcher_handles(self) -> None:
        """A button wired to nothing looks exactly like a button that works.

        The panel has one click listener and dispatches on `data-action`, so a renderer
        that emits a name the switch does not carry produces a control that swallows every
        tap in silence — no error, no console line, nothing to notice until somebody
        presses it twice and gives up. The set is small and closed; keeping it closed is a
        regex away.
        """
        source = self.source()
        rendered = set(re.findall(r'data-action="([\w-]+)"', source))
        handled = set(re.findall(r'^\s+case "([\w-]+)":', source, re.MULTILINE))

        assert rendered - handled == set(), "rendered by the panel, handled by nothing"

    def test_every_spool_choice_opens_the_picker_and_never_a_native_select(self) -> None:
        """A spool is a colour and a balance before it is a name, and a `<select>` shows
        only the name.

        The picker draws the spools the way the mount dialog does — cards, in sections —
        and the rule (06 §6.3) is that every place the panel asks *which spool* opens it.
        The review card's charge row was the last native dropdown, and the user found it
        unintuitive; this keeps it from coming back. The selects that remain choose a
        material and a discard mode, neither of which is a spool.
        """
        source = self.source()
        selects = re.findall(r"<select\b[^>]*>", source)
        assert selects, "the panel has lost every <select>; the pattern has drifted"
        for tag in selects:
            assert "rv-pick" not in tag, "the review card's dropdown is back"
            assert "spool" not in tag.lower(), f"a spool is chosen in the picker, not in {tag}"

        # The reassign field and both faces of a charge row — chosen and not yet chosen —
        # each carry the button that opens the picker.
        assert source.count('data-action="open-spool-picker"') == 3


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
