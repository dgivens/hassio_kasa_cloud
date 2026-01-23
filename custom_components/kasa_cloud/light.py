"""Support for TP-Link Kasa smart bulbs."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.color import (
    color_temperature_kelvin_to_mired,
    color_temperature_mired_to_kelvin,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kasa light devices."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []
    for device in coordinator.data.values():
        if device.is_bulb or device.is_light_strip or (device.is_dimmable and not device.is_plug):
            entities.append(KasaLight(coordinator, device))

    async_add_entities(entities)


class KasaLight(CoordinatorEntity, LightEntity):
    """Representation of a Kasa Smart Light."""

    def __init__(self, coordinator, device) -> None:
        """Initialize the light."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = device.device_id or device.host
        self._attr_name = None # Use device name
        self._attr_has_entity_name = True

        # Determine supported color modes
        self._attr_supported_color_modes = set()

        if device.is_variable_color_temp:
            self._attr_supported_color_modes.add(ColorMode.COLOR_TEMP)

        if device.is_color:
            self._attr_supported_color_modes.add(ColorMode.HS)

        if device.is_dimmable and not self._attr_supported_color_modes:
            self._attr_supported_color_modes.add(ColorMode.BRIGHTNESS)

        if not self._attr_supported_color_modes:
            self._attr_supported_color_modes.add(ColorMode.ONOFF)

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
        """Return true if light is on."""
        return self._device.is_on

    @property
    def brightness(self) -> int | None:
        """Return the brightness of this light between 0..255."""
        if self._device.is_dimmable:
            return self._device.brightness
        return None

    @property
    def color_temp(self) -> int | None:
        """Return the color temperature in mireds."""
        if self._device.is_variable_color_temp:
            return color_temperature_kelvin_to_mired(self._device.color_temp)
        return None

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Return the hue and saturation color value [float, float]."""
        if self._device.is_color:
            hue, saturation, _ = self._device.hsv
            return hue, saturation
        return None

    @property
    def color_mode(self) -> ColorMode | None:
        """Return the active color mode."""
        if self._device.is_color:
            return ColorMode.HS
        if self._device.is_variable_color_temp:
            return ColorMode.COLOR_TEMP
        if self._device.is_dimmable:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        if ATTR_BRIGHTNESS in kwargs:
            brightness_pct = int(kwargs[ATTR_BRIGHTNESS] / 2.55)
            await self._device.set_brightness(brightness_pct)

        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            await self._device.set_color_temp(int(kwargs[ATTR_COLOR_TEMP_KELVIN]))

        if ATTR_HS_COLOR in kwargs:
            hue, saturation = kwargs[ATTR_HS_COLOR]
            # Keep current brightness
            brightness = self._device.hsv[2] if hasattr(self._device, "hsv") else 100
            await self._device.set_hsv(int(hue), int(saturation), brightness)

        await self._device.turn_on()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        await self._device.turn_off()
        await self.coordinator.async_request_refresh()
