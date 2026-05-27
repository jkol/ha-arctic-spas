"""Shared helpers for Arctic Spa entities."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import CONF_CLOUD_SPA_ID, CONF_LOCAL_HOST, CONF_LOCAL_MAC, DOMAIN
from .spa_data import SpaCapabilities


def device_info(
    entry: ConfigEntry,
    capabilities: SpaCapabilities | None = None,
) -> dict[str, Any]:
    """Return a consistent device info dict for all Arctic Spa entities."""
    stable_id = (
        entry.data.get(CONF_CLOUD_SPA_ID)
        or entry.data.get(CONF_LOCAL_MAC)
        or entry.data.get(CONF_LOCAL_HOST)
        or entry.entry_id
    )
    info: dict[str, Any] = {
        "identifiers": {(DOMAIN, stable_id)},
        "name": entry.title or "Arctic Spa",
        "manufacturer": "Arctic Spas",
        "configuration_url": "https://dealer.myarcticspa.com",
    }
    if capabilities is not None:
        if capabilities.model:
            info["model"] = capabilities.model
        if capabilities.firmware_lpc:
            info["sw_version"] = capabilities.firmware_lpc
    return info
