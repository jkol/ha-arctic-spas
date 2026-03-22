"""Select entity for Pump 1 (off/low/high)."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ArcticSpaApiError
from .const import DOMAIN, PUMP1_STATES
from .coordinator import ArcticSpaCoordinator
from .entity_base import device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ArcticSpaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ArcticSpaPump1Select(coordinator, entry)])


class ArcticSpaPump1Select(CoordinatorEntity[ArcticSpaCoordinator], SelectEntity):
    """Select entity controlling Pump 1 speed (off / low / high)."""

    _attr_has_entity_name = True
    _attr_name = "Pump 1"
    _attr_options = PUMP1_STATES

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_pump1"
        self._attr_device_info = device_info(entry)

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.data.get("pump1")
        if isinstance(value, str) and value.lower() in PUMP1_STATES:
            return value.lower()
        return None

    async def async_select_option(self, option: str) -> None:
        try:
            await self.coordinator.client.set_pump("1", option)
        except ArcticSpaApiError as err:
            _LOGGER.error("Failed to set pump 1 to %s: %s", option, err)
            return
        await self.coordinator.async_request_refresh()
