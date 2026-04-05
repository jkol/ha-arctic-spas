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


_CL_PRESETS = {
    "Low":  (545, 555),
    "Mid":  (645, 655),
    "High": (745, 755),
}
_ORP_LOW_TO_LEVEL = {v[0]: k for k, v in _CL_PRESETS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ArcticSpaCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list = [ArcticSpaPump1Select(coordinator, entry)]
    if coordinator.capabilities.spaboy:
        entities.append(ArcticSpaClRangeSelect(coordinator, entry))
    async_add_entities(entities)


class ArcticSpaPump1Select(CoordinatorEntity[ArcticSpaCoordinator], SelectEntity):
    """Select entity controlling Pump 1 speed (off / low / high)."""

    _attr_has_entity_name = True
    _attr_name = "Pump 1"
    _attr_options = PUMP1_STATES

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_pump1"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    @property
    def available(self) -> bool:
        # Spa locks pump1 to high during filter boost — disallow changes
        return (
            super().available
            and self.coordinator.data.get("connected", False)
            and self.coordinator.data.get("filter_status") != "boost"
        )

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
        self.hass.async_create_task(self.coordinator.async_request_refresh_delayed())


class ArcticSpaClRangeSelect(CoordinatorEntity[ArcticSpaCoordinator], SelectEntity):
    """Select entity for SpaBoy chlorine (ORP) target range: Low / Mid / High.

    Reads current preset from spaboy_orp_low (populated by settings/spaboy MQTT topic).
    Commands via set_spaboy_orp().  Presets confirmed from APK CL Range UI.
    """

    _attr_has_entity_name = True
    _attr_name = "CL Range"
    _attr_options = list(_CL_PRESETS)
    _attr_icon = "mdi:test-tube"

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_cl_range"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    @property
    def current_option(self) -> str | None:
        orp_low = self.coordinator.data.get("spaboy_orp_low")
        return _ORP_LOW_TO_LEVEL.get(orp_low)

    async def async_select_option(self, option: str) -> None:
        orp_low, orp_high = _CL_PRESETS[option]
        try:
            await self.coordinator.client.set_spaboy_orp(orp_low, orp_high)
        except ArcticSpaApiError as err:
            _LOGGER.error("Failed to set CL range to %s: %s", option, err)
            return
        self.hass.async_create_task(self.coordinator.async_request_refresh_delayed())
