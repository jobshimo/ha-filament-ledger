"""Config flow.

One entry, no printer. "Manual inventory only" is a fully supported mode rather than a
degraded one — it is what Phase 1 delivers, and it is what a user without a Bambu printer
gets permanently.

Everything chosen here is editable afterwards through the options flow. A setting that can
only be chosen during installation is a setting the user will get wrong once and live with.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import NumberSelector, NumberSelectorMode

from .const import (
    CONF_ANOMALY_THRESHOLD,
    CONF_AUTO_MOUNT_ON_RFID,
    CONF_AUTO_REGISTER_ON_DETECT,
    CONF_DEFAULT_CORE_WEIGHT,
    CONF_DEFAULT_OPENING_WEIGHT,
    CONF_LOW_STOCK_THRESHOLD,
    DEFAULT_ANOMALY_THRESHOLD_PCT,
    DEFAULT_AUTO_MOUNT_ON_RFID,
    DEFAULT_AUTO_REGISTER_ON_DETECT,
    DEFAULT_CORE_WEIGHT_G,
    DEFAULT_LOW_STOCK_THRESHOLD_G,
    DEFAULT_OPENING_WEIGHT_G,
    DOMAIN,
)
from .infrastructure.ha.runtime import LedgerConfigEntry
from .infrastructure.ha.validators import low_stock_threshold

TITLE = "Filament Ledger"


class LowStockThresholdSelector(NumberSelector):
    """Expose a serializable number field while retaining exact gram validation."""

    def __call__(self, data: object) -> float:
        return low_stock_threshold(data)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_LOW_STOCK_THRESHOLD,
                default=defaults.get(CONF_LOW_STOCK_THRESHOLD, DEFAULT_LOW_STOCK_THRESHOLD_G),
            ): LowStockThresholdSelector(
                {
                    "min": 0.001,
                    "max": 10000,
                    "step": 0.001,
                    "mode": NumberSelectorMode.BOX,
                    "unit_of_measurement": "g",
                }
            ),
            vol.Required(
                CONF_DEFAULT_OPENING_WEIGHT,
                default=defaults.get(CONF_DEFAULT_OPENING_WEIGHT, DEFAULT_OPENING_WEIGHT_G),
            ): vol.All(cv.positive_int, vol.Range(min=1, max=10000)),
            vol.Required(
                CONF_DEFAULT_CORE_WEIGHT,
                default=defaults.get(CONF_DEFAULT_CORE_WEIGHT, DEFAULT_CORE_WEIGHT_G),
            ): vol.All(cv.positive_int, vol.Range(min=0, max=2000)),
            vol.Required(
                CONF_ANOMALY_THRESHOLD,
                default=defaults.get(CONF_ANOMALY_THRESHOLD, DEFAULT_ANOMALY_THRESHOLD_PCT),
            ): vol.All(cv.positive_int, vol.Range(min=1, max=100)),
            vol.Required(
                CONF_AUTO_MOUNT_ON_RFID,
                default=defaults.get(CONF_AUTO_MOUNT_ON_RFID, DEFAULT_AUTO_MOUNT_ON_RFID),
            ): cv.boolean,
            vol.Required(
                CONF_AUTO_REGISTER_ON_DETECT,
                default=defaults.get(CONF_AUTO_REGISTER_ON_DETECT, DEFAULT_AUTO_REGISTER_ON_DETECT),
            ): cv.boolean,
        }
    )


class FilamentLedgerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # A ledger is a single inventory. A second one would be a second source of truth
        # about the same shelf, which is the arrangement this project exists to avoid.
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title=TITLE, data=user_input)

        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    def async_get_options_flow(entry: LedgerConfigEntry) -> OptionsFlow:
        return FilamentLedgerOptionsFlow()


class FilamentLedgerOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
