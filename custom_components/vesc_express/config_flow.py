"""Config flow for the vesc express integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_BMS_CAN_ID,
    CONF_CELL_TYPE,
    CONF_CONTROLLER_CAN_ID,
    CONF_POLL_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
)
from .soc import CELL_TYPE_UNKNOWN, cell_type_labels
from .vesc_protocol import VescClient, VescProtocolError

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required("host"): str,
        vol.Required("port", default=DEFAULT_PORT): int,
        vol.Required(CONF_CONTROLLER_CAN_ID, default=0): int,
        vol.Required(CONF_BMS_CAN_ID, default=1): int,
        vol.Required(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): int,
        vol.Required(CONF_CELL_TYPE, default=CELL_TYPE_UNKNOWN): vol.In(
            cell_type_labels()
        ),
    }
)


class VescConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle setup via the UI."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            client = VescClient(user_input["host"], user_input["port"])
            try:
                # Cheapest possible reachability check. If the board happens
                # to be asleep right now, that's fine -- setup can still
                # proceed, this is just a friendly early signal when it's
                # obviously a wrong host/port.
                await client.get_fw_version()
            except (VescProtocolError, OSError, TimeoutError) as err:
                _LOGGER.debug(
                    "Could not reach %s:%s during setup (board may just be "
                    "asleep -- proceeding anyway): %s",
                    user_input["host"],
                    user_input["port"],
                    err,
                )

            await self.async_set_unique_id(f"{user_input['host']}:{user_input['port']}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"VESC Express ({user_input['host']})",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return VescOptionsFlow(config_entry)


class VescOptionsFlow(config_entries.OptionsFlow):
    """Allow changing poll interval / CAN IDs after setup without re-adding."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self._config_entry.data, **self._config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CONTROLLER_CAN_ID,
                    default=current.get(CONF_CONTROLLER_CAN_ID, 0),
                ): int,
                vol.Required(
                    CONF_BMS_CAN_ID, default=current.get(CONF_BMS_CAN_ID, 1)
                ): int,
                vol.Required(
                    CONF_POLL_INTERVAL,
                    default=current.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): int,
                vol.Required(
                    CONF_CELL_TYPE,
                    default=current.get(CONF_CELL_TYPE, CELL_TYPE_UNKNOWN),
                ): vol.In(cell_type_labels()),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
