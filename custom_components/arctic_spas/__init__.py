"""Arctic Spa Home Assistant integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import ArcticSpaApiError
from .const import (
    CONF_LOCAL_HOST,
    CONF_LOCAL_MAC,
    CONF_MODE,
    ConnectionMode,
    DOMAIN,
)
from .coordinator import ArcticSpaCoordinator
from .spa_data import resolve_native_capabilities

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
    """Reject old config entries — v3 requires re-adding the integration."""
    _LOGGER.error(
        "Arctic Spa config entry %s uses an outdated format (version %s). "
        "Please delete and re-add the integration.",
        entry.entry_id,
        entry.version,
    )
    return False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Arctic Spa from a config entry."""
    mode = entry.data.get(CONF_MODE)

    if mode == ConnectionMode.LOCAL:
        from .websocket_client import ArcticSpaWebSocketClient  # noqa: PLC0415

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

        coordinator.capabilities = resolve_native_capabilities(
            client.get_settings(),
        )
        _LOGGER.debug(
            "Local capabilities resolved via WebSocket (LPC %s, YOC %s)",
            coordinator.capabilities.firmware_lpc,
            coordinator.capabilities.firmware_yocto,
        )

    else:
        _LOGGER.error(
            "Unsupported connection mode '%s' — please delete and re-add the integration",
            mode,
        )
        return False

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
