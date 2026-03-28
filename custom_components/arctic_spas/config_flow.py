"""Config flow for Arctic Spas integration."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArcticSpaApiError, ArcticSpaAuthError, ArcticSpaClient
from .const import (
    CONF_API_KEY,
    CONF_LOCAL_HOST,
    CONF_LOCAL_PORT,
    CONF_MODE,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_USERNAME,
    DEFAULT_LOCAL_PORT,
    DOMAIN,
    ConnectionMode,
)

_LOGGER = logging.getLogger(__name__)

_AUTH_BASE_URL = "https://myarcticspa.com"
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=10)
_TCP_CONNECT_TIMEOUT = 5.0

STEP_MODE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MODE, default=ConnectionMode.REST): vol.In(
            [ConnectionMode.REST, ConnectionMode.MQTT, ConnectionMode.LOCAL]
        )
    }
)

STEP_REST_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})

STEP_MQTT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MQTT_USERNAME): str,
        vol.Required(CONF_MQTT_PASSWORD): str,
    }
)

STEP_LOCAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LOCAL_HOST): str,
        vol.Required(CONF_LOCAL_PORT, default=DEFAULT_LOCAL_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
    }
)



class ArcticSpaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Arctic Spas."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: choose connection mode (rest, mqtt, or local)."""
        if user_input is not None:
            mode = user_input[CONF_MODE]
            if mode == ConnectionMode.MQTT:
                return await self.async_step_mqtt()
            if mode == ConnectionMode.LOCAL:
                return await self.async_step_local()
            return await self.async_step_rest()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_MODE_SCHEMA,
            errors={},
        )

    async def async_step_rest(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2a: REST mode — validate API key against the My Arctic Spa API."""
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
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during REST validation")
                errors["base"] = "unknown"
            else:
                unique_id = (
                    status.get("serialNumber")
                    or status.get("deviceId")
                    or hashlib.sha256(api_key.encode()).hexdigest()[:16]
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title="Arctic Spa (REST)",
                    data={
                        CONF_MODE: ConnectionMode.REST,
                        CONF_API_KEY: api_key,
                    },
                )

        return self.async_show_form(
            step_id="rest",
            data_schema=STEP_REST_SCHEMA,
            errors=errors,
            description_placeholders={
                "api_key_url": "https://myarcticspa.com/spa/SpaAPIManagement.aspx"
            },
        )

    async def async_step_mqtt(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2b: MQTT mode — validate credentials with 2-round auth flow."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_MQTT_USERNAME].strip()
            password = user_input[CONF_MQTT_PASSWORD].strip()

            try:
                spa_id = await self._validate_mqtt_credentials(username, password)
            except _MqttAuthError:
                errors["base"] = "invalid_auth"
            except _MqttConnectError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during MQTT validation")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(spa_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title="Arctic Spa (MQTT)",
                    data={
                        CONF_MODE: ConnectionMode.MQTT,
                        CONF_MQTT_USERNAME: username,
                        CONF_MQTT_PASSWORD: password,
                    },
                )

        return self.async_show_form(
            step_id="mqtt",
            data_schema=STEP_MQTT_SCHEMA,
            errors=errors,
        )

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2c: Local mode — validate by opening a TCP connection to port 12121."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_LOCAL_HOST].strip()
            port = int(user_input[CONF_LOCAL_PORT])

            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port, limit=1024),
                    timeout=_TCP_CONNECT_TIMEOUT,
                )
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
                except Exception:  # noqa: BLE001
                    pass
            except ConnectionRefusedError:
                errors["base"] = "port_not_open"
            except (TimeoutError, asyncio.TimeoutError, OSError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during local TCP validation")
                errors["base"] = "unknown"
            else:
                unique_id = f"{host}:{port}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title="Arctic Spa (Local)",
                    data={
                        CONF_MODE: ConnectionMode.LOCAL,
                        CONF_LOCAL_HOST: host,
                        CONF_LOCAL_PORT: port,
                    },
                )

        return self.async_show_form(
            step_id="local",
            data_schema=STEP_LOCAL_SCHEMA,
            errors=errors,
        )

    async def _validate_mqtt_credentials(self, username: str, password: str) -> str:
        """Run two-round MQTT auth flow and return the spa ID on success.

        Returns the Spas[0].Id string, which becomes the unique_id for the entry.
        Raises _MqttAuthError on bad credentials, _MqttConnectError on network failure.
        """
        session = async_get_clientsession(self.hass)

        # Round 1 — request salt
        try:
            async with session.post(
                f"{_AUTH_BASE_URL}/api/auth",
                json={"username": username, "hash": None, "AllowNoSpaLogin": True},
                timeout=_HTTP_TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise _MqttAuthError("Credentials rejected in round 1")
                if resp.status != 200:
                    raise _MqttConnectError(f"Unexpected status {resp.status} in round 1")
                try:
                    body = await resp.json()
                except (json.JSONDecodeError, aiohttp.ContentTypeError) as err:
                    raise _MqttConnectError(f"Invalid response in round 1: {err}") from err
        except aiohttp.ClientError as err:
            raise _MqttConnectError(f"Network error in round 1: {err}") from err

        salt = body.get("Salt") or body.get("salt")
        if not salt:
            raise _MqttAuthError("No salt in round-1 response")

        # Hash password — SHA-1(base64_decode(salt) + password.utf-16-le)
        try:
            salt_bytes = base64.b64decode(salt)
        except Exception as err:  # noqa: BLE001
            raise _MqttAuthError(f"Cannot decode salt: {err}") from err

        pw_bytes = password.encode("utf-16-le")
        pw_hash = base64.b64encode(
            hashlib.sha1(salt_bytes + pw_bytes).digest()  # noqa: S324
        ).decode()

        # Round 2 — authenticate
        try:
            async with session.post(
                f"{_AUTH_BASE_URL}/api/auth",
                json={"username": username, "hash": pw_hash, "AllowNoSpaLogin": True},
                timeout=_HTTP_TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise _MqttAuthError("Credentials rejected in round 2")
                if resp.status != 200:
                    raise _MqttConnectError(f"Unexpected status {resp.status} in round 2")
                try:
                    body = await resp.json()
                except (json.JSONDecodeError, aiohttp.ContentTypeError) as err:
                    raise _MqttConnectError(f"Invalid response in round 2: {err}") from err
        except aiohttp.ClientError as err:
            raise _MqttConnectError(f"Network error in round 2: {err}") from err

        spas = body.get("Spas") or body.get("spas")
        if not spas:
            raise _MqttAuthError("No spas returned — credentials may be invalid")

        spa_id = spas[0].get("Id") or spas[0].get("id")
        if not spa_id:
            raise _MqttAuthError("Spa ID missing from round-2 response")

        return str(spa_id)


class _MqttAuthError(Exception):
    """MQTT credentials were rejected."""


class _MqttConnectError(Exception):
    """Could not reach the MQTT auth endpoint."""
