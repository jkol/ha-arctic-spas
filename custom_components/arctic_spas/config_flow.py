"""Config flow for Arctic Spas integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_CLOUD_DEALERSHIP_ID,
    CONF_CLOUD_EMAIL,
    CONF_CLOUD_PASSWORD,
    CONF_CLOUD_SPA_ID,
    CONF_LOCAL_HOST,
    CONF_LOCAL_MAC,
    CONF_MODE,
    DOMAIN,
    ConnectionMode,
)

_LOGGER = logging.getLogger(__name__)

_WS_CONNECT_TIMEOUT = 5.0

STEP_MODE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MODE, default=ConnectionMode.DIRECT): vol.In(
            [ConnectionMode.DIRECT, ConnectionMode.CLOUD]
        )
    }
)

STEP_DIRECT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LOCAL_HOST): str,
    }
)

STEP_CLOUD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLOUD_EMAIL): str,
        vol.Required(CONF_CLOUD_PASSWORD): str,
    }
)


class ArcticSpaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Arctic Spas."""

    VERSION = 3

    def __init__(self) -> None:
        super().__init__()
        self._cloud_token: str | None = None
        self._cloud_spas: list[dict[str, Any]] = []
        self._cloud_email: str = ""
        self._cloud_password: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: choose connection mode (Direct or Cloud)."""
        if user_input is not None:
            mode = user_input[CONF_MODE]
            if mode == ConnectionMode.CLOUD:
                return await self.async_step_cloud()
            return await self.async_step_direct()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_MODE_SCHEMA,
            errors={},
        )

    # ── Direct mode ──────────────────────────────────────────────────────────

    async def async_step_direct(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Direct mode — validate by connecting to ws://<host>:8765."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_LOCAL_HOST].strip()

            error = await self._validate_ws(host)
            if error:
                errors["base"] = error

            if not errors:
                unique_id = f"{host}:8765"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                mac = await self._resolve_mac(host)

                entry_data: dict[str, Any] = {
                    CONF_MODE: ConnectionMode.DIRECT,
                    CONF_LOCAL_HOST: host,
                }
                if mac:
                    entry_data[CONF_LOCAL_MAC] = mac

                return self.async_create_entry(
                    title="Arctic Spa (Direct)",
                    data=entry_data,
                )

        return self.async_show_form(
            step_id="direct",
            data_schema=STEP_DIRECT_SCHEMA,
            errors=errors,
        )

    # ── Cloud mode ───────────────────────────────────────────────────────────

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Cloud mode — authenticate with dealer API."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_CLOUD_EMAIL].strip()
            password = user_input[CONF_CLOUD_PASSWORD].strip()

            try:
                from .cloud_client import (  # noqa: PLC0415
                    ArcticSpaCloudAuthError,
                    async_dealer_login,
                    async_get_spas,
                )

                token = await async_dealer_login(email, password)
                spas = await async_get_spas(token)
            except ArcticSpaCloudAuthError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Cloud login")
                errors["base"] = "cannot_connect"
            else:
                if not spas:
                    errors["base"] = "no_spas"
                else:
                    self._cloud_token = token
                    self._cloud_spas = spas
                    self._cloud_email = email
                    self._cloud_password = password

                    if len(spas) == 1:
                        return await self._create_cloud_entry(spas[0])
                    return await self.async_step_cloud_select_spa()

        return self.async_show_form(
            step_id="cloud",
            data_schema=STEP_CLOUD_SCHEMA,
            errors=errors,
        )

    async def async_step_cloud_select_spa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which spa to add (when the account has multiple)."""
        if user_input is not None:
            spa_id = user_input["spa_id"]
            spa = next((s for s in self._cloud_spas if s["id"] == spa_id), None)
            if spa:
                return await self._create_cloud_entry(spa)

        spa_options = {
            s["id"]: s.get("nick_name") or s.get("arctic_serial_number") or s["id"]
            for s in self._cloud_spas
        }
        return self.async_show_form(
            step_id="cloud_select_spa",
            data_schema=vol.Schema(
                {vol.Required("spa_id"): vol.In(spa_options)}
            ),
        )

    async def _create_cloud_entry(self, spa: dict[str, Any]) -> ConfigFlowResult:
        spa_id = spa["id"]
        dealership_id = str(spa.get("dealership_id", spa.get("dealership", {}).get("id", "")))
        nick_name = spa.get("nick_name") or "Arctic Spa"

        await self.async_set_unique_id(spa_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=nick_name,
            data={
                CONF_MODE: ConnectionMode.CLOUD,
                CONF_CLOUD_EMAIL: self._cloud_email,
                CONF_CLOUD_PASSWORD: self._cloud_password,
                CONF_CLOUD_SPA_ID: spa_id,
                CONF_CLOUD_DEALERSHIP_ID: dealership_id,
            },
        )

    # ── Reconfigure ──────────────────────────────────────────────────────────

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None
        mode = entry.data.get(CONF_MODE)

        if mode == ConnectionMode.CLOUD:
            return await self.async_step_reconfigure_cloud(user_input)
        return await self.async_step_reconfigure_direct(user_input)

    async def async_step_reconfigure_direct(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_LOCAL_HOST].strip()
            error = await self._validate_ws(host)
            if error:
                errors["base"] = error
            else:
                mac = await self._resolve_mac(host)
                new_data: dict[str, Any] = {**entry.data, CONF_LOCAL_HOST: host}
                if mac:
                    new_data[CONF_LOCAL_MAC] = mac
                return self.async_update_reload_and_abort(entry, data=new_data)

        return self.async_show_form(
            step_id="reconfigure_direct",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LOCAL_HOST,
                        default=entry.data.get(CONF_LOCAL_HOST, ""),
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_CLOUD_EMAIL].strip()
            password = user_input[CONF_CLOUD_PASSWORD].strip()

            try:
                from .cloud_client import (  # noqa: PLC0415
                    ArcticSpaCloudAuthError,
                    async_dealer_login,
                )

                await async_dealer_login(email, password)
            except ArcticSpaCloudAuthError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Cloud reconfigure")
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_CLOUD_EMAIL: email,
                        CONF_CLOUD_PASSWORD: password,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure_cloud",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CLOUD_EMAIL,
                        default=entry.data.get(CONF_CLOUD_EMAIL, ""),
                    ): str,
                    vol.Required(CONF_CLOUD_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    async def _validate_ws(self, host: str) -> str | None:
        try:
            session = async_get_clientsession(self.hass)
            ws = await asyncio.wait_for(
                session.ws_connect(
                    f"ws://{host}:8765",
                    origin=f"http://{host}",
                ),
                timeout=_WS_CONNECT_TIMEOUT,
            )
            await ws.send_json({"query": 0})
            msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
            await ws.close()
            if msg.type != aiohttp.WSMsgType.TEXT:
                return "cannot_connect"
        except (TimeoutError, asyncio.TimeoutError, OSError):
            return "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during WebSocket validation")
            return "unknown"
        return None

    async def _resolve_mac(self, host: str) -> str | None:
        from .websocket_client import async_resolve_mac  # noqa: PLC0415

        try:
            mac = await async_resolve_mac(host)
        except Exception:  # noqa: BLE001
            mac = None
        if mac:
            _LOGGER.debug("Resolved MAC %s for %s", mac, host)
        else:
            _LOGGER.debug("Could not resolve MAC for %s", host)
        return mac
