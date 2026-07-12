"""The vesc express integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BMS_CAN_ID,
    CONF_CELL_TYPE,
    CONF_CONTROLLER_CAN_ID,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .coordinator import VescCoordinator
from .soc import CELL_TYPE_UNKNOWN

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    options = entry.options or {}
    data = entry.data

    coordinator = VescCoordinator(
        hass,
        entry_id=entry.entry_id,
        host=data["host"],
        port=data["port"],
        controller_can_id=options.get(
            CONF_CONTROLLER_CAN_ID, data.get(CONF_CONTROLLER_CAN_ID, 0)
        ),
        bms_can_id=options.get(CONF_BMS_CAN_ID, data.get(CONF_BMS_CAN_ID, 1)),
        poll_interval=options.get(
            CONF_POLL_INTERVAL, data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        ),
        cell_type=options.get(
            CONF_CELL_TYPE, data.get(CONF_CELL_TYPE, CELL_TYPE_UNKNOWN)
        ),
    )

    # First refresh: don't block setup if the board happens to be asleep --
    # a failed first poll just means entities start out "disconnected".
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
