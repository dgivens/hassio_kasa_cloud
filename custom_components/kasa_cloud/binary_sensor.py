"""Binary sensor platform for TP-Link Kasa Cloud."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KasaConfigEntry
from .entity import KasaCloudEntity

PARALLEL_UPDATES = 0  # read-only


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: KasaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kasa binary sensor entities."""
    coordinator = config_entry.runtime_data

    entities: list[BinarySensorEntity] = []
    for device in coordinator.data.values():
        entities.append(KasaConnectivitySensor(coordinator, device))
        # Only create this when the device genuinely reports thermal state.
        # Upstream created it for everything, so plugs with no thermal sensor
        # showed a confident permanent "OK".
        if device.overheated is not None:
            entities.append(KasaOverheatSensor(coordinator, device))

    async_add_entities(entities)


class KasaConnectivitySensor(KasaCloudEntity, BinarySensorEntity):
    """Whether the cloud can currently reach the device."""

    _attr_name = "Cloud connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device, key="connectivity")

    @property
    def available(self) -> bool:
        """Always available while the entry is loaded.

        The base class would mark this unavailable exactly when the device is
        unreachable — and with a single-device account "one device failed" is
        also "all devices failed", so the sensor that exists to report an
        outage would be unavailable for the whole outage.
        """
        return True

    @property
    def is_on(self) -> bool | None:
        # A failed poll is itself the reading: the cloud path is down.
        if not self.coordinator.last_update_success:
            return False
        return self._device.is_connected


class KasaOverheatSensor(KasaCloudEntity, BinarySensorEntity):
    """Device-reported overheat state."""

    _attr_name = "Overheated"
    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device, key="overheat")

    @property
    def is_on(self) -> bool | None:
        return self._device.overheated
