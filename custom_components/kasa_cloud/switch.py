"""Support for TP-Link Kasa smart plugs and switches."""
from __future__ import annotations

import logging
import asyncio
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .cloud_api import KasaCloudChildDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kasa switch devices."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []
    for device in coordinator.data.values():
        if device.is_plug or device.is_wall_switch or device.is_strip:
            # For power strips with multiple outlets
            if device.is_strip and device.has_children:
                # Add main device switch (Control for all outlets)
                entities.append(KasaSwitch(coordinator, device, name_override="Main Power"))
                # Add child switches
                for child in device.children:
                    entities.append(KasaSwitch(coordinator, device, child))
            else:
                entities.append(KasaSwitch(coordinator, device))
            
            # Add configuration switches
            if not device.is_bulb: # LED switch meant for plugs/switches usually
                 entities.append(KasaLedSwitch(coordinator, device))
            
            # Check for Auto-Update/Auto-Off - Only add if not child
            if not isinstance(device, KasaCloudChildDevice):
                 entities.append(KasaAutoUpdateSwitch(coordinator, device))

            # Motion Enable Switch
            if "ES20M" in device.model or "motion" in device.model.lower():
                 entities.append(KasaMotionEnableSwitch(coordinator, device))
            
        elif device.is_dimmable and not device.is_bulb:
            # For dimmer switches, relay is handled in light.py. 
            # We add config switches here.
            entities.append(KasaLedSwitch(coordinator, device))
            entities.append(KasaAutoUpdateSwitch(coordinator, device))
            
            if "ES20M" in device.model or "motion" in device.model.lower():
                 entities.append(KasaMotionEnableSwitch(coordinator, device))

    async_add_entities(entities)


class KasaSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a Kasa Smart Switch/Plug."""

    def __init__(self, coordinator, device, child=None, name_override=None) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device = device
        self._child = child

        # Unique ID construction
        if child:
             self._attr_unique_id = child.device_id
        else:
             self._attr_unique_id = device.device_id or device.host
             if name_override:
                  self._attr_unique_id += f"_{name_override}"

        # Naming
        if name_override:
             self._attr_name = name_override
        elif child:
             self._attr_name = child.alias
        else:
             self._attr_name = None # Use device name
             
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

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        if self._child:
            return self._child.is_on
        return self._device.is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        if self._child:
            await self._child.turn_on()
        elif self._device.is_strip and self._device.has_children:
            # Explicitly turn on all children for "Main Power"
            await asyncio.gather(*[child.turn_on() for child in self._device.children])
        else:
            await self._device.turn_on()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        if self._child:
            await self._child.turn_off()
        elif self._device.is_strip and self._device.has_children:
            # Explicitly turn off all children for "Main Power"
            await asyncio.gather(*[child.turn_off() for child in self._device.children])
        else:
            await self._device.turn_off()
        await self.coordinator.async_request_refresh()


class KasaLedSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of Kasa Device LED Status."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_led"
        self._attr_name = "LED Status"
        self._attr_has_entity_name = True
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_icon = "mdi:led-on"

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
        """Return true if LED is on."""
        return self._device.led_status

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the LED."""
        await self._device.set_led(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the LED."""
        await self._device.set_led(False)
        await self.coordinator.async_request_refresh()


class KasaAutoUpdateSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of Kasa Auto Update Configuration."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_auto_update"
        self._attr_name = "Auto-update enabled"
        self._attr_has_entity_name = True
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_icon = "mdi:update"

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
        """Return true if auto update is enabled."""
        return False # Placeholder

    async def async_turn_on(self, **kwargs: Any) -> None:
        pass

    async def async_turn_off(self, **kwargs: Any) -> None:
        pass


class KasaMotionEnableSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of Kasa Motion Configuration."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.device_id}_motion_enable"
        self._attr_name = "Motion detected enabled"
        self._attr_has_entity_name = True
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_icon = "mdi:motion-sensor"

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
        """Return true if motion enabled."""
        return self._device.motion_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable motion detection."""
        await self._device.set_motion_detection(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable motion detection."""
        await self._device.set_motion_detection(False)
        await self.coordinator.async_request_refresh()
