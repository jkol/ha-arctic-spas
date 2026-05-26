"""Arctic Spa Home Assistant integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArcticSpaApiError, ArcticSpaClient
from .const import (
    CONF_API_KEY,
    CONF_LOCAL_HOST,
    CONF_LOCAL_MAC,
    CONF_MODE,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_USERNAME,
    ConnectionMode,
    DOMAIN,
)
from .coordinator import ArcticSpaCoordinator
from .spa_data import resolve_mqtt_capabilities, resolve_rest_capabilities

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.NUMBER,
]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entries from v1 to v2.

    Handles three v1 shapes:
      1. Only api_key present (original cloud-only flow)
      2. connection_mode="cloud" (two-step v1 flow)
      3. connection_mode="local" + host (two-step v1 flow local branch)
    """
    # Import legacy constants here — migration only, not used elsewhere.
    from .const import (  # noqa: PLC0415
        CONF_CONNECTION_MODE,
        CONF_HOST,
        CONNECTION_MODE_LOCAL,
    )

    if entry.version != 1:
        return True

    _LOGGER.info(
        "Migrating Arctic Spa config entry %s from version 1 to 2", entry.entry_id
    )

    old_data = dict(entry.data)
    new_data: dict[str, Any]

    connection_mode = old_data.get(CONF_CONNECTION_MODE)

    if connection_mode == CONNECTION_MODE_LOCAL or (
        connection_mode is None and CONF_HOST in old_data
    ):
        # v1 local entry — rename host → local_host, remove connection_mode
        host = old_data.get(CONF_HOST, "")
        new_data = {
            CONF_MODE: ConnectionMode.LOCAL,
            CONF_LOCAL_HOST: host,
        }
    else:
        # v1 cloud/REST entry — keep api_key, remove old connection_mode key
        new_data = {
            CONF_MODE: ConnectionMode.REST,
            CONF_API_KEY: old_data.get(CONF_API_KEY, ""),
        }

    hass.config_entries.async_update_entry(entry, data=new_data, version=2)
    _LOGGER.info(
        "Migration of Arctic Spa config entry %s complete (mode=%s)",
        entry.entry_id,
        new_data[CONF_MODE],
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Arctic Spa from a config entry."""
    mode = entry.data.get(CONF_MODE, ConnectionMode.REST)

    if mode == ConnectionMode.MQTT:
        from .mqtt_client import ArcticSpaMqttClient  # noqa: PLC0415

        client: Any = ArcticSpaMqttClient(
            hass,
            entry.data[CONF_MQTT_USERNAME],
            entry.data[CONF_MQTT_PASSWORD],
        )
        coordinator = ArcticSpaCoordinator(hass, client, push_mode=True)

        def _on_mqtt_update(data: dict[str, Any]) -> None:
            coordinator.async_set_updated_data(data)

        try:
            await client.start(_on_mqtt_update)
            await coordinator.async_config_entry_first_refresh()
            await client.wait_for_config(timeout=5.0)
            await client.wait_for_filters(timeout=3.0)
        except (ArcticSpaApiError, OSError, asyncio.TimeoutError) as err:
            raise ConfigEntryNotReady(str(err)) from err

        coordinator.capabilities = resolve_mqtt_capabilities(
            client.get_config(), client.get_info()
        )
        _LOGGER.debug(
            "MQTT capabilities resolved (config/spa received: %s)",
            bool(client.get_config()),
        )

    elif mode == ConnectionMode.LOCAL:
        from .websocket_client import ArcticSpaWebSocketClient, resolve_ws_capabilities  # noqa: PLC0415

        host = entry.data[CONF_LOCAL_HOST]
        mac = entry.data.get(CONF_LOCAL_MAC)

        def _on_host_changed(new_host: str) -> None:
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, CONF_LOCAL_HOST: new_host},
            )
            _LOGGER.info("Persisted new spa IP %s to config entry", new_host)

        client = ArcticSpaWebSocketClient(
            host, mac=mac, on_host_changed=_on_host_changed,
        )
        coordinator = ArcticSpaCoordinator(hass, client, push_mode=True)

        def _on_local_update(data: dict[str, Any]) -> None:
            coordinator.async_set_updated_data(data)

        try:
            await client.start(_on_local_update)
            await coordinator.async_config_entry_first_refresh()
            await client.wait_for_config(timeout=5.0)
        except (ArcticSpaApiError, OSError, asyncio.TimeoutError) as err:
            raise ConfigEntryNotReady(str(err)) from err

        coordinator.capabilities = resolve_ws_capabilities(
            client.get_settings(), coordinator.data or {},
        )
        _LOGGER.debug(
            "Local capabilities resolved via WebSocket (LPC %s, YOC %s)",
            coordinator.capabilities.firmware_lpc,
            coordinator.capabilities.firmware_yocto,
        )

    else:  # ConnectionMode.REST (default)
        session = async_get_clientsession(hass)
        client = ArcticSpaClient(entry.data[CONF_API_KEY], session)
        coordinator = ArcticSpaCoordinator(hass, client)
        try:
            await coordinator.async_config_entry_first_refresh()
        except (ArcticSpaApiError, OSError, asyncio.TimeoutError) as err:
            raise ConfigEntryNotReady(str(err)) from err

        coordinator.capabilities = resolve_rest_capabilities(coordinator.data or {})
        _LOGGER.debug("REST capabilities resolved from first status poll")

    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data[entry.entry_id] = coordinator
    # Store client so unload() can call stop() on MQTT/Local clients.
    domain_data[f"{entry.entry_id}_client"] = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        domain_data = hass.data.get(DOMAIN, {})
        domain_data.pop(entry.entry_id, None)
        client = domain_data.pop(f"{entry.entry_id}_client", None)
        if client is not None and hasattr(client, "stop"):
            try:
                await client.stop()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Error stopping client: %s", err)
    return unload_ok
