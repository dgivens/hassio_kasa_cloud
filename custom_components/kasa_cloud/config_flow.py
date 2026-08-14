"""Config flow for the TP-Link Kasa Cloud integration."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud_api import KasaCloudAuthError, KasaCloudClient, KasaCloudError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class KasaCloudConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TP-Link Kasa Cloud."""

    VERSION = 1

    async def _async_check_credentials(self, username: str, password: str) -> str | None:
        """Return an error key, or ``None`` when the credentials work.

        Upstream reported every failure as "cannot_connect", so a typo'd
        password looked like a network problem and every retry was another
        real login attempt against TP-Link.
        """
        client = KasaCloudClient(username, password, async_get_clientsession(self.hass))
        try:
            devices = await client.get_devices()
        except KasaCloudAuthError:
            return "invalid_auth"
        except KasaCloudError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001 - unexpected, but must not leak a traceback
            _LOGGER.exception("Unexpected error validating TP-Link cloud credentials")
            return "unknown"
        if not devices:
            return "no_devices_found"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            # Deliberately outside the error handling below: the AbortFlow this
            # raises must propagate, not be reported as a connection error.
            await self.async_set_unique_id(username.strip().lower())
            self._abort_if_unique_id_configured()

            error = await self._async_check_credentials(
                username, user_input[CONF_PASSWORD]
            )
            if error is None:
                return self.async_create_entry(
                    title=f"Kasa Cloud ({username})", data=user_input
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication after the cloud rejects our credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for a new password."""
        errors: dict[str, str] = {}
        # Not `assert`: assertions are stripped under `python -O`.
        entry = self._get_reauth_entry()
        if entry is None:
            return self.async_abort(reason="reauth_failed")

        if user_input is not None:
            error = await self._async_check_credentials(
                entry.data[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry,
                    data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )
