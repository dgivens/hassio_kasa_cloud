"""Data update coordinator for the TP-Link Kasa Cloud integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cloud_api import (
    KasaCloudAuthError,
    KasaCloudClient,
    KasaCloudDevice,
    KasaCloudError,
)
from .const import (
    DEVICE_LIST_REFRESH_INTERVAL,
    DOMAIN,
    EMETER_REFRESH_INTERVAL,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class KasaDataUpdateCoordinator(DataUpdateCoordinator[dict[str, KasaCloudDevice]]):
    """Poll the Kasa cloud for device state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: KasaCloudClient,
        devices: list[KasaCloudDevice],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
            config_entry=entry,
        )
        self.client = client
        self.devices = devices
        # Wall-clock based, not cycle-counted: every command triggers an
        # immediate extra refresh, so counting cycles drifts badly.
        self._device_list_at = monotonic()
        self._emeter_at = 0.0

    async def _async_update_data(self) -> dict[str, KasaCloudDevice]:
        """Refresh every device, tolerating individual failures."""
        now = monotonic()

        if now - self._device_list_at >= DEVICE_LIST_REFRESH_INTERVAL:
            self._device_list_at = now
            await self._refresh_device_records()

        # Energy is polled far less often than state. On a 6-outlet HS300 the
        # emeter costs one cloud call per outlet, so folding it into every
        # state poll would be 7 calls a minute against a throttled API.
        include_emeter = now - self._emeter_at >= EMETER_REFRESH_INTERVAL
        if include_emeter:
            self._emeter_at = now

        results = await asyncio.gather(
            *(device.update(include_emeter=include_emeter) for device in self.devices),
            return_exceptions=True,
        )

        # A rejected credential must reach HA as a re-auth prompt, not as a
        # generic update failure that retries the same bad password forever.
        for result in results:
            if isinstance(result, KasaCloudAuthError):
                raise ConfigEntryAuthFailed(str(result)) from result

        failures: list[BaseException] = []
        for device, result in zip(self.devices, results):
            if isinstance(result, asyncio.CancelledError):
                # Shutdown, not a device problem.
                raise result
            if isinstance(result, BaseException):
                failures.append(result)
                _LOGGER.debug(
                    "Kasa cloud update failed for %s: %s", device.device_id, result
                )

        # Anything that is not a known cloud error is a bug in this
        # integration; surface it instead of hiding it behind UpdateFailed.
        for failure in failures:
            if not isinstance(failure, KasaCloudError):
                raise failure

        if failures and len(failures) == len(self.devices):
            raise UpdateFailed(
                f"All {len(failures)} device(s) failed to update: {failures[0]}"
            )

        return {device.device_id: device for device in self.devices}

    async def _refresh_device_records(self) -> None:
        """Re-read the device list.

        It is the only source of cloud reachability. Upstream fetched it once
        at setup, so the "Cloud connection" sensor was frozen for the lifetime
        of the Home Assistant process.
        """
        try:
            records = await self.client.fetch_device_records()
        except KasaCloudAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except KasaCloudError as err:
            # Not fatal: fall through to the per-device poll.
            _LOGGER.debug("Could not refresh Kasa device list: %s", err)
            return

        # Only replace a record with a well-formed one for the same device: the
        # payload is remote input, and device_info feeds entity properties.
        by_id = {
            record["deviceId"]: record
            for record in records
            if isinstance(record, dict) and isinstance(record.get("deviceId"), str)
        }
        for device in self.devices:
            record = by_id.get(device.device_id)
            if record is not None:
                device.device_info = record
