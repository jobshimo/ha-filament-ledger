"""Constants shared across the infrastructure layer.

Nothing in here is a business rule. The domain does not import this module and never will.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "filament_ledger"

# Configuration
CONF_DEFAULT_OPENING_WEIGHT: Final = "default_opening_weight"
CONF_DEFAULT_CORE_WEIGHT: Final = "default_core_weight"
CONF_ANOMALY_THRESHOLD: Final = "anomaly_threshold"
CONF_AUTO_MOUNT_ON_RFID: Final = "auto_mount_on_rfid"
CONF_AUTO_REGISTER_ON_DETECT: Final = "auto_register_on_detect"

DEFAULT_OPENING_WEIGHT_G: Final = 1000
# Bambu spools are roughly this. It is a starting point the user corrects per vendor, not a
# fact — and the domain refuses to default it at all, so this value is only ever a
# suggestion the service layer fills in. See docs/02-domain-model.md §2.8.
DEFAULT_CORE_WEIGHT_G: Final = 250
DEFAULT_ANOMALY_THRESHOLD_PCT: Final = 15
# On by default: a detected tag mounting its spool is the product working as designed. The
# option exists for users who keep spools registered to a shelf and load them briefly —
# silently rewriting their locations is not a service. See docs/04-use-cases.md UC-02.
DEFAULT_AUTO_MOUNT_ON_RFID: Final = True
# On by default too: an unknown Bambu tag whose reading names material and colour is a
# spool the printer just described in full, and registering it with the configured
# defaults is the product working as designed. Off, every unknown tag only reports, which
# suits users who register by hand with a real weight. See docs/04-use-cases.md UC-02.
DEFAULT_AUTO_REGISTER_ON_DETECT: Final = True

DATABASE_FILENAME: Final = "filament_ledger.db"

# Panel
PANEL_URL: Final = "filament-ledger"
PANEL_TITLE: Final = "Filament"
PANEL_ICON: Final = "mdi:printer-3d-nozzle"
PANEL_COMPONENT: Final = "filament-ledger-panel"
STATIC_URL: Final = "/filament_ledger_static"

# Services — one per use case. No service performs two operations, and no use case is
# reachable through two services.
SERVICE_REGISTER_SPOOL: Final = "register_spool"
SERVICE_RECONCILE_SPOOL: Final = "reconcile_spool"
SERVICE_DISCARD_FILAMENT: Final = "discard_filament"
SERVICE_ADJUST_SPOOL: Final = "adjust_spool"
SERVICE_MOUNT_SPOOL: Final = "mount_spool"
SERVICE_UNMOUNT_SPOOL: Final = "unmount_spool"
SERVICE_APPROVE_REVIEW: Final = "approve_review"
SERVICE_DISMISS_REVIEW: Final = "dismiss_review"
# The one exception to "one service per use case", and a narrow one: the service runs the
# same reconciliation pass startup runs — DetectSpool per tray — not a second operation.
SERVICE_SYNC_TRAYS: Final = "sync_trays"

# Events on the Home Assistant bus, prefixed so they never collide with another integration.
EVENT_PREFIX: Final = f"{DOMAIN}_"
