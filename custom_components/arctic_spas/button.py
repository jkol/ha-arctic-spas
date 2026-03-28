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
    caps = coordinator.capabilities
    entities: list[ButtonEntity] = [
        ArcticSpaFiltrationBoostButton(coordinator, entry),
        ArcticSpaEasyModeButton(coordinator, entry),
    ]
    if caps.has_spaboy_boost:
        entities.append(ArcticSpaSpaBoyBoostButton(coordinator, entry))
    async_add_entities(entities)


class ArcticSpaFiltrationBoostButton(CoordinatorEntity[ArcticSpaCoordinator], ButtonEntity):
    """Button that activates filtration boost mode on the spa."""

    _attr_has_entity_name = True
    _attr_name = "Filtration Boost"
    _attr_icon = "mdi:rocket-launch"

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_boost"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.activate_boost()
        except ArcticSpaApiError as err:
            _LOGGER.error("Failed to activate filtration boost: %s", err)
            return
        self.hass.async_create_task(self.coordinator.async_request_refresh_delayed())


class ArcticSpaSpaBoyBoostButton(CoordinatorEntity[ArcticSpaCoordinator], ButtonEntity):
    """Button that activates SpaBoy chlorine boost on the spa.

    Only created when caps.has_spaboy_boost is True (local mode + SpaBoy hardware).
    Uses coldfire SpaCommand field 19 (SPABOY_BOOST), packet type 0x0001.
    """

    _attr_has_entity_name = True
    _attr_name = "SpaBoy Boost"
    _attr_icon = "mdi:flask"

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_spaboy_boost"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.activate_spaboy_boost()
        except ArcticSpaApiError as err:
            _LOGGER.error("Failed to activate SpaBoy boost: %s", err)
            return
        self.hass.async_create_task(self.coordinator.async_request_refresh_delayed())


class ArcticSpaEasyModeButton(CoordinatorEntity[ArcticSpaCoordinator], ButtonEntity):
    """Button that activates easy mode on the spa.

    Easy mode triggers pump activity but the API never returns an easymode
    status field, so this is modelled as a stateless button rather than a switch.
    """

    _attr_has_entity_name = True
    _attr_name = "Easy Mode"
    _attr_icon = "mdi:waves-arrow-up"

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_easymode"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.set_easymode(True)
        except ArcticSpaApiError as err:
            _LOGGER.error("Failed to activate easy mode: %s", err)
            return
        self.hass.async_create_task(self.coordinator.async_request_refresh_delayed())
