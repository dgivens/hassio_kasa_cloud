"""Support for TP-Link Kasa Cloud binary sensors."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    """Set up Kasa binary sensor devices."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []
    for device in coordinator.data.values():
        entities.append(KasaConnectivitySensor(coordinator, device))
        entities.append(KasaOverheatSensor(coordinator, device))
        
        if "ES20M" in device.model or "motion" in device.model.lower():
             entities.append(KasaMotionSensor(coordinator, device))

    async_add_entities(entities)


class KasaConnectivitySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of Kasa Connectivity Status."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_connectivity"
        self._attr_name = "Cloud Connection"
        self._attr_has_entity_name = True
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._device.device_id)},
            "name": self._device.alias,
            "manufacturer": "TP-Link",
            "model": self._device.model,
            "sw_version": self._device.hw_info.get("sw_ver"),
            "via_device": self.coordinator.hub_id,
        }

    @property
    def is_on(self) -> bool:
        """Return True if connected."""
        return self._device.is_connected


class KasaMotionSensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of Kasa Motion Sensor."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_motion"
        self._attr_name = "Motion"
        self._attr_has_entity_name = True
        self._attr_device_class = BinarySensorDeviceClass.MOTION

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device.device_id)},
            "name": self._device.alias,
            "manufacturer": "TP-Link",
            "model": self._device.model,
            "sw_version": self._device.hw_info.get("sw_ver"),
            "via_device": self.coordinator.hub_id,
        }

    @property
    def is_on(self) -> bool:
        """Return True if motion detected."""
        sys_info = self._device.sys_info
        if not sys_info:
            return False
            
        if "motion_detected" in sys_info:
            return sys_info["motion_detected"] == 1
        return False
        
class KasaOverheatSensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of Kasa Overheat Status."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_overheat"
        self._attr_name = "Overheated"
        self._attr_has_entity_name = True
        self._attr_device_class = BinarySensorDeviceClass.HEAT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device.device_id)},
            "name": self._device.alias,
            "manufacturer": "TP-Link",
            "model": self._device.model,
            "sw_version": self._device.hw_info.get("sw_ver"),
            "via_device": self.coordinator.hub_id,
        }

    @property
    def is_on(self) -> bool:
        """Return True if overheated."""
        return self._device.overheated == 1
