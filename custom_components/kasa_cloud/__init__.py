"""The TP-Link Kasa Cloud integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD, UPDATE_INTERVAL
from .cloud_api import KasaCloudClient, KasaCloudDevice

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.LIGHT, Platform.SWITCH, Platform.SENSOR, Platform.BUTTON, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TP-Link Kasa Cloud from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    # Get devices from TP-Link cloud
    try:
        # Get devices from cloud
        client = KasaCloudClient(username, password)
        cloud_devices = await client.get_devices()
    except Exception as err:
        _LOGGER.error("Failed to get devices from TP-Link cloud: %s", err)
        return False

    if not cloud_devices:
        _LOGGER.warning("No Kasa devices found in cloud account")
        return False

    _LOGGER.info("Found %d device(s) in cloud account", len(cloud_devices))

    # Create coordinator for updates
    coordinator = KasaDataUpdateCoordinator(
        hass,
        devices=cloud_devices,
        username=username,
        password=password,
        entry_id=entry.entry_id,
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Register hub device
    device_registry = dr.async_get(hass)
    hub_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, username)},
        name=f"Kasa Cloud ({username})",
        manufacturer="TP-Link",
        model="Kasa Cloud Account",
        entry_type=dr.DeviceEntryType.SERVICE,
    )

    # Setup platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class KasaDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Kasa data."""

    def __init__(
        self,
        hass: HomeAssistant,
        devices: list[KasaCloudDevice],
        username: str,
        password: str,
        entry_id: str,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.devices = devices
        self.username = username
        self.password = password
        self.entry_id = entry_id
        self.hub_id = (DOMAIN, username)

    async def _async_update_data(self) -> dict[str, KasaCloudDevice]:
        """Update data via library."""
        try:
            # Update all devices
            await asyncio.gather(
                *[device.update() for device in self.devices],
                return_exceptions=False,
            )

            # Return devices indexed by device_id (using host property for compat)
            return {device.host: device for device in self.devices}
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
