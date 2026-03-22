"""Sensor entities for Arctic Spa (read-only status values)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ArcticSpaCoordinator
from .entity_base import device_info


@dataclass(frozen=True, kw_only=True)
class ArcticSpaSensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with optional-feature flag."""

    optional: bool = False  # Only add if field present in status response


SENSORS: tuple[ArcticSpaSensorDescription, ...] = (
    ArcticSpaSensorDescription(
        key="temperatureF",
        name="Water Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
    ),
    ArcticSpaSensorDescription(
        key="setpointF",
        name="Temperature Setpoint",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
    ),
    ArcticSpaSensorDescription(
        key="ph",
        name="Spa pH Level",
        device_class=SensorDeviceClass.PH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="pH",
        icon="mdi:ph",
        optional=True,
    ),
    ArcticSpaSensorDescription(
        key="ph_status",
        name="pH Status",
        device_class=SensorDeviceClass.ENUM,
        optional=True,
    ),
    ArcticSpaSensorDescription(
        key="orp",
        name="Spa Chlorine Level",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="mV",
        icon="mdi:lightning-bolt",
        optional=True,
    ),
    ArcticSpaSensorDescription(
        key="orp_status",
        name="ORP Status",
        device_class=SensorDeviceClass.ENUM,
        optional=True,
    ),
    ArcticSpaSensorDescription(
        key="filter_status",
        name="Filter Status",
        device_class=SensorDeviceClass.ENUM,
    ),
    ArcticSpaSensorDescription(
        key="filtration_duration",
        name="Filter Duration",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.HOURS,
    ),
    ArcticSpaSensorDescription(
        key="filtration_frequency",
        name="Filter Frequency",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="cycles/day",
    ),
    ArcticSpaSensorDescription(
        key="errors",
        name="Errors",
        entity_registry_enabled_default=True,
    ),
)

_OPTIONAL_SENSORS = {desc.key: desc for desc in SENSORS if desc.optional}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ArcticSpaCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}

    # Add non-optional sensors immediately, plus any optional ones already present.
    added_keys: set[str] = set()
    initial = []
    for desc in SENSORS:
        if not desc.optional or desc.key in data:
            initial.append(ArcticSpaSensor(coordinator, entry, desc))
            added_keys.add(desc.key)
    async_add_entities(initial)

    # Watch for optional features that appear in later polls.
    def _check_for_new_optional_sensors() -> None:
        current_data = coordinator.data or {}
        new_entities = []
        for key, desc in _OPTIONAL_SENSORS.items():
            if key not in added_keys and key in current_data:
                new_entities.append(ArcticSpaSensor(coordinator, entry, desc))
                added_keys.add(key)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        coordinator.async_add_listener(_check_for_new_optional_sensors)
    )


class ArcticSpaSensor(CoordinatorEntity[ArcticSpaCoordinator], SensorEntity):
    """A read-only sensor sourced from the spa status response."""

    _attr_has_entity_name = True
    entity_description: ArcticSpaSensorDescription

    def __init__(
        self,
        coordinator: ArcticSpaCoordinator,
        entry: ConfigEntry,
        description: ArcticSpaSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self) -> Any:
        return self.coordinator.data.get(self.entity_description.key)
