"""Number entities for Arctic Spa filter schedule configuration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ArcticSpaClient
from .const import DOMAIN
from .coordinator import ArcticSpaCoordinator


@dataclass(frozen=True, kw_only=True)
class ArcticSpaNumberDescription(NumberEntityDescription):
    filter_kwarg: str  # kwarg name passed to api.set_filter()


NUMBERS: tuple[ArcticSpaNumberDescription, ...] = (
    ArcticSpaNumberDescription(
        key="filter_frequency",
        name="Filter Frequency",
        filter_kwarg="frequency",
        native_min_value=1,
        native_max_value=12,
        native_step=1,
        native_unit_of_measurement="cycles/day",
        mode=NumberMode.BOX,
    ),
    ArcticSpaNumberDescription(
        key="filter_duration",
        name="Filter Duration",
        filter_kwarg="duration",
        native_min_value=1,
        native_max_value=24,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=NumberDeviceClass.DURATION,
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ArcticSpaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [ArcticSpaNumber(coordinator, entry, desc) for desc in NUMBERS]
    )


class ArcticSpaNumber(CoordinatorEntity[ArcticSpaCoordinator], NumberEntity):
    """Numeric control for filter schedule parameters."""

    _attr_has_entity_name = True
    entity_description: ArcticSpaNumberDescription

    def __init__(
        self,
        coordinator: ArcticSpaCoordinator,
        entry: ConfigEntry,
        description: ArcticSpaNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Arctic Spa",
            "manufacturer": "Arctic Spas",
        }

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.data.get(self.entity_description.key)
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.client.set_filter(
            **{self.entity_description.filter_kwarg: int(value)}
        )
        await self.coordinator.async_request_refresh()
