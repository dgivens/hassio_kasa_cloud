"""Support for TP-Link Kasa Cloud buttons."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
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
    """Set up Kasa button devices."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []
    for device in coordinator.data.values():
        entities.append(KasaRebootButton(coordinator, device))

    async_add_entities(entities)


class KasaRebootButton(CoordinatorEntity, ButtonEntity):
    """Representation of a Kasa Reboot Button."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_reboot"
        self._attr_name = "Reboot"
        self._attr_has_entity_name = True
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._device.device_id)},
            "name": self._device.alias,
            "manufacturer": "TP-Link",
            "model": self._device.model,
            "sw_version": self._device.hw_info.get("sw_ver") if self._device.hw_info else None,
            "via_device": self.coordinator.hub_id,
        }

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._device.reboot()
