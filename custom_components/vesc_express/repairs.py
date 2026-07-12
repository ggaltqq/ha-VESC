"""Repair flow: ask the user for their cell type so SoC can be estimated.

Raised by the coordinator when the BMS reports 0% on a clearly healthy Li-ion
pack and no cell type has been configured. Picking a cell type here writes it
to the config entry's options and reloads the integration.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_CELL_TYPE, DOMAIN  # noqa: F401  (DOMAIN documents intent)
from .soc import CELL_TYPE_UNKNOWN, cell_type_labels


class CellTypeRepairFlow(RepairsFlow):
    """Single-step flow: choose a cell type."""

    def __init__(self, entry_id: str | None) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> FlowResult:
        return await self.async_step_confirm(user_input)

    async def async_step_confirm(
        self, user_input: dict | None = None
    ) -> FlowResult:
        if user_input is not None and self._entry_id is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is not None:
                self.hass.config_entries.async_update_entry(
                    entry,
                    options={**entry.options, CONF_CELL_TYPE: user_input[CONF_CELL_TYPE]},
                )
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self._entry_id)
                )
            return self.async_create_entry(title="", data={})

        # Offer every real cell type (drop the "unknown" placeholder).
        choices = {
            key: label
            for key, label in cell_type_labels().items()
            if key != CELL_TYPE_UNKNOWN
        }
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Required(CONF_CELL_TYPE): vol.In(choices)}),
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None
) -> RepairsFlow:
    return CellTypeRepairFlow((data or {}).get("entry_id"))
