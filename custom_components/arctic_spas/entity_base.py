"""Shared helpers for Arctic Spa entities."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN
from .spa_data import SpaCapabilities


def device_info(
    entry: ConfigEntry,
    capabilities: SpaCapabilities | None = None,
) -> dict[str, Any]:
    """Return a consistent device info dict for all Arctic Spa entities.

    Pass coordinator.capabilities to populate model and firmware version when
    available (currently populated by MQTT mode via information/spa).
    """
    info: dict[str, Any] = {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": "Arctic Spas",
        "manufacturer": "Arctic Spas",
    }
    if capabilities is not None:
        if capabilities.model:
            info["model"] = capabilities.model
        if capabilities.firmware_lpc:
            info["sw_version"] = capabilities.firmware_lpc
    return info
