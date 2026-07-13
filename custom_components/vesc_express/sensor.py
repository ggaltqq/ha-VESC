"""Sensor entities for the vesc express integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            VescControllerSensor(coordinator, entry),
            VescBmsSensor(coordinator, entry),
            VescOdometerSensor(coordinator, entry),
        ]
    )


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="VESC Express",
        manufacturer="Vedder / community",
        model="VESC Express",
    )


class VescControllerSensor(CoordinatorEntity, SensorEntity):
    """State is literally the string 'connected' or 'disconnected'.

    Deliberately never goes to HA's unavailable/unknown -- this entity's
    whole purpose is to answer "is the board reachable right now", so it
    always has an opinion.
    """

    _attr_has_entity_name = True
    _attr_name = "Controller"
    _attr_icon = "mdi:chip"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_controller"
        self._attr_device_info = _device_info(entry)

    @property
    def available(self) -> bool:
        return True  # always report something, never go unavailable

    @property
    def native_value(self) -> str:
        connected = self.coordinator.data.get("controller_connected")
        return "connected" if connected else "disconnected"

    @property
    def extra_state_attributes(self) -> dict:
        c = self.coordinator.data.get("controller")
        if c is None:
            return {}
        attrs = {
            "duty_cycle": c.duty_now,
            "rpm": c.rpm,
            "input_voltage": c.v_in,
            "motor_current": c.current_motor,
            "input_current": c.current_in,
            "mosfet_temp": c.temp_mos,
            "motor_temp": c.temp_motor,
            "amp_hours": c.amp_hours,
            "amp_hours_charged": c.amp_hours_charged,
            "watt_hours": c.watt_hours,
            "watt_hours_charged": c.watt_hours_charged,
            "tachometer": c.tachometer,
            "tachometer_abs": c.tachometer_abs,
            "fault_code": c.fault_code,
        }
        return attrs


class VescBmsSensor(CoordinatorEntity, SensorEntity):
    """State is SoC %. Follows normal HA numeric-sensor availability rules.

    - unavailable: controller itself isn't reachable (board asleep/offline)
    - unknown: controller is reachable but the BMS-specific read failed
    - a number: everything's fine
    """

    _attr_has_entity_name = True
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_bms"
        self._attr_device_info = _device_info(entry)

    @property
    def available(self) -> bool:
        # Available (though possibly "unknown") whenever the controller is
        # reachable -- only truly unavailable when the whole board is down.
        return bool(self.coordinator.data.get("controller_connected"))

    @property
    def native_value(self):
        # soc_percent already prefers the BMS reading and falls back to a
        # voltage-based estimate; None renders as "unknown" (still available).
        soc = self.coordinator.data.get("soc_percent")
        return round(soc, 1) if soc is not None else None

    @property
    def extra_state_attributes(self) -> dict:
        bms = self.coordinator.data.get("bms")
        if bms is None:
            return {}
        attrs = {
            "charging": bms.charging,
            "pack_voltage": bms.pack_voltage,
            "charge_voltage": bms.charge_voltage,
            "pack_current": bms.pack_current,
            "current_main": bms.current_main,
            "current_ic": bms.current_ic,
            # True when the value is a voltage estimate, not a BMS reading.
            "soc_estimated": self.coordinator.data.get("soc_estimated", False),
            "bms_reported_soc": bms.soc_percent,
        }
        if bms.cells:
            attrs["cell_count"] = len(bms.cells)
            attrs["min_cell_voltage"] = round(min(bms.cells), 3)
            attrs["max_cell_voltage"] = round(max(bms.cells), 3)
        return attrs


class VescOdometerSensor(CoordinatorEntity, SensorEntity):
    """Persistent lifetime distance (the "life" odometer stored on the board).

    Always meters on the wire regardless of the VESC km/miles display setting
    (that setting is client-side only); exposed here in km. Uses
    TOTAL_INCREASING so Home Assistant's long-term statistics accumulate
    correctly -- the value is board-persisted and monotonic, surviving reboots.
    """

    _attr_has_entity_name = True
    _attr_name = "Odometer"
    _attr_icon = "mdi:counter"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_odometer"
        self._attr_device_info = _device_info(entry)

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data.get("controller_connected"))

    @property
    def native_value(self):
        meters = self.coordinator.data.get("odometer_m")
        return round(meters / 1000, 3) if meters is not None else None
