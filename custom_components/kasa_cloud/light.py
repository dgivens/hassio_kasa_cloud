"""Light platform for TP-Link Kasa Cloud bulbs and dimmers."""
from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KasaConfigEntry
from .cloud_api import KasaCloudError
from .entity import KasaCloudEntity

PARALLEL_UPDATES = 1

# Conservative defaults; Kasa tunable-white bulbs sit inside this range.
DEFAULT_MIN_KELVIN = 2500
DEFAULT_MAX_KELVIN = 6500


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: KasaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kasa light entities."""
    coordinator = config_entry.runtime_data
    async_add_entities(
        KasaLight(coordinator, device)
        for device in coordinator.data.values()
        # Colour capabilities come from sys_info, so skip un-polled devices
        # rather than freezing them into the wrong set of colour modes.
        if device.sys_info
        # A dimmable non-bulb is a wall dimmer; a strip outlet is never a light.
        and (device.is_bulb or (device.is_dimmable and not device.has_children))
    )


class KasaLight(KasaCloudEntity, LightEntity):
    """A Kasa bulb or wall dimmer."""

    _attr_name = None

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device)

        modes: set[ColorMode] = set()
        if device.is_color:
            modes.add(ColorMode.HS)
        if device.is_variable_color_temp:
            modes.add(ColorMode.COLOR_TEMP)
        if not modes:
            modes.add(
                ColorMode.BRIGHTNESS if device.is_dimmable else ColorMode.ONOFF
            )
        self._attr_supported_color_modes = modes

        if ColorMode.COLOR_TEMP in modes:
            self._attr_min_color_temp_kelvin = DEFAULT_MIN_KELVIN
            self._attr_max_color_temp_kelvin = DEFAULT_MAX_KELVIN

    @property
    def is_on(self) -> bool | None:
        return self._device.is_on

    @property
    def brightness(self) -> int | None:
        return self._device.brightness

    @property
    def color_temp_kelvin(self) -> int | None:
        """Kelvin, not mireds: the mired properties were removed in HA 2026.3."""
        return self._device.color_temp

    @property
    def hs_color(self) -> tuple[float, float] | None:
        hsv = self._device.hsv
        if hsv is None:
            return None
        return float(hsv[0]), float(hsv[1])

    @property
    def color_mode(self) -> ColorMode | None:
        modes = self._attr_supported_color_modes or set()
        hsv = self._device.hsv
        if ColorMode.HS in modes and hsv and hsv[1] > 0:
            return ColorMode.HS
        if ColorMode.COLOR_TEMP in modes and self._device.color_temp:
            return ColorMode.COLOR_TEMP
        # Fixed precedence, not set-iteration order, which is not stable
        # between processes and would flip the rendered attribute on restart.
        for mode in (
            ColorMode.COLOR_TEMP,
            ColorMode.HS,
            ColorMode.BRIGHTNESS,
            ColorMode.ONOFF,
        ):
            if mode in modes:
                return mode
        return None

    async def _async_run(self, action: Coroutine[Any, Any, None]) -> None:
        try:
            await action
        except KasaCloudError as err:
            raise HomeAssistantError(
                f"Failed to control {self._device.alias or self._device.device_id}: {err}"
            ) from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, applying only the attributes actually requested."""
        if ATTR_HS_COLOR in kwargs:
            hue, saturation = kwargs[ATTR_HS_COLOR]
            brightness = kwargs.get(ATTR_BRIGHTNESS)
            if brightness is not None:
                value = round(brightness * 100 / 255)
            else:
                current = self._device.hsv
                value = current[2] if current else 100
            await self._async_run(self._device.set_hsv(int(hue), int(saturation), value))
        elif ATTR_COLOR_TEMP_KELVIN in kwargs:
            await self._async_run(
                self._device.set_color_temp(int(kwargs[ATTR_COLOR_TEMP_KELVIN]))
            )
            if ATTR_BRIGHTNESS in kwargs:
                await self._async_run(
                    self._device.set_brightness(kwargs[ATTR_BRIGHTNESS])
                )
        elif ATTR_BRIGHTNESS in kwargs:
            # Passed through on HA's 0-255 scale; set_brightness converts once.
            await self._async_run(self._device.set_brightness(kwargs[ATTR_BRIGHTNESS]))
        else:
            await self._async_run(self._device.turn_on())

        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_run(self._device.turn_off())
        await self.coordinator.async_request_refresh()
