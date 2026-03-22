"""Config flow for Arctic Spa integration."""
from __future__ import annotations

import hashlib
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArcticSpaApiError, ArcticSpaAuthError, ArcticSpaClient
from .const import CONF_API_KEY, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_API_KEY): str}
)


class ArcticSpaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Arctic Spa."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            session = async_get_clientsession(self.hass)
            client = ArcticSpaClient(api_key, session)

            try:
                status = await client.get_status()
            except ArcticSpaAuthError:
                errors[CONF_API_KEY] = "invalid_auth"
            except ArcticSpaApiError:
                errors["base"] = "cannot_connect"
            else:
                # Use spa serial / device ID as unique ID when available,
                # otherwise hash the API key so duplicate entries are prevented.
                unique_id = (
                    status.get("serialNumber")
                    or status.get("deviceId")
                    or hashlib.sha256(api_key.encode()).hexdigest()[:16]
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title="Arctic Spa",
                    data={CONF_API_KEY: api_key},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
