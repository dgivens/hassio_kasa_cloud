"""The TP-Link Kasa Cloud integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud_api import KasaCloudAuthError, KasaCloudClient, KasaCloudError
from .const import CONF_TERMINAL_UUID, DOMAIN
from .coordinator import KasaDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.SENSOR,
    Platform.SWITCH,
]

KasaConfigEntry = ConfigEntry[KasaDataUpdateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: KasaConfigEntry) -> bool:
    """Set up TP-Link Kasa Cloud from a config entry."""
    # Reuse Home Assistant's shared session instead of building one per
    # request, so connections are pooled and closed with HA.
    client = KasaCloudClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        async_get_clientsession(hass),
        terminal_uuid=entry.data.get(CONF_TERMINAL_UUID),
    )

    # Persist the terminal UUID so the TP-Link account does not accumulate a
    # new registered "terminal" on every restart.
    if not entry.data.get(CONF_TERMINAL_UUID):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TERMINAL_UUID: client.terminal_uuid}
        )

    # Entries created by the upstream version have no unique_id, so the
    # duplicate check in the config flow would not match them and the same
    # account could be configured a second time.
    if entry.unique_id is None:
        hass.config_entries.async_update_entry(
            entry, unique_id=entry.data[CONF_USERNAME].strip().lower()
        )

    try:
        devices = await client.get_devices()
    except KasaCloudAuthError as err:
        # Triggers HA's re-auth card rather than silently dying.
        raise ConfigEntryAuthFailed(str(err)) from err
    except KasaCloudError as err:
        # Retryable: upstream returned False here, which HA never retries, so
        # a WAN blip during startup disabled the integration until a manual
        # reload.
        raise ConfigEntryNotReady(f"Cannot reach TP-Link cloud: {err}") from err

    # A record without a device id cannot be addressed or keyed on.
    devices = [device for device in devices if device.device_id]

    if not devices:
        raise ConfigEntryNotReady(
            "TP-Link cloud returned no usable devices for this account"
        )

    coordinator = KasaDataUpdateCoordinator(hass, entry, client, devices)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={coordinator.hub_id},
        name="Kasa Cloud",
        manufacturer="TP-Link",
        model="Kasa Cloud Account",
        entry_type=dr.DeviceEntryType.SERVICE,
    )

    # Register the physical devices up front. Platforms are set up
    # concurrently, so a platform that registers a child outlet with
    # `via_device` pointing at its strip may otherwise run before the platform
    # that would have created the strip, leaving the children unlinked.
    for device in coordinator.data.values():
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, str(device.device_id))},
            name=device.alias or None,
            model=device.model or None,
            manufacturer="TP-Link",
            via_device=coordinator.hub_id,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KasaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
