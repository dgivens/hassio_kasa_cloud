"""Shared entity base for the TP-Link Kasa Cloud integration.

Upstream repeated the same ``device_info`` dict in eight places across five
platform files, with three different variants of the ``sw_version`` guard.
Centralising it removes that drift and gives every entity consistent
availability semantics.
"""
from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud_api import KasaCloudChildDevice, KasaCloudDevice
from .const import DOMAIN
from .coordinator import KasaDataUpdateCoordinator


class KasaCloudEntity(CoordinatorEntity[KasaDataUpdateCoordinator]):
    """Base class for every Kasa cloud entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: KasaDataUpdateCoordinator,
        device: KasaCloudDevice | KasaCloudChildDevice,
        *,
        key: str | None = None,
        parent: KasaCloudDevice | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self._parent = parent
        # State always originates from the top-level device's sys_info; a
        # child outlet has no poll of its own.
        self._root: KasaCloudDevice = parent if parent is not None else device
        self._attr_unique_id = f"{device.device_id}_{key}" if key else device.device_id

    @property
    def device_info(self) -> DeviceInfo:
        """Describe this device, linking child outlets to their strip."""
        info = DeviceInfo(
            identifiers={(DOMAIN, str(self._device.device_id))},
            manufacturer="TP-Link",
            name=self._device.alias or None,
            model=self._device.model or None,
        )

        hw_info = self._device.hw_info or {}
        if hw_info.get("sw_ver"):
            info["sw_version"] = str(hw_info["sw_ver"])
        if hw_info.get("hw_ver"):
            info["hw_version"] = str(hw_info["hw_ver"])

        if self._parent is not None:
            info["via_device"] = (DOMAIN, str(self._parent.device_id))
        else:
            # Only the physical device carries the MAC; reusing it for child
            # outlets would collapse them into a single device registry entry.
            # Type-checked, not just presence-checked: this comes from a cloud
            # response, and format_mac raises on a non-string.
            mac = getattr(self._device, "mac", None)
            if isinstance(mac, str) and mac:
                info["connections"] = {(dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac))}
            info["via_device"] = self.coordinator.hub_id

        return info

    @property
    def available(self) -> bool:
        """Available only when the last poll genuinely produced state.

        Upstream swallowed every error, so entities stayed available forever
        and reported stale values as if they were live.
        """
        if not self.coordinator.last_update_success:
            return False
        if self._device.is_connected is False:
            return False
        return bool(self._root.sys_info)
