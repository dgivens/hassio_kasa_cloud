"""Button platform for TP-Link Kasa Cloud."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KasaConfigEntry
from .cloud_api import KasaCloudError
from .entity import KasaCloudEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: KasaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kasa button entities."""
    coordinator = config_entry.runtime_data
    async_add_entities(
        KasaRebootButton(coordinator, device) for device in coordinator.data.values()
    )


class KasaRebootButton(KasaCloudEntity, ButtonEntity):
    """Reboot the device.

    Disabled by default: on a strip this power-cycles every outlet, which is
    not something to expose one mis-click away on remote hardware.
    """

    _attr_name = "Reboot"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator, device, key="reboot")

    async def async_press(self) -> None:
        try:
            await self._device.reboot()
        except KasaCloudError as err:
            raise HomeAssistantError(
                f"Failed to reboot {self._device.alias or self._device.device_id}: {err}"
            ) from err
