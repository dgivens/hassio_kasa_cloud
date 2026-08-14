"""Switch platform for TP-Link Kasa Cloud plugs, wall switches and strips."""
from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KasaConfigEntry
from .cloud_api import KasaCloudError
from .entity import KasaCloudEntity

# Serialise writes: this is a rate-limited cloud API.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: KasaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kasa switch entities."""
    coordinator = config_entry.runtime_data

    entities: list[SwitchEntity] = []
    for device in coordinator.data.values():
        if not device.sys_info:
            # Capabilities are read from sys_info, so a device whose first poll
            # failed would be mis-modelled (an HS300 would lose all six outlets
            # and gain a bogus parent switch). It appears after a reload.
            continue
        if device.is_bulb:
            continue  # the light platform owns bulbs

        if device.has_children:
            # A power strip: one switch per outlet. There is no master relay
            # in hardware, so no synthetic "all outlets" switch is offered.
            entities.extend(
                KasaSwitch(coordinator, child, parent=device)
                for child in device.children
            )
        elif not device.is_dimmable:
            entities.append(KasaSwitch(coordinator, device))

        # Only offer the LED toggle when the device actually reports it.
        if device.led_status is not None:
            entities.append(KasaLedSwitch(coordinator, device))

    async_add_entities(entities)


class KasaSwitchBase(KasaCloudEntity, SwitchEntity):
    """Shared command handling so a failed write is never a silent success."""

    async def _async_run(self, action: Coroutine[Any, Any, None]) -> None:
        try:
            await action
        except KasaCloudError as err:
            raise HomeAssistantError(
                f"Failed to control {self._device.alias or self._device.device_id}: {err}"
            ) from err
        await self.coordinator.async_request_refresh()


class KasaSwitch(KasaSwitchBase):
    """A Kasa plug, wall switch, or a single outlet on a strip."""

    _attr_name = None  # take the device's own name

    @property
    def is_on(self) -> bool | None:
        return self._device.is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_run(self._device.turn_on())

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_run(self._device.turn_off())


class KasaLedSwitch(KasaSwitchBase):
    """The status LED on the device itself."""

    _attr_name = "LED"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:led-on"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device, key="led")

    @property
    def is_on(self) -> bool | None:
        return self._device.led_status

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_run(self._device.set_led(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_run(self._device.set_led(False))
