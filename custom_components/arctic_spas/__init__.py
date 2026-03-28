"""Arctic Spa Home Assistant integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArcticSpaClient
from .const import (
    CONF_API_KEY,
    CONF_LOCAL_HOST,
    CONF_MODE,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_USERNAME,
    ConnectionMode,
    DEFAULT_LOCAL_PORT,
    CONF_LOCAL_PORT,
    DOMAIN,
)
from .coordinator import ArcticSpaCoordinator
from .spa_data import resolve_local_capabilities, resolve_mqtt_capabilities, resolve_rest_capabilities

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
            CONF_LOCAL_PORT: DEFAULT_LOCAL_PORT,
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
        except Exception as err:
            raise ConfigEntryNotReady(str(err)) from err

        coordinator.capabilities = resolve_mqtt_capabilities(
            client.get_config(), client.get_info()
        )
        _LOGGER.debug(
            "MQTT capabilities resolved (config/spa received: %s)",
            bool(client.get_config()),
        )

    elif mode == ConnectionMode.LOCAL:
        from .local_api import ArcticSpaLocalClient  # noqa: PLC0415

        host = entry.data[CONF_LOCAL_HOST]
        client = ArcticSpaLocalClient(host)
        coordinator = ArcticSpaCoordinator(hass, client, push_mode=True)

        def _on_local_update(data: dict[str, Any]) -> None:
            coordinator.async_set_updated_data(data)

        try:
            await client.start(_on_local_update)
            await coordinator.async_config_entry_first_refresh()
        except Exception as err:
            raise ConfigEntryNotReady(str(err)) from err

        coordinator.capabilities = resolve_local_capabilities(
            client.get_first_spa_live_fields(),
            has_spaboy=client.has_spaboy,
        )
        _LOGGER.debug("Local capabilities resolved from first spa_live frame")

    else:  # ConnectionMode.REST (default)
        session = async_get_clientsession(hass)
        client = ArcticSpaClient(entry.data[CONF_API_KEY], session)
        coordinator = ArcticSpaCoordinator(hass, client)
        try:
            await coordinator.async_config_entry_first_refresh()
        except Exception as err:
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
