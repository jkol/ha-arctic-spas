"""Shared helpers for Arctic Spa entities."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN


def device_info(entry: ConfigEntry) -> dict:
    """Return a consistent device info dict for all Arctic Spa entities."""
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": "Arctic Spas",
        "manufacturer": "Arctic Spas",
    }
