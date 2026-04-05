"""Binary sensor entities for Arctic Spas."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
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
    caps = coordinator.capabilities

    entities: list[BinarySensorEntity] = [
        ArcticSpaConnectedSensor(coordinator, entry),
    ]
    if caps.has_errors:
        entities.append(ArcticSpaErrorSensor(coordinator, entry))
    if caps.has_exhaust:
        entities.append(ArcticSpaExhaustSensor(coordinator, entry))
    if caps.has_economy:
        entities.append(ArcticSpaEconomySensor(coordinator, entry))
    if caps.spaboy:
        entities.append(ArcticSpaOnzenSanitizingSensor(coordinator, entry))
        entities.append(ArcticSpaOnzenPumpSensor(coordinator, entry))
    if caps.has_rfid:
        entities.append(ArcticSpaRfidSensor(coordinator, entry))
    if caps.has_peak_settings:
        entities.append(ArcticSpaPeakModeSensor(coordinator, entry))

    async_add_entities(entities)


class ArcticSpaConnectedSensor(CoordinatorEntity[ArcticSpaCoordinator], BinarySensorEntity):
    """Reports whether the spa is online and reachable."""

    _attr_has_entity_name = True
    _attr_name = "Connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_connected"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.get("connected"))


class ArcticSpaErrorSensor(CoordinatorEntity[ArcticSpaCoordinator], BinarySensorEntity):
    """On when the spa is reporting one or more active error codes."""

    _attr_has_entity_name = True
    _attr_name = "Problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_problem"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    @property
    def is_on(self) -> bool:
        errors = self.coordinator.data.get("errors", [])
        return bool(errors)

    @property
    def extra_state_attributes(self) -> dict:
        errors = self.coordinator.data.get("errors", [])
        return {"error_codes": ", ".join(errors) if errors else "none"}


class ArcticSpaExhaustSensor(CoordinatorEntity[ArcticSpaCoordinator], BinarySensorEntity):
    """On when the spa exhaust fan is running (cooling after heater overshoot)."""

    _attr_has_entity_name = True
    _attr_name = "Exhaust Fan"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_exhaust"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("exhaust"))


class ArcticSpaEconomySensor(CoordinatorEntity[ArcticSpaCoordinator], BinarySensorEntity):
    """On when economy (energy-savings schedule) mode is active."""

    _attr_has_entity_name = True
    _attr_name = "Economy Mode"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_economy"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("economy"))


class ArcticSpaOnzenSanitizingSensor(CoordinatorEntity[ArcticSpaCoordinator], BinarySensorEntity):
    """On when the SpaBoy Onzen sanitizing cycle is actively running."""

    _attr_has_entity_name = True
    _attr_name = "SpaBoy Sanitizing"
    _attr_icon = "mdi:flask"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_onzen_sanitizing"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("onzen_sanitizing"))


class ArcticSpaOnzenPumpSensor(CoordinatorEntity[ArcticSpaCoordinator], BinarySensorEntity):
    """On when the SpaBoy circulation pump is actively running."""

    _attr_has_entity_name = True
    _attr_name = "SpaBoy Pump"
    _attr_icon = "mdi:pump"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_onzen_pump"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("onzen_pump"))


class ArcticSpaRfidSensor(CoordinatorEntity[ArcticSpaCoordinator], BinarySensorEntity):
    """On when the RFID reader is present and communicating with the controller."""

    _attr_has_entity_name = True
    _attr_name = "RFID Reader"
    _attr_icon = "mdi:card-account-details"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_rfid_communicating"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("rfid_communicating"))


class ArcticSpaPeakModeSensor(CoordinatorEntity[ArcticSpaCoordinator], BinarySensorEntity):
    """On when the peak-pricing schedule has at least one active day."""

    _attr_has_entity_name = True
    _attr_name = "Peak Mode"
    _attr_icon = "mdi:clock-time-eight"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ArcticSpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_peak_mode_enabled"
        self._attr_device_info = device_info(entry, coordinator.capabilities)

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("peak_mode_enabled"))

    @property
    def extra_state_attributes(self) -> dict:
        return (self.coordinator.data or {}).get("peak_schedule") or {}
