"""Button entities for Arctic Spas (stateless trigger actions)."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ArcticSpaApiError
from .const import DOMAIN
from .coordinator import ArcticSpaCoordinator
from .entity_base import device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ArcticSpaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        ArcticSpaBoostButton(coordinator, entry),
        ArcticSpaEasyModeButton(coordinator, entry),
    ])


class ArcticSpaBoostButton(CoordinatorEntity[ArcticSpaCoordinator], ButtonEntity):
    """Button that activates boost mode on the spa."""

    _attr_has_entity_name = True
    _attr_name = "Boost"

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_boost"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.activate_boost()
        except ArcticSpaApiError as err:
            _LOGGER.error("Failed to activate boost: %s", err)
            return
        await self.coordinator.async_request_refresh()


class ArcticSpaEasyModeButton(CoordinatorEntity[ArcticSpaCoordinator], ButtonEntity):
    """Button that activates easy mode on the spa.

    Easy mode triggers pump activity but the API never returns an easymode
    status field, so this is modelled as a stateless button rather than a switch.
    """

    _attr_has_entity_name = True
    _attr_name = "Easy Mode"

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_easymode"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.set_easymode(True)
        except ArcticSpaApiError as err:
            _LOGGER.error("Failed to activate easy mode: %s", err)
            return
        await self.coordinator.async_request_refresh()
