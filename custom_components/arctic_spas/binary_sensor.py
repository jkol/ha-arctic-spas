"""Binary sensor for Arctic Spa connectivity status."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ArcticSpaCoordinator
from .entity_base import device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ArcticSpaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ArcticSpaConnectedSensor(coordinator, entry)])


class ArcticSpaConnectedSensor(CoordinatorEntity[ArcticSpaCoordinator], BinarySensorEntity):
    """Reports whether the spa is online and reachable."""

    _attr_has_entity_name = True
    _attr_name = "Connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_connected"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.get("connected"))
