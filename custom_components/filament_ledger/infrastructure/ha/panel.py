"""Sidebar panel registration.

The panel is served as a plain ES module from the integration directory. No build step, no
bundler, no committed dist folder — see docs/adr/0006-vanilla-panel.md for why v1 does it
this way rather than with Lit and Vite.
"""

from __future__ import annotations

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


async def async_register_panel(hass: HomeAssistant) -> None:
    if PANEL_URL in hass.data.get(frontend.DATA_PANELS, {}):
        return

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                url_path=STATIC_URL,
                path=str(PANEL_DIR),
                # The panel changes only when the integration is updated, so letting the
                # browser cache it is safe and keeps a phone at the printer responsive.
                cache_headers=False,
            )
        ]
    )

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=PANEL_COMPONENT,
        frontend_url_path=PANEL_URL,
        module_url=f"{STATIC_URL}/{PANEL_FILE}",
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
