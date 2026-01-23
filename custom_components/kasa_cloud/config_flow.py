"""Config flow for TP-Link Kasa Cloud integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .cloud_api import KasaCloudClient

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
    }
)


class KasaCloudConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TP-Link Kasa Cloud."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            # Test credentials by retrieving devices from cloud
            try:
                client = KasaCloudClient(username, password)
                devices = await client.get_devices()

                _LOGGER.info("Received devices from cloud: type=%s, value=%s",
                           type(devices).__name__, devices)

                if not devices or len(devices) == 0:
                    _LOGGER.warning("No devices found for account %s. Devices value: %s", username, devices)
                    errors["base"] = "no_devices_found"
                else:
                    _LOGGER.info("Found %d devices for account %s", len(devices), username)
                    # Create a unique ID for this config entry
                    await self.async_set_unique_id(username)
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=f"Kasa Cloud ({username})",
                        data=user_input,
                    )

            except Exception as err:
                _LOGGER.exception("Failed to authenticate with TP-Link cloud: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
