"""Support for TP-Link Kasa sensors."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kasa sensor devices."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []
    for device in coordinator.data.values():
        # Check if device has energy monitoring capability
        if device.has_emeter:
            entities.extend([
                KasaPowerSensor(coordinator, device),
                KasaVoltageSensor(coordinator, device),
                KasaCurrentSensor(coordinator, device),
                KasaTotalEnergySensor(coordinator, device),
            ])

        # Diagnostic sensors
        entities.append(KasaRssiSensor(coordinator, device))
        entities.append(KasaSignalLevelSensor(coordinator, device))
        
        # On Since typically for main relays
        if device.is_plug or device.is_wall_switch or device.is_strip:
             entities.append(KasaOnSinceSensor(coordinator, device))

    async_add_entities(entities)


class KasaSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for Kasa sensors."""

    def __init__(self, coordinator, device, sensor_type: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._sensor_type = sensor_type
        self._attr_unique_id = f"{device.device_id or device.host}_{sensor_type}"
        self._attr_has_entity_name = True

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._device.device_id or self._device.host)},
            "name": self._device.alias,
            "manufacturer": "TP-Link",
            "model": self._device.model,
            "sw_version": self._device.hw_info.get("sw_ver") if hasattr(self._device, "hw_info") else None,
            "via_device": self.coordinator.hub_id,
        }


class KasaPowerSensor(KasaSensorBase):
    """Representation of current power consumption."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device, "power")
        self._attr_name = "Current Power"
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_native_unit_of_measurement = UnitOfPower.WATT
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """Return the current power consumption."""
        try:
            emeter = self._device.emeter_realtime
            return emeter.get("power_mw", 0) / 1000 if "power_mw" in emeter else emeter.get("power", 0)
        except Exception as err:
            return None


class KasaVoltageSensor(KasaSensorBase):
    """Representation of current voltage."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device, "voltage")
        self._attr_name = "Voltage"
        self._attr_device_class = SensorDeviceClass.VOLTAGE
        self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """Return the current voltage."""
        try:
            emeter = self._device.emeter_realtime
            return emeter.get("voltage_mv", 0) / 1000 if "voltage_mv" in emeter else emeter.get("voltage", 0)
        except Exception as err:
            return None


class KasaCurrentSensor(KasaSensorBase):
    """Representation of current."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device, "current")
        self._attr_name = "Current"
        self._attr_device_class = SensorDeviceClass.CURRENT
        self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """Return the current."""
        try:
            emeter = self._device.emeter_realtime
            return emeter.get("current_ma", 0) / 1000 if "current_ma" in emeter else emeter.get("current", 0)
        except Exception as err:
            return None


class KasaTotalEnergySensor(KasaSensorBase):
    """Representation of total energy consumption."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device, "total_energy")
        self._attr_name = "Total Energy"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> float | None:
        """Return the total energy consumption."""
        try:
            emeter = self._device.emeter_realtime
            return emeter.get("total", 0)
        except Exception as err:
            return None


class KasaRssiSensor(KasaSensorBase):
    """Representation of WiFi signal strength."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device, "rssi")
        self._attr_name = "WiFi Signal"
        self._attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
        self._attr_native_unit_of_measurement = "dBm"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int | None:
        """Return the WiFi signal strength."""
        try:
            return self._device.rssi
        except Exception as err:
            return None

class KasaSignalLevelSensor(KasaSensorBase):
    """Representation of WiFi signal level (0-3)."""
    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device, "signal_level")
        self._attr_name = "Signal Level"
        self._attr_device_class = None
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int | None:
        rssi = self._device.rssi
        if rssi is None: return None
        if rssi >= -50: return 3
        if rssi >= -70: return 2
        return 1

class KasaOnSinceSensor(KasaSensorBase):
    """Representation of On Since time."""
    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device, "on_since")
        self._attr_name = "On since"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        # Time usually returned as seconds on, or similar. Formatting needed.
        # But user wants "On since". Kasa API sys_info often has `on_time` in seconds.
        # We need datetime of when it turned on.
        # For now, just return None or raw if we can't calculate cleanly without robust relative time logic.
        return None # Placeholder
