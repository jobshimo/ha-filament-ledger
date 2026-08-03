"""Sidebar panel registration.

The panel is served as a plain ES module from the integration directory. No build step, no
bundler, no committed dist folder — see docs/adr/0006-vanilla-panel.md for why v1 does it
this way rather than with Lit and Vite.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from ...const import (
    DOMAIN,
    PANEL_COMPONENT,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL,
    STATIC_URL,
)

LOGGER = logging.getLogger(__name__)

PANEL_DIR = Path(__file__).parent.parent.parent / "www"
PANEL_FILE = "filament-ledger-panel.js"
_MANIFEST = Path(__file__).parent.parent.parent / "manifest.json"

# The static path outlives the panel deliberately: Home Assistant has no unregister API
# for static paths, so it is registered once per run and left in place. Without this flag
# every unload/reload cycle would stack another identical route onto the router.
_STATIC_PATH_REGISTERED = f"{DOMAIN}_static_path_registered"


def _read_version() -> str:
    """The version of record, read from the file that holds it.

    `manifest.json` is the version of record — RELEASING.md says so and the release workflow
    refuses a tag that disagrees with it — so reading it here means the URL a browser caches
    and the code it is caching cannot drift apart.

    Read directly rather than through `homeassistant.loader`: the loader answers the same
    question but only once its own registry has been built, which couples panel registration
    to a part of Home Assistant this adapter otherwise never touches. The file sits beside
    this module and is the authority either way.

    Anything unreadable falls back to `dev`, which is only reachable in a checkout with a
    broken manifest — and `dev` is honest, where a made-up number would not be.
    """
    try:
        manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
        return str(manifest["version"])
    except OSError, ValueError, KeyError:  # pragma: no cover - a broken checkout only
        LOGGER.warning("could not read a version from %s; serving the panel as 'dev'", _MANIFEST)
        return "dev"


async def async_register_panel(hass: HomeAssistant) -> None:
    if PANEL_URL in hass.data.get(frontend.DATA_PANELS, {}):
        return

    # The version rides in the **path**, not in a query string.
    #
    # `cache_headers=True` is what makes Home Assistant send `Cache-Control: max-age` — a
    # month of it — and the panel now ships 125 KB of typefaces on top of its own weight, so
    # revalidating all of it on every load is real cost on the phone this is used from.
    #
    # But a cached panel that survives an upgrade is a user running last month's code, and a
    # query string would only bust the one file it is attached to: `panel.js?v=1.1.0` imports
    # `./i18n.js`, which resolves **without** the query and would stay stale for a month.
    # Versioning the directory busts the module, its imports, and the fonts together, because
    # every relative resolution inherits the prefix.
    # Off the loop: one small read, but setup runs in the event loop and file I/O there is
    # the kind of thing that is fine until somebody's config is on a slow mount.
    version = await hass.async_add_executor_job(_read_version)
    static_root = f"{STATIC_URL}/{version}"

    if not hass.data.get(_STATIC_PATH_REGISTERED):
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    url_path=static_root,
                    path=str(PANEL_DIR),
                    # Safe precisely because the URL changes when the code does.
                    cache_headers=True,
                )
            ]
        )
        hass.data[_STATIC_PATH_REGISTERED] = True

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=PANEL_COMPONENT,
        frontend_url_path=PANEL_URL,
        module_url=f"{static_root}/{PANEL_FILE}",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        # Not admin-only: weighing a spool is not an administrative act, and the queue only
        # works if the person standing at the printer can reach it.
        require_admin=False,
        config={"domain": DOMAIN},
    )
    LOGGER.debug("registered %s panel at /%s", DOMAIN, PANEL_URL)


def async_remove_panel(hass: HomeAssistant) -> None:
    if PANEL_URL in hass.data.get(frontend.DATA_PANELS, {}):
        frontend.async_remove_panel(hass, PANEL_URL)
